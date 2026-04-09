#!/usr/bin/env python3
"""Generate TTS audio via ComfyUI's ChatterboxTTS and download the .wav file."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from comfyui_tts import COMFYUI_URL, render_line


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


def main():
    parser = argparse.ArgumentParser(description="ComfyUI ChatterboxTTS client")
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("-v", "--voice", default="despotism-doc.wav", help="Voice reference filename (default: despotism-doc.wav)")
    parser.add_argument("-o", "--output", default=None, help="Output .wav path (default: ~/Desktop/tts_output.wav)")
    parser.add_argument("-s", "--seed", type=int, default=None, help="RNG seed (default: random)")
    parser.add_argument("-t", "--token-scale", type=float, default=1.0, help="Scale auto-estimated token count (default: 1.0)")
    parser.add_argument("--no-play", action="store_true", help="Don't play audio after download")
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

    print(f"Queuing TTS: {text[:80]}{'...' if len(text) > 80 else ''}")
    render_line(args.url, text, args.voice, seed=args.seed, dest_path=output_path, token_scale=args.token_scale)
    print(f"\nSaved: {output_path}")

    if not args.no_play:
        play_audio(output_path)


if __name__ == "__main__":
    main()
