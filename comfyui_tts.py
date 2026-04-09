"""Shared ComfyUI ChatterboxTTS client library."""

import hashlib
import json
import os
import random
import time
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

WORKFLOW = {
    "4": {
        "class_type": "LoadAudio",
        "inputs": {
            "audio": "despotism-doc.wav",
        },
    },
    "28": {
        "class_type": "ChatterboxTTS",
        "inputs": {
            "audio_prompt": ["4", 0],
            "model_pack_name": "resembleai_default_voice",
            "text": "",
            "audio_prompt_length": 1000,
            "cfg_weight": 0.4,
            "exaggeration": 0.35,
            "temperature": 0.5,
            "repetition_penalty": 1.2,
            "top_p": 0.95,
            "min_p": 0.05,
            "max_new_tokens": 4000,
            "flow_cfg_scale": 1.0,
            "chunk_overlap": 0.1,
            "chunks": 1,
            "seed": 0,
            "control_after_generate": "randomize",
            "remove_silence": False,
            "use_watermark": False,
        },
    },
    "7": {
        "class_type": "SaveAudio",
        "inputs": {
            "audio": ["28", 0],
            "filename_prefix": "audio/ComfyUI",
        },
    },
}


def post_json(url, payload):
    """POST JSON, following redirects while preserving the body."""
    data = json.dumps(payload).encode()
    for _ in range(5):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 307, 308):
                url = e.headers["Location"]
            else:
                body = e.read().decode(errors="replace")
                raise RuntimeError(f"HTTP {e.code} {e.reason}: {body}") from e
    raise RuntimeError(f"Too many redirects for {url}")


def queue_prompt(base_url, prompt, client_id):
    return post_json(
        f"{base_url}/api/prompt",
        {"prompt": prompt, "client_id": client_id},
    )


def get_history(base_url, prompt_id):
    with urllib.request.urlopen(f"{base_url}/api/history/{prompt_id}") as resp:
        return json.loads(resp.read())


