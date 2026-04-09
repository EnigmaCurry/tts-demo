#!/usr/bin/env python3
"""Dialog engine: render a play script with multiple voices and splice audio."""

import argparse
import array
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from comfyui_tts import COMFYUI_URL, OVERRIDABLE_PARAMS, normalize_voice, render_line, build_prompt, validate_prompt, prompt_hash


def apply_lpf(path, cutoff_hz):
    """Apply a low-pass filter to a WAV file using ffmpeg."""
    tmp = str(path) + ".lpf.wav"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-af", f"lowpass=f={cutoff_hz}", tmp],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg lpf failed: {result.stderr.decode(errors='replace')}")
    Path(tmp).replace(path)


def apply_audio_fx(path, amp=None, comp=None):
    """Apply compression and amplification in a single ffmpeg pass.

    Order: compress → amplify → limiter (prevents clipping).
    """
    filters = []
    if comp is not None:
        filters.append(f"acompressor=threshold={comp}dB:ratio=4:attack=5:release=50")
    if amp is not None and float(amp) != 1.0:
        filters.append(f"volume={amp}")
    # Limiter to prevent clipping
    filters.append("alimiter=limit=0.95:level=false")

    if not filters:
        return

    tmp = str(path) + ".fx.wav"
    af = ",".join(filters)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-af", af, tmp],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fx failed: {result.stderr.decode(errors='replace')}")
    Path(tmp).replace(path)


def ensure_pcm_wav(path, sample_rate=None, channels=1):
    """Convert any audio file to 16-bit PCM WAV using ffmpeg."""
    tmp = str(path) + ".converting.wav"
    cmd = ["ffmpeg", "-y", "-i", str(path), "-acodec", "pcm_s16le", "-ac", str(channels)]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    cmd.append(tmp)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')}")
    Path(tmp).replace(path)


def parse_script(text):
    """Parse a play script into voice mappings and dialog lines.

    Format:
        [voices]
        NARRATOR = narrator-voice.wav
        ALICE = alice-voice.wav
        BOB = bob-voice.wav

        [dialog]
        NARRATOR: Once upon a time, there were two friends.
        ALICE: Hello Bob! How are you today?
        BOB: I'm doing great, thanks for asking.

    Lines starting with # are comments. Blank lines are ignored.
    Multi-line dialog: continuation lines that don't match ROLE: are
    appended to the previous line.

    Voice settings: key=value pairs after the wav filename.
        WALT = walt-danger.wav temp=0.2

    Per-line overrides: key=value pairs at the start of the line text.
        WALT: [temp=0.3] I am the one who knocks.
    """
    voices = {}
    lines = []
    section = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.lower() == "[voices]":
            section = "voices"
            continue
        elif stripped.lower() == "[dialog]":
            section = "dialog"
            continue

        if section == "voices":
            if "=" in stripped:
                role, rest = stripped.split("=", 1)
                parts = rest.strip().split()
                voice_file = normalize_voice(parts[0])
                params = {}
                for p in parts[1:]:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        params[k] = _parse_param(v)
                voices[role.strip().upper()] = {"voice": voice_file, **params}
        elif section == "dialog":
            if ":" in stripped:
                maybe_role, rest = stripped.split(":", 1)
                if maybe_role.strip().upper() in voices:
                    rest = rest.strip()
                    line_params = {}
                    if rest.startswith("[") and "]" in rest:
                        bracket, rest = rest[1:].split("]", 1)
                        rest = rest.strip()
                        for p in bracket.split():
                            if "=" in p:
                                k, v = p.split("=", 1)
                                line_params[k] = _parse_param(v)
                    lines.append((maybe_role.strip().upper(), rest, line_params))
                    continue
            # Continuation of previous line
            if lines:
                role, prev_text, params = lines[-1]
                lines[-1] = (role, prev_text + " " + stripped, params)

    return voices, lines


def _parse_param(value):
    """Parse a parameter value string to the appropriate type."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    return value


def read_wav(path):
    """Read a WAV file, return (params, samples_as_array)."""
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth == 2:
        samples = array.array("h", frames)
    elif params.sampwidth == 4:
        samples = array.array("i", frames)
    else:
        samples = array.array("b", frames)
    return params, samples


def write_wav(path, params, samples):
    """Write samples to a WAV file."""
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())


def make_silence(params, duration_sec):
    """Create silent samples for the given duration."""
    n = int(params.framerate * params.nchannels * duration_sec)
    if params.sampwidth == 2:
        return array.array("h", [0] * n)
    elif params.sampwidth == 4:
        return array.array("i", [0] * n)
    return array.array("b", [0] * n)


def _silence_threshold(samples, fraction=0.05):
    """Compute silence threshold as a fraction of the clip's own peak."""
    if not samples:
        return 0
    peak = max(abs(s) for s in samples)
    return int(peak * fraction)


