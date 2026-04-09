# tts-demo

Multi-voice text-to-speech dialog engine using
[ChatterboxTTS](https://github.com/resemble-ai/chatterbox) via
[ComfyUI](https://github.com/comfyanonymous/ComfyUI).

## Prerequisites

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with
  [ComfyUI-ChatterboxTTS](https://github.com/DreamWaltz-AI/ComfyUI-ChatterboxTTS)
- Python 3 (no third-party packages)
- ffmpeg
- [just](https://github.com/casey/just)

### Installing ChatterboxTTS in ComfyUI

1. Open the ComfyUI web interface
2. Click the **Manager** button (install
   [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager) first
   if you don't have it)
3. Click **Custom Nodes Manager**
4. Search for `ChatterboxTTS`
5. Click **Install** on **ComfyUI-ChatterboxTTS**
6. Restart ComfyUI when prompted

The model weights download automatically on first use.

Create a `.env` file with your ComfyUI server URL:

```bash
echo "COMFYUI_URL=http://comfyui.example.com" > .env
```

## Quick start

```bash
just sync                              # upload voices to server
just render scripts/science-debate.txt # render to output/science-debate.wav
just play scripts/science-debate.txt   # render and play
just compress                          # encode output/*.wav to opus
```

## Voices

Place reference voice samples in the `voices/` directory as `.wav` files.
Each sample should be 20–60 seconds of clear speech from the target voice
(no music, minimal background noise). Longer samples give better voice
cloning quality. The filename becomes the voice name in scripts
(e.g. `voices/feynman.wav` → `FEYNMAN = feynman`).

Sync them to the ComfyUI server:

```bash
just sync              # upload new voices (skips existing)
just sync -- --force   # re-upload all
just sync -- --dry-run # preview
```

## Dialog scripts

Scripts live in `scripts/` and use this format:

```
[voices]
NARRATOR = despotism-doc
WALT = walt-danger temp=0.2 lpf=2500
FOX = fox-mulder chunk_words=15 exaggeration=0.3

[dialog]
NARRATOR: Two colleagues meet in the hallway.
WALT: Chemistry is the study of matter.
FOX: [temp=0.05 seed=42] The truth is out there.
```

The `.wav` extension is optional on voice filenames. Long lines are
automatically split at sentence boundaries and rendered as separate TTS
calls, then concatenated.

### Voice and line parameters

Set defaults per-voice in `[voices]`, override per-line with `[key=value]`:

| Param | Default | Description |
|-------|---------|-------------|
| `temp` | 0.5 | Temperature |
| `exaggeration` | 0.35 | Voice exaggeration |
| `cfg_weight` | 0.4 | CFG weight |
| `flow_cfg_scale` | 1.0 | Flow CFG scale |
| `repetition_penalty` | 1.2 | Repetition penalty (min 1.0) |
| `top_p` | 0.95 | Nucleus sampling |
| `min_p` | 0.05 | Minimum probability |
| `seed` | 1 | RNG seed (deterministic output) |
| `token_scale` | 1.0 | Scale auto-estimated max_new_tokens |
| `chunk_words` | 25 | Max words per TTS chunk |
| `lpf` | - | Low-pass filter cutoff in Hz |

### Rendering

```bash
# Render without playing
just render scripts/my-script.txt

# Render and play
just play scripts/my-script.txt

# Render a subset of lines
just play scripts/my-script.txt -- -r 3-7

# Render with a different global seed
just render scripts/my-script.txt -- -s 42

# Render multiple scripts together
just play scripts/part1.txt scripts/part2.txt

# Skip cache
just render scripts/my-script.txt -- --no-cache
```

Output goes to `output/` named after the first script file.

### Caching

Rendered clips are cached in `cache/` keyed by a hash of all inputs (text,
voice, seed, params). Re-running the same script skips already-rendered
lines. Change any parameter and only affected lines re-render.

### Compressing

```bash
just compress  # encode output/*.wav to .opus
```

## Single line TTS

```bash
just say "Hello world"
just say -- -v feynman "Hello world"
```

## Voice aliases

Source `tts-voices.bash` to create shell functions for each voice in
`voices/`. Add this to your `~/.bashrc`:

```bash
export COMFYUI_URL=http://comfyui.example.com
source ~/git/vendor/enigmacurry/tts-demo/tts-voices.bash
```

This creates a function for each `.wav` file in `voices/`. For example,
`voices/mcgill.wav` creates a `mcgill` command:

```bash
mcgill "hello there"         # speak text with the mcgill voice
mcgill                       # read from stdin
mcgill -s 42 "hello there"   # pass flags through to tts.py
```

Run `tts_load_voices` to reload after adding new voice files.

## Cleanup

```bash
just clean  # remove output/, cache/, __pycache__/ (with confirmation)
```

## Justfile targets

| Target | Description |
|--------|-------------|
| `just render` | Render dialog script(s) without playing |
| `just play` | Render and play dialog script(s) |
| `just say` | Speak a single line of text |
| `just sync` | Upload voices to ComfyUI server |
| `just compress` | Encode output wav files to opus |
| `just clean` | Remove output and cache directories |
