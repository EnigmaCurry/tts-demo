## tts-voices.bash — source this file to create voice functions
## Usage: source ~/git/vendor/enigmacurry/tts-demo/tts-voices.bash
##
## Creates a shell function for each .wav file in the voices/ directory.
## For example, voices/mcgill.wav creates a function `say-mcgill` so you can run:
##   say-mcgill hello there whatever
## which is equivalent to:
##   python3 tts.py -v mcgill "hello there whatever"

TTS_DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_tts_voice_fn() {
    local voice="$1"; shift
    if [ $# -eq 0 ]; then
        python3 "${TTS_DEMO_DIR}/tts.py" -v "$voice"
    else
        python3 "${TTS_DEMO_DIR}/tts.py" -v "$voice" "$*"
    fi
}

_tts_voice_completions() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local opts="-s --seed -t --token-scale -o --output --no-play --url"
    COMPREPLY=($(compgen -W "$opts" -- "$cur"))
}

tts_load_voices() {
    local wav name
    for wav in "${TTS_DEMO_DIR}"/voices/*.wav; do
        [ -f "$wav" ] || continue
        name="$(basename "$wav" .wav)"
        eval "say-${name}() { _tts_voice_fn ${name} \"\$@\"; }"
        complete -F _tts_voice_completions "say-${name}"
    done
}

tts_load_voices
