"""Subtitld add-on entry point for Parler-TTS Mini v1.

Wraps `parler-tts` (PyPI, Apache-2.0). Parler is *prompt-driven*: there
are no preset voices in the model itself — instead, you describe the
voice in plain English ("A female speaker with a slightly low-pitched
voice, clear close-mic recording, no background noise.") and the model
follows that description.

To make this fit Subtitld's voice-id-driven UI, we expose 34 named
"consistent speakers" the Parler team trained the model to recognize:
Jon, Lea, Gary, Jenna, Mike, Laura, ... — picking the same voice id
across requests yields a stable speaker. Each id maps to a description
template that bakes in the speaker name so the model picks them up.

The user can also override the description entirely via the
per-speaker `voice_description` config field (or the per-request
`description` parameter).

API call shape:
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer
    model = ParlerTTSForConditionalGeneration.from_pretrained(REPO).to(device)
    tok = AutoTokenizer.from_pretrained(REPO)
    desc_ids = tok(description, return_tensors='pt').input_ids.to(device)
    prompt_ids = tok(text, return_tensors='pt').input_ids.to(device)
    audio = model.generate(input_ids=desc_ids, prompt_input_ids=prompt_ids)
    wav = audio.cpu().numpy().squeeze()  # float32 mono, 44_100 Hz
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger('parler-tts')
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='[parler-tts] %(levelname)s %(message)s')

PROTOCOL = 1
ADDON_ID = 'parler-tts'
VERSION = '1.0.2'

DEFAULT_REPO = os.environ.get('PARLER_TTS_REPO', 'parler-tts/parler-tts-mini-v1')

# Voice id (suffix after 'parler-') → friendly name to embed in the description.
# The set is the 34 named speakers the Parler team consistently trained on
# (see https://huggingface.co/parler-tts/parler-tts-mini-v1#📖-quick-index).
NAMED_SPEAKERS: dict[str, str] = {
    'jon':     'Jon',     'lea':     'Lea',     'gary':    'Gary',
    'jenna':   'Jenna',   'mike':    'Mike',    'laura':   'Laura',
    'karen':   'Karen',   'rick':    'Rick',    'brenda':  'Brenda',
    'david':   'David',   'eileen':  'Eileen',  'jordan':  'Jordan',
    'yann':    'Yann',    'joy':     'Joy',     'james':   'James',
    'eric':    'Eric',    'lauren':  'Lauren',  'rose':    'Rose',
    'will':    'Will',    'jason':   'Jason',   'aaron':   'Aaron',
    'naomie':  'Naomie',  'alisa':   'Alisa',   'patrick': 'Patrick',
    'jerry':   'Jerry',   'tina':    'Tina',    'bill':    'Bill',
    'tom':     'Tom',     'carol':   'Carol',   'barbara': 'Barbara',
    'rebecca': 'Rebecca', 'anna':    'Anna',    'bruce':   'Bruce',
    'emily':   'Emily',
}

# Default description template — neutral, mic-close, no background. Subtitld
# users can override per-speaker via voice_description in the addon config.
DEFAULT_DESC_TEMPLATE = (
    "{speaker} speaks at a moderate pace with a clear, "
    "close-mic recording and no background noise."
)


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------
_write_lock = threading.Lock()


def write_frame(frame: dict) -> None:
    line = json.dumps(frame, ensure_ascii=False)
    with _write_lock:
        sys.stdout.write(line + '\n')
        sys.stdout.flush()


def emit_progress(rid, value, message=''):
    write_frame({'id': rid, 'type': 'progress',
                 'data': {'value': max(0.0, min(1.0, float(value))), 'message': message}})


def emit_error(rid, code, message, retryable=False):
    write_frame({'id': rid, 'type': 'error',
                 'data': {'code': code, 'message': message, 'retryable': retryable}})


def emit_result(rid, data):
    write_frame({'id': rid, 'type': 'result', 'data': data})


# ---------------------------------------------------------------------------
# Model state — loaded lazily on first request
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()
_model_cache: dict = {'model': None, 'tokenizer': None, 'device': None}
_pending_cancel: set[str] = set()
_pending_cancel_lock = threading.Lock()


def _load_model(device: str):
    with _model_lock:
        cache = _model_cache
        if cache['model'] is not None and cache['device'] == device:
            return cache['model'], cache['tokenizer']

        try:
            import torch  # type: ignore  # noqa: F401
            from parler_tts import ParlerTTSForConditionalGeneration  # type: ignore
            from transformers import AutoTokenizer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(f'parler-tts python package not available: {exc}') from exc

        log.info('loading %s on %s', DEFAULT_REPO, device)
        model = ParlerTTSForConditionalGeneration.from_pretrained(DEFAULT_REPO).to(device)
        tokenizer = AutoTokenizer.from_pretrained(DEFAULT_REPO)
        cache['model'] = model
        cache['tokenizer'] = tokenizer
        cache['device'] = device
        return model, tokenizer


# ---------------------------------------------------------------------------
# Audio writing
# ---------------------------------------------------------------------------
def _write_wav(path: str, wav, sample_rate: int) -> tuple[float, int, int]:
    import numpy as np
    import soundfile as sf

    arr = np.asarray(wav)
    if arr.ndim > 1:
        arr = arr.squeeze()
    if arr.ndim != 1:
        raise RuntimeError(f'unexpected waveform shape: {arr.shape}')

    arr = np.clip(arr.astype(np.float32, copy=False), -1.0, 1.0)
    sf.write(path, arr, int(sample_rate), subtype='PCM_16')

    duration = float(len(arr)) / float(sample_rate or 1)
    return duration, int(sample_rate), 1


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------
def _resolve_description(voice_id: str, params: dict, defaults: dict) -> str:
    """Per-request `description` wins. Else per-speaker
    `voice_description` from the addon defaults. Else the canned
    template substituted with the named speaker."""
    direct = params.get('description') or defaults.get('voice_description')
    if direct:
        return str(direct)

    bare = voice_id[len('parler-'):] if voice_id.startswith('parler-') else voice_id
    speaker = NAMED_SPEAKERS.get(bare, 'A speaker')
    return DEFAULT_DESC_TEMPLATE.format(speaker=speaker)


def handle_tts_synthesize(rid: str, params: dict, defaults: dict) -> None:
    text = params.get('text')
    voice_id = params.get('voice')
    output_path = params.get('output_path')
    if not text or not voice_id or not output_path:
        emit_error(rid, 'bad_params', 'text, voice, and output_path are all required')
        return

    bare = voice_id[len('parler-'):] if voice_id.startswith('parler-') else voice_id
    if bare not in NAMED_SPEAKERS:
        emit_error(rid, 'unsupported_voice', f'unknown voice id: {voice_id!r}')
        return

    with _pending_cancel_lock:
        if rid in _pending_cancel:
            _pending_cancel.discard(rid)
            emit_error(rid, 'cancelled', 'cancelled before synthesis started')
            return

    description = _resolve_description(voice_id, params, defaults)

    emit_progress(rid, 0.05, 'Loading Parler-TTS (first call may download ~2 GB)...')
    try:
        model, tokenizer = _load_model(defaults['device'])
    except Exception as exc:
        log.exception('model load failed')
        emit_error(rid, 'internal', f'failed to load Parler-TTS: {exc}')
        return

    emit_progress(rid, 0.4, 'Synthesizing...')
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # type: ignore
        device = defaults['device']
        desc_ids = tokenizer(description, return_tensors='pt').input_ids.to(device)
        prompt_ids = tokenizer(text, return_tensors='pt').input_ids.to(device)
        with torch.no_grad():
            audio = model.generate(input_ids=desc_ids, prompt_input_ids=prompt_ids)
        wav = audio.cpu().to(torch.float32).numpy().squeeze()
        sample_rate = int(getattr(model.config, 'sampling_rate', 44100))
    except Exception as exc:
        log.exception('synth failed')
        emit_error(rid, 'internal', f'synthesize failed: {exc}')
        return

    try:
        duration, sample_rate, channels = _write_wav(output_path, wav, sample_rate)
    except Exception as exc:
        log.exception('wav write failed')
        emit_error(rid, 'internal', f'failed to write {output_path}: {exc}')
        return

    emit_progress(rid, 0.99, 'Finalizing...')
    emit_result(rid, {
        'path': output_path,
        'duration_sec': duration,
        'sample_rate': sample_rate,
        'channels': channels,
    })


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> int:
    manifest_path = Path(__file__).resolve().parent / 'manifest.json'
    voices: list[dict] = []
    languages: list[str] = []
    config_defaults: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            voices = manifest.get('voices') or []
            languages = manifest.get('languages') or []
            config_defaults = {f.get('key'): f.get('default')
                               for f in (manifest.get('config_schema') or {}).get('fields', [])
                               if f.get('default') is not None}
        except Exception:
            log.exception('manifest parse failed')

    defaults = {
        'device': os.environ.get('PARLER_TTS_DEVICE') or config_defaults.get('device', 'cpu'),
        'voice_description': os.environ.get('PARLER_TTS_VOICE_DESCRIPTION') or '',
    }

    write_frame({
        'type': 'hello',
        'protocol': PROTOCOL,
        'addon': ADDON_ID,
        'version': VERSION,
        'capabilities': [
            {'task': 'tts.synthesize', 'languages': languages, 'voices': voices,
             'voice_clone': False},
        ],
    })

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            frame = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        ftype = frame.get('type')
        rid = frame.get('id', '')

        if ftype == 'shutdown':
            log.info('shutdown received; exiting')
            return 0
        if ftype == 'cancel':
            target = (frame.get('data') or {}).get('target') or frame.get('target')
            if target:
                with _pending_cancel_lock:
                    _pending_cancel.add(target)
            continue
        if ftype == 'tts.synthesize':
            threading.Thread(
                target=handle_tts_synthesize,
                args=(rid, frame.get('params') or {}, defaults),
                daemon=True,
            ).start()
            continue
        # Host control frames (`ready` confirms our hello, future-proof
        # for other host-→-addon notifications) carry no request id and
        # expect no response. Log and ignore — only error on actual
        # *requests* we don't recognise.
        if not rid:
            log.debug('ignoring host control frame: %s', ftype)
            continue

        emit_error(rid, 'bad_params', f'unknown request type: {ftype!r}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
