# tts-demo Justfile

# List available recipes
default:
    @just --list

# Render dialog script(s) without playing
render *ARGS:
    python3 dialog.py --no-play {{ARGS}}

# Render and play dialog script(s)
play *ARGS:
    python3 dialog.py {{ARGS}}

# Stream playback while rendering (starts playing after buffer fills)
stream *ARGS:
    python3 dialog.py --stream 2 {{ARGS}}

# Speak a single line of text
say *ARGS:
    python3 tts.py {{ARGS}}

# Sync local voices to ComfyUI server
sync *ARGS:
    python3 sync-voices.py {{ARGS}}

# Compress all wav files in output/ to opus
compress:
    #!/usr/bin/env bash
    shopt -s nullglob
    files=(output/*.wav)
    if [ ${#files[@]} -eq 0 ]; then
        echo "No wav files in output/"
        exit 0
    fi
    for f in "${files[@]}"; do
        out="${f%.wav}.opus"
        echo "  ${f} → ${out}"
        ffmpeg -y -i "$f" -c:a libopus -b:a 64k "$out" 2>/dev/null
        rm "$f"
    done
    echo "Done: ${#files[@]} file(s) compressed"

# Clean output and cache
[confirm("Remove output/ and cache/ directories?")]
clean:
    rm -rf cache/ output/ __pycache__/
    @echo "Cleaned"