def trim_silence(samples, params, min_silence_sec=0.5):
    """Trim silence longer than min_silence_sec down to min_silence_sec.

    Scans the audio for runs of samples below a threshold (relative to
    the clip's own peak) and shortens them.
    """
    abs_threshold = _silence_threshold(samples)
    min_silence_samples = int(params.framerate * params.nchannels * min_silence_sec)

    result = array.array(samples.typecode)
    silence_run = 0

    for s in samples:
        if abs(s) <= abs_threshold:
            silence_run += 1
            if silence_run <= min_silence_samples:
                result.append(s)
        else:
            silence_run = 0
            result.append(s)

    return result


def trim_edges(samples, params):
    """Trim leading and trailing silence from samples."""
    abs_threshold = _silence_threshold(samples)

    # Find first non-silent sample
    start = 0
    for i, s in enumerate(samples):
        if abs(s) > abs_threshold:
            start = i
            break

    # Find last non-silent sample
    end = len(samples)
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) > abs_threshold:
            end = i + 1
            break

    return samples[start:end]


def clip_duration(samples, params):
    """Return duration in seconds."""
    return len(samples) / (params.framerate * params.nchannels)


def clip_peak(samples, params):
    """Return peak amplitude as a fraction of max."""
    if params.sampwidth == 2:
        max_val = 32767
    elif params.sampwidth == 4:
        max_val = 2147483647
    else:
        max_val = 127
    if not samples:
        return 0.0
    return max(abs(s) for s in samples) / max_val


def normalize(samples, params, target_peak=0.9):
    """Normalize samples so the peak hits target_peak of max."""
    peak = clip_peak(samples, params)
    if peak < 0.001:
        return samples
    scale = target_peak / peak
    return array.array(samples.typecode, [max(-32768, min(32767, int(s * scale))) for s in samples])


def splice_audio(wav_files, params, pause_between=0.4, max_silence=0):
    """Splice WAV files together with pause trimming."""
    pause_samples = make_silence(params, pause_between)
    combined = array.array(pause_samples.typecode)

    for i, path in enumerate(wav_files):
        _, samples = read_wav(path)
        raw_dur = clip_duration(samples, params)
        peak = clip_peak(samples, params)

        # Trim silence (0 = disabled)
        if max_silence > 0:
            samples = trim_silence(samples, params, min_silence_sec=max_silence)
            samples = trim_edges(samples, params)

        final_dur = clip_duration(samples, params)
        final_peak = clip_peak(samples, params)
        print(f"    clip {i}: {raw_dur:.1f}s → {final_dur:.1f}s (raw_peak={peak:.3f} final_peak={final_peak:.3f})")

        combined.extend(samples)
        if i < len(wav_files) - 1:
            combined.extend(pause_samples)

    return combined


def play_audio(path):
    for player in ["pw-play", "paplay", "aplay", "mpv", "ffplay"]:
        if shutil.which(player):
            args = [player]
            if player == "ffplay":
                args += ["-nodisp", "-autoexit"]
            args.append(str(path))
            print(f"Playing with {player}...")
            subprocess.run(args)
            return
    print(f"No audio player found. File saved to: {path}")


def find_player():
    """Find an available audio player."""
    for player in ["pw-play", "paplay", "aplay", "mpv", "ffplay"]:
        if shutil.which(player):
            return player
    return None


_current_player_proc = None
_stream_stop = threading.Event()


def play_clip(player, path):
    """Play a single clip with the given player."""
    global _current_player_proc
    if _stream_stop.is_set():
        return
    args = [player]
    if player == "ffplay":
        args += ["-nodisp", "-autoexit"]
    args.append(str(path))
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _current_player_proc = proc
    proc.wait()
    _current_player_proc = None


def stream_player(clip_queue, player):
    """Background thread that plays clips from a queue."""
    while not _stream_stop.is_set():
        try:
            clip = clip_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if clip is None:
            break
        play_clip(player, clip)
        clip_queue.task_done()