def poll_until_done(base_url, prompt_id, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        history = get_history(base_url, prompt_id)
        if prompt_id in history:
            status = history[prompt_id].get("status", {})
            if status.get("completed", False) or status.get("status_str") == "success":
                return history[prompt_id]
            if status.get("status_str") == "error":
                msgs = history[prompt_id].get("status", {}).get("messages", [])
                raise RuntimeError(f"Workflow failed: {msgs}")
        time.sleep(1)
        sys.stdout.write(".")
        sys.stdout.flush()
    raise TimeoutError(f"Workflow did not complete within {timeout}s")


def download_audio(base_url, output_info, dest_path):
    """Find the audio output from SaveAudio node and download it."""
    outputs = output_info.get("outputs", {})
    for node_id, node_out in outputs.items():
        if "audio" in node_out:
            for audio in node_out["audio"]:
                filename = audio["filename"]
                subfolder = audio.get("subfolder", "")
                audio_type = audio.get("type", "output")
                params = urllib.parse.urlencode(
                    {"filename": filename, "subfolder": subfolder, "type": audio_type}
                )
                url = f"{base_url}/api/view?{params}"
                urllib.request.urlretrieve(url, dest_path)
                return dest_path
    raise RuntimeError("No audio output found in workflow results")


def normalize_voice(voice):
    """Ensure voice filename has .wav extension."""
    if not voice.endswith(".wav"):
        voice += ".wav"
    return voice


def estimate_tokens(text):
    """Estimate max_new_tokens based on text length.

    Roughly 25 tokens ≈ 1 second of audio, and speech is ~150 words/min
    (~2.5 words/sec). So per word we need ~10 tokens. We add headroom
    and clamp to the server's 16–4000 range.
    """
    words = len(text.split())
    tokens = max(100, words * 12)
    return min(tokens, 4000)


def split_sentences(text, max_words=25):
    """Split text into chunks for rendering.

    Splits at sentence boundaries, then groups small sentences together
    so each chunk is roughly max_words words.
    """
    import re
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    parts = [p for p in parts if p.strip()]

    if not parts:
        return [text.strip()]

    chunks = []
    current = []
    current_words = 0
    for sentence in parts:
        words = len(sentence.split())
        if current and current_words + words > max_words:
            chunks.append(" ".join(current))
            current = [sentence]
            current_words = words
        else:
            current.append(sentence)
            current_words += words
    if current:
        chunks.append(" ".join(current))

    return chunks



# Params that can be overridden per-voice or per-line in scripts
OVERRIDABLE_PARAMS = {
    "temp", "flow_cfg_scale", "exaggeration", "cfg_weight",
    "repetition_penalty", "min_p", "top_p", "seed", "token_scale",
    "chunk_words", "lpf", "amp",
}


def build_prompt(text, voice, seed, token_scale=1.0, overrides=None):
    """Build a ComfyUI prompt dict without submitting it."""
    voice = normalize_voice(voice)
    prompt = json.loads(json.dumps(WORKFLOW))
    prompt["4"]["inputs"]["audio"] = voice
    prompt["28"]["inputs"]["text"] = text
    prompt["28"]["inputs"]["seed"] = seed
    prompt["28"]["inputs"]["max_new_tokens"] = min(4000, int(estimate_tokens(text) * token_scale))

    if overrides:
        for k, v in overrides.items():
            if k == "temp":
                prompt["28"]["inputs"]["temperature"] = v
            elif k in OVERRIDABLE_PARAMS and k not in ("token_scale", "chunk_words", "lpf", "amp"):
                prompt["28"]["inputs"][k] = v

    return prompt


def prompt_hash(prompt, lpf=None, amp=None):
    """Compute a deterministic hash of a prompt for caching."""
    # Extract the inputs that affect audio output
    key = {
        "voice": prompt["4"]["inputs"]["audio"],
        "tts": prompt["28"]["inputs"],
    }
    if lpf:
        key["lpf"] = lpf
    if amp and amp != 1:
        key["amp"] = amp
    canonical = json.dumps(key, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


PARAM_RANGES = {
    "temperature": (0.0, 2.0),
    "cfg_weight": (0.0, 1.0),
    "exaggeration": (0.0, 1.0),
    "flow_cfg_scale": (0.0, 5.0),
    "repetition_penalty": (1.0, 2.0),
    "top_p": (0.0, 1.0),
    "min_p": (0.0, 1.0),
    "max_new_tokens": (16, 4000),
}


def validate_prompt(base_url, prompt, label=""):
    """Validate a prompt locally and check voice file on server. Returns list of errors."""
    errors = []
    inputs = prompt["28"]["inputs"]
    for param, (lo, hi) in PARAM_RANGES.items():
        if param in inputs:
            val = inputs[param]
            if val < lo or val > hi:
                errors.append(f"{label}{param}={val} out of range [{lo}, {hi}]")

    # Check voice file exists on server
    voice = prompt["4"]["inputs"]["audio"]
    params = urllib.parse.urlencode({"filename": voice, "subfolder": "", "type": "input"})
    try:
        req = urllib.request.Request(f"{base_url}/api/view?{params}", method="HEAD")
        with urllib.request.urlopen(req):
            pass
    except Exception:
        errors.append(f"{label}voice file not found on server: {voice}")

    return errors


def _render_single(base_url, text, voice, seed, dest_path, token_scale=1.0, overrides=None):
    """Render a single short text to a .wav file."""
    prompt = build_prompt(text, voice, seed, token_scale=token_scale, overrides=overrides)

    client_id = str(uuid.uuid4())
    result = queue_prompt(base_url, prompt, client_id)
    prompt_id = result["prompt_id"]

    history = poll_until_done(base_url, prompt_id)
    download_audio(base_url, history, dest_path)
    return dest_path


def render_line(base_url, text, voice, seed=1, dest_path=None, token_scale=1.0, overrides=None, on_chunk=None):
    """Render text to a .wav file, splitting long text into sentences.

    on_chunk: optional callback(path) called after each chunk is rendered,
    for streaming playback.
    """
    # Allow seed override from overrides
    if overrides and "seed" in overrides:
        seed = overrides["seed"]

    chunk_words = (overrides or {}).get("chunk_words", 25)
    sentences = split_sentences(text, max_words=int(chunk_words))

    # Short text: render directly
    if len(sentences) <= 1:
        _render_single(base_url, sentences[0] if sentences else text, voice, seed, dest_path, token_scale=token_scale, overrides=overrides)
        if on_chunk:
            on_chunk(dest_path)
        return dest_path

    # Long text: render each sentence, then concatenate with ffmpeg
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="tts-sentences-")
    parts = []
    for i, sentence in enumerate(sentences):
        part_path = os.path.join(tmpdir, f"{i:04d}.wav")
        sys.stdout.write(f"[{i+1}/{len(sentences)}]")
        sys.stdout.flush()
        _render_single(base_url, sentence, voice, seed + i, part_path, token_scale=token_scale, overrides=overrides)
        parts.append(part_path)
        if on_chunk:
            on_chunk(part_path)

    # Concatenate with ffmpeg
    list_file = os.path.join(tmpdir, "concat.txt")
    with open(list_file, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(dest_path)],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode(errors='replace')}")

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    return dest_path
