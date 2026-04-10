#!/usr/bin/env python3
"""Generate TTS audio via ComfyUI's ChatterboxTTS and download the .wav file."""

import argparse
import queue
import shutil
import subprocess
import signal
import sys
import tempfile
import threading
from pathlib import Path

from comfyui_tts import COMFYUI_URL, render_line

_stream_stop = threading.Event()
_current_player_proc = None


def find_player():
    for player in ["pw-play", "paplay", "aplay", "mpv", "ffplay"]:
        if shutil.which(player):
            return player
    return None


def play_audio(path, player=None):
    if not player:
        player = find_player()
    if not player:
        print(f"No audio player found. File saved to: {path}")
        return
    args = [player]
    if player == "ffplay":
        args += ["-nodisp", "-autoexit"]
    args.append(str(path))
    subprocess.run(args)


def play_clip(player, path):
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
    while not _stream_stop.is_set():
        try:
            clip = clip_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if clip is None:
            break
        play_clip(player, clip)
        clip_queue.task_done()


def handle_sigint(sig, frame):
    _stream_stop.set()
    if _current_player_proc:
        _current_player_proc.terminate()
    sys.exit(130)


def main():
    parser = argparse.ArgumentParser(description="ComfyUI ChatterboxTTS client")
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("-v", "--voice", default="despotism-doc.wav", help="Voice reference filename (default: despotism-doc.wav)")
    parser.add_argument("-o", "--output", default=None, help="Output .wav path (default: ~/Desktop/tts_output.wav)")
    parser.add_argument("-s", "--seed", type=int, default=1, help="RNG seed (default: 1)")
    parser.add_argument("-t", "--token-scale", type=float, default=1.0, help="Scale auto-estimated token count (default: 1.0)")
    parser.add_argument("--no-play", action="store_true", help="Don't play audio after download")
    parser.add_argument("--no-stream", action="store_true", help="Wait for all audio before playing")
    parser.add_argument("--url", default=COMFYUI_URL, help=f"ComfyUI URL (default: {COMFYUI_URL})")
    args = parser.parse_args()

    if args.text:
        text = args.text
    else:
        if sys.stdin.isatty():
            print("Enter text (Ctrl+D to finish):")
        text = sys.stdin.read().strip()
        if not text:
            print("No text provided.", file=sys.stderr)
            sys.exit(1)

    output_path = Path(args.output) if args.output else Path.home() / "Desktop" / "tts_output.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Split into paragraphs for streaming
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    streaming = not args.no_play and not args.no_stream and len(paragraphs) > 1

    signal.signal(signal.SIGINT, handle_sigint)

    if streaming:
        player = find_player()
        if not player:
            print("No audio player found, disabling stream", file=sys.stderr)
            streaming = False

    if streaming:
        clip_q = queue.Queue()
        player_thread = threading.Thread(target=stream_player, args=(clip_q, player), daemon=True)
        player_thread.start()

        tmpdir = Path(tempfile.mkdtemp(prefix="tts-stream-"))
        all_parts = []

        for pi, para in enumerate(paragraphs):
            if _stream_stop.is_set():
                break
            part_path = tmpdir / f"para_{pi:04d}.wav"
            print(f"[{pi+1}/{len(paragraphs)}] {para[:60]}{'...' if len(para) > 60 else ''}")

            def on_chunk(path, _pi=pi, _part=part_path):
                # Copy chunk so it persists after render_line cleanup
                safe = tmpdir / f"chunk_{_pi:04d}_{Path(path).name}"
                shutil.copy2(path, safe)
                clip_q.put(str(safe))

            render_line(args.url, para, args.voice, seed=args.seed + pi,
                        dest_path=str(part_path), token_scale=args.token_scale,
                        on_chunk=on_chunk)
            all_parts.append(str(part_path))

        # Signal end of stream and wait for playback
        clip_q.put(None)
        player_thread.join()

        # Concatenate all parts into final output
        if all_parts and not _stream_stop.is_set():
            list_file = tmpdir / "concat.txt"
            list_file.write_text("".join(f"file '{p}'\n" for p in all_parts))
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                 "-c:a", "pcm_s16le", str(output_path)],
                capture_output=True,
            )
            print(f"\nSaved: {output_path}")

        shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        print(f"Queuing TTS: {text[:80]}{'...' if len(text) > 80 else ''}")
        render_line(args.url, text, args.voice, seed=args.seed, dest_path=output_path, token_scale=args.token_scale)
        print(f"\nSaved: {output_path}")

        if not args.no_play:
            play_audio(output_path)


if __name__ == "__main__":
    main()
