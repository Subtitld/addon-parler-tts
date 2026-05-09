# Parler-TTS Mini v1 add-on for Subtitld

Apache-2.0 prompt-driven TTS based on
[parler-tts/parler-tts-mini-v1](https://huggingface.co/parler-tts/parler-tts-mini-v1).
Heavy: the model is ~2 GB, peak RAM around 4 GB, CPU inference is
~10-30 s per line.

Parler is unusual: it has no preset voice files. Instead, the user
*describes* the voice in plain English ("A female speaker with a
slightly low-pitched voice, clear close-mic recording, no background
noise.") and the model produces speech matching the description.

## Voice ids and consistency

To make this fit Subtitld's voice-id-driven UI, we expose 34
**named consistent speakers** the Parler team trained the model on:

> Jon, Lea, Gary, Jenna, Mike, Laura *(recommended for best quality)*,
> Karen, Rick, Brenda, David, Eileen, Jordan, Yann, Joy, James, Eric,
> Lauren, Rose, Will, Jason, Aaron, Naomie, Alisa, Patrick, Jerry,
> Tina, Bill, Tom, Carol, Barbara, Rebecca, Anna, Bruce, Emily.

Picking the same id across requests yields a stable speaker. Each id
maps to a description template that bakes in the speaker name so the
model picks them up.

Users can override the description entirely via the per-speaker
`voice_description` config field (or the per-request `description`
parameter). Voice cloning from reference audio is **not supported** —
Parler is description-driven only.

## Languages

English only. Multilingual variants are planned upstream but not yet
released as Apache-2.0 weights.

## Building

```bash
pip install pyinstaller
pip install torch --extra-index-url https://download.pytorch.org/whl/cpu
# parler-tts hard-pins transformers==4.46.1; let pip resolve the rest.
pip install parler-tts
pyinstaller parler-tts-addon.spec --distpath dist/
cd dist/parler-tts-addon
zip -r ../parler-tts-1.0.0-linux-x86_64.zip . ../../manifest.json ../../LICENSE ../../README.md
```

The hard pin on `transformers==4.46.1` is upstream's choice — drift
broke Parler in earlier and later versions. We let pip pull it directly
from the package metadata to stay aligned.

## Model storage

Weights are *not* bundled — `from_pretrained()` auto-downloads from
`parler-tts/parler-tts-mini-v1` on first use into the standard HuggingFace
cache. ~2.2 GB on disk.

## License

Wrapper code: Apache-2.0. Parler-TTS Mini v1 weights: Apache-2.0 —
commercial use is permitted, no extra license needed. (The "Mini v1
Jenny" sibling repo has a Jenny dataset attribution clause; we
deliberately default to the plain Mini v1 to keep things simple.)
