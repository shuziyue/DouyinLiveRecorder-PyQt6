# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# 自动收集 src 包及其所有子模块
hiddenimports = collect_submodules('src')
hiddenimports += [
    'douyin_only',
    'ffmpeg_install',
    'i18n',
    'flask',
    'flask.app',
    'flask.helpers',
    'flask.templating',
    'flask.wrappers',
    'flask.json',
    'jinja2',
    'werkzeug',
    'execjs',
    'httpx',
    'httpcore',
    'h2',
    'loguru',
    'Crypto',
    'Crypto.Cipher',
    'Crypto.Cipher.AES',
    'Crypto.Util',
    'distro',
    'tqdm',
    'requests',
    'urllib3',
    'certifi',
    'charset_normalizer',
    'idna',
    'typing_extensions',
    'anyio',
    'sniffio',
    'h11',
    'hpack',
    'hyperframe',
    'six',
    'win32_setctime',
    'colorama',
    'webview',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/javascript', 'src/javascript'),
        ('i18n', 'i18n'),
        ('templates', 'templates'),
        ('douyin_only.py', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DouyinLiveRecorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DouyinLiveRecorder',
)