def main():
    parser = argparse.ArgumentParser(
        description="Render a play script with multiple TTS voices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Script format:
    [voices]
    NARRATOR = narrator-voice.wav
    ALICE = alice-voice.wav temp=0.2
    BOB = bob-voice.wav temp=0.05

    [dialog]
    NARRATOR: Once upon a time, there were two friends.
    ALICE: Hello Bob! How are you?
    BOB: [temp=0.3] I'm great, thanks!
""",
    )
    parser.add_argument("script", nargs="+", help="Path(s) to play script file(s)")
    parser.add_argument("-o", "--output", default=None, help="Output .wav path (default: ~/Desktop/dialog_output.wav)")
    parser.add_argument("-r", "--range", default=None, help="Render only a subset of lines (e.g. 3, 3-7, 3-)")
    parser.add_argument("-s", "--seed", type=int, default=1, help="Default seed for deterministic output (default: 1)")
    parser.add_argument("-t", "--token-scale", type=float, default=1.0, help="Scale auto-estimated token count (default: 1.0)")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache, re-render everything")
    parser.add_argument("--pause", type=float, default=0.4, help="Pause between lines in seconds (default: 0.4)")
    parser.add_argument("--max-silence", type=float, default=0, help="Max internal silence in seconds (default: 0, disabled)")
    parser.add_argument("--no-play", action="store_true", help="Don't play audio after rendering")
    parser.add_argument("--stream", type=int, default=None, metavar="N",
                        help="Stream playback while rendering, buffer N clips ahead (default: 2 if flag used)")
    parser.add_argument("--url", default=COMFYUI_URL, help=f"ComfyUI URL (default: {COMFYUI_URL})")
    args = parser.parse_args()

    voices = {}
    lines = []
    for script_file in args.script:
        script_path = Path(script_file)
        if not script_path.exists():
            print(f"Script not found: {script_path}", file=sys.stderr)
            sys.exit(1)
        if script_path.suffix == ".py":
            print(f"Skipping non-script file: {script_path}", file=sys.stderr)
            continue
        v, l = parse_script(script_path.read_text())
        voices.update(v)
        lines.extend(l)
        print(f"Loaded: {script_path.name} ({len(l)} lines, {len(v)} voices)")

    if not voices:
        print("No voices defined. Add a [voices] section.", file=sys.stderr)
        sys.exit(1)
    if not lines:
        print("No dialog lines found. Add a [dialog] section.", file=sys.stderr)
        sys.exit(1)

    # Apply range filter
    total = len(lines)
    if args.range:
        r = args.range
        if "-" in r:
            parts = r.split("-", 1)
            start = int(parts[0]) - 1 if parts[0] else 0
            end = int(parts[1]) if parts[1] else total
        else:
            start = int(r) - 1
            end = int(r)
        lines = lines[start:end]

    print(f"Voices: {', '.join(f'{r} → {v['voice']}' for r, v in voices.items())}")
    if args.range:
        print(f"Lines: {len(lines)} (of {total}, range {args.range})")
    else:
        print(f"Lines: {len(lines)}")
    print()

    output_dir = Path(__file__).parent / "output"
    if args.output:
        output_path = Path(args.output)
    else:
        # Name output after the first script file
        output_path = output_dir / Path(args.script[0]).with_suffix(".wav").name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Preflight: validate all lines before rendering
    print("Preflight check...")
    all_errors = []
    seen_voices = set()
    for i, (role, text, line_params) in enumerate(lines):
        voice_cfg = voices[role]
        voice = voice_cfg["voice"]
        overrides = {}
        for k in OVERRIDABLE_PARAMS:
            if k in line_params:
                overrides[k] = line_params[k]
            elif k in voice_cfg:
                overrides[k] = voice_cfg[k]
        token_scale = overrides.pop("token_scale", args.token_scale)
        overrides.pop("lpf", None)
        overrides.pop("amp", None)
        overrides.pop("comp", None)
        overrides.pop("chunk_words", None)
        seed = overrides.pop("seed", voice_cfg.get("seed", args.seed))
        prompt = build_prompt(text, voice, seed, token_scale=token_scale, overrides=overrides or None)
        label = f"  line {i+1} ({role}): "
        # Only check voice file once per voice
        if voice not in seen_voices:
            errors = validate_prompt(args.url, prompt, label=label)
            seen_voices.add(voice)
        else:
            errors = validate_prompt(args.url, prompt, label=label)
            # Filter out voice-file errors we already checked
            errors = [e for e in errors if "voice file" not in e]
        all_errors.extend(errors)
    if all_errors:
        print("Preflight failed:")
        for e in all_errors:
            print(f"  {e}")
        sys.exit(1)
    print("Preflight OK\n")

    cache_dir = Path(__file__).parent / "cache"
    if not args.no_cache:
        cache_dir.mkdir(exist_ok=True)

    # Set up streaming playback if requested
    streaming = args.stream is not None
    clip_queue = None
    player_thread = None
    if streaming:
        player = find_player()
        if not player:
            print("No audio player found, disabling stream", file=sys.stderr)
            streaming = False
        else:
            clip_queue = queue.Queue()
            player_thread = threading.Thread(target=stream_player, args=(clip_queue, player), daemon=True)
            player_thread.start()
            buffer_size = args.stream
            print(f"Streaming: buffering {buffer_size} clip(s) ahead, playing with {player}")

    with tempfile.TemporaryDirectory(prefix="tts-dialog-") as tmpdir:
        tmpdir = Path(tmpdir)
        wav_files = []
        ref_params = None

        # Streaming state: buffer chunks across all lines
        stream_ready = []
        stream_started = False
        buffer_size = args.stream if streaming else 0

        stream_chunk_dir = tmpdir / "stream_chunks"
        stream_chunk_dir.mkdir()
        stream_chunk_idx = [0]

        def on_chunk(chunk_path):
            """Called per-chunk during rendering for streaming playback."""
            nonlocal stream_started
            if not streaming:
                return
            # Copy chunk to persistent location (render_line cleans its tmpdir)
            safe_copy = stream_chunk_dir / f"chunk_{stream_chunk_idx[0]:04d}.wav"
            stream_chunk_idx[0] += 1
            shutil.copy2(chunk_path, safe_copy)
            ensure_pcm_wav(safe_copy)
            stream_ready.append(str(safe_copy))
            # Once buffer is full, start draining
            if len(stream_ready) > buffer_size:
                stream_started = True
            # Drain all ready clips beyond the buffer
            while stream_started and len(stream_ready) > 0:
                clip_queue.put(stream_ready.pop(0))

        for i, (role, text, line_params) in enumerate(lines):
            voice_cfg = voices[role]
            voice = voice_cfg["voice"]
            # Merge: voice defaults < line overrides
            overrides = {}
            for k in OVERRIDABLE_PARAMS:
                if k in line_params:
                    overrides[k] = line_params[k]
                elif k in voice_cfg:
                    overrides[k] = voice_cfg[k]
            token_scale = overrides.pop("token_scale", args.token_scale)
            lpf = overrides.pop("lpf", None)
            amp = overrides.pop("amp", None)
            comp = overrides.pop("comp", None)
            # Seed priority: line override > voice default > CLI default
            seed = overrides.pop("seed", voice_cfg.get("seed", args.seed))

            # Build prompt to compute cache key
            prompt = build_prompt(text, voice, seed, token_scale=token_scale, overrides=overrides or None)
            cache_key = prompt_hash(prompt, lpf=lpf, amp=amp, comp=comp)
            cached_file = cache_dir / f"{cache_key}.wav"
            dest = tmpdir / f"{i:04d}_{role}.wav"

            print(f"  [{i+1}/{len(lines)}] {role}: {text[:60]}{'...' if len(text) > 60 else ''}")

            if not args.no_cache and cached_file.exists():
                shutil.copy2(cached_file, dest)
                print(f"    cached ({cache_key})")
                if streaming:
                    on_chunk(str(dest))
            else:
                sys.stdout.write("    rendering")
                sys.stdout.flush()
                render_line(args.url, text, voice, seed=seed, dest_path=dest, token_scale=token_scale,
                            overrides=overrides or None, on_chunk=on_chunk if streaming else None)
                # Convert to PCM WAV preserving native sample rate
                ensure_pcm_wav(dest)
                if lpf:
                    apply_lpf(dest, int(lpf))
                if (amp is not None and float(amp) != 1.0) or comp is not None:
                    apply_audio_fx(dest, amp=amp, comp=comp)
                    fx = []
                    if comp is not None:
                        fx.append(f"comp={comp}dB")
                    if amp is not None and float(amp) != 1.0:
                        fx.append(f"amp={amp}")
                    print(f" [{' '.join(fx)}]", end="")
                print(f" done ({cache_key})")
                # Store in cache
                if not args.no_cache:
                    shutil.copy2(dest, cached_file)

            if ref_params is None:
                ref_params, _ = read_wav(dest)
                print(f"  Audio format: {ref_params.framerate}Hz, {ref_params.nchannels}ch, {ref_params.sampwidth*8}bit")

            wav_files.append(dest)

        # Flush remaining buffered chunks to player
        if streaming:
            for clip in stream_ready:
                clip_queue.put(clip)

        # Second pass: ensure all clips match reference sample rate
        for wav in wav_files:
            p, _ = read_wav(wav)
            if p.framerate != ref_params.framerate or p.nchannels != ref_params.nchannels:
                ensure_pcm_wav(wav, sample_rate=ref_params.framerate, channels=ref_params.nchannels)

        print(f"\nSplicing {len(wav_files)} clips...")
        combined = splice_audio(
            wav_files, ref_params,
            pause_between=args.pause,
            max_silence=args.max_silence,
        )
        write_wav(output_path, ref_params, combined)
        print(f"Saved: {output_path}")

        # Wait for streaming playback to finish before tmpdir cleanup
        if streaming and player_thread:
            clip_queue.put(None)
            player_thread.join()

    if not args.no_play and not streaming:
        play_audio(output_path)


def _cleanup_on_exit(*_):
    """Kill player process and stop stream thread on Ctrl+C."""
    _stream_stop.set()
    if _current_player_proc:
        _current_player_proc.kill()
    sys.exit(1)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, _cleanup_on_exit)
    main()
