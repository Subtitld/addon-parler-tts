# PyInstaller spec for the parler-tts add-on.
# Build with: pyinstaller parler-tts-addon.spec --distpath dist/

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _safe_collect(fn, name):
    try:
        return fn(name)
    except Exception:
        return []


# parler-tts depends on transformers (hard-pinned to 4.46.1!), torch,
# sentencepiece, descript-audio-codec-unofficial, descript-audiotools-unofficial.
# The codec/audiotools packages have C-extensions that the safe collector
# walks gracefully.
hiddenimports = (
    _safe_collect(collect_submodules, 'parler_tts')
    + _safe_collect(collect_submodules, 'transformers')
    + _safe_collect(collect_submodules, 'sentencepiece')
    + _safe_collect(collect_submodules, 'soundfile')
    + _safe_collect(collect_submodules, 'descript')
)
datas = (
    _safe_collect(collect_data_files, 'parler_tts')
    + _safe_collect(collect_data_files, 'transformers')
    + _safe_collect(collect_data_files, 'sentencepiece')
    + [('manifest.json', '.')]
)

block_cipher = None

a = Analysis(
    ['parler_tts_addon.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'jax', 'flax', 'gradio', 'wandb', 'matplotlib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='parler-tts-addon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='parler-tts-addon',
)
