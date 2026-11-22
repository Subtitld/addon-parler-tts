"""Speak real lines with the frozen add-on, as Subtitld does.

    python tools/smoke_synth.py dist/parler-tts-addon/parler-tts-addon

Downloads the model on the first line (about 2.6 GB).
Fails on a line that errors, writes no audio, or takes more than the timeout:
1.0.2 failed every line: audiotools' data files were missing from the
bundle.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import wave

LINES = [
    ('parler-jon', 'Hello there. The meeting starts in five minutes.'),
    ('parler-lea', 'All human beings are born free and equal in dignity and rights.'),
]
TIMEOUT = 1800


def main(binary: str) -> int:
    out = tempfile.mkdtemp(prefix='parler-tts-smoke-')
    env = dict(os.environ, SUBTITLD_TEMP_DIR=out)
    proc = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=sys.stderr, text=True, env=env)
    timer = threading.Timer(TIMEOUT, proc.kill)
    timer.start()
    try:
        hello = json.loads(proc.stdout.readline())
        assert hello.get('type') == 'hello', hello
        for n, (voice, text) in enumerate(LINES):
            path = os.path.join(out, f'{n}.wav')
            proc.stdin.write(json.dumps({'type': 'tts.synthesize', 'id': str(n), 'params': {
                'text': text, 'voice': voice, 'output_path': path}}) + '\n')
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise SystemExit(f'{voice}: the add-on exited (or timed out) without an answer')
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:   # anything but JSON on stdout breaks the host
                    raise SystemExit(f'{voice}: not a protocol frame on stdout: {line[:300]!r}')
                if frame.get('id') != str(n) or frame.get('type') == 'progress':
                    continue
                if frame.get('type') != 'result':
                    raise SystemExit(f'{voice}: {frame}')
                break
            with wave.open(path) as w:
                seconds = w.getnframes() / w.getframerate()
            assert seconds > 1.0, f'{voice}: only {seconds:.2f} s of audio'
            print(f'ok {voice}: {seconds:.2f} s', flush=True)
        proc.stdin.write(json.dumps({'type': 'shutdown'}) + '\n')
        proc.stdin.flush()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # As Subtitld does after its grace period. Seen on the macOS
            # runner with Kokoro 26.11.2: every line spoken, then no exit.
            print('warning: still running 30 s after shutdown; terminating it, as Subtitld would', flush=True)
            proc.terminate()
            proc.wait(timeout=10)
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
