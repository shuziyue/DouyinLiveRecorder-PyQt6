# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# 自动收集 src 包及其所有子模块
hiddenimports = collect_submodules('src')
hiddenimports += [
    'douyin_only',
    'ffmpeg_install',
    'i18n',
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
]

# 公共数据文件
datas = [
    ('src/javascript', 'src/javascript'),
    ('i18n', 'i18n'),
    ('douyin_only.py', '.'),
]

# ========== 主程序（PyQt6 GUI） ==========
a = Analysis(
    ['main_pyqt6.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    name='抖音直播录制工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ========== 录制程序 ==========
a2 = Analysis(
    ['recorder.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz2 = PYZ(a2.pure)

exe2 = EXE(
    pyz2,
    a2.scripts,
    [],
    exclude_binaries=True,
    name='recorder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ========== 收集所有文件到一个目录 ==========
coll = COLLECT(
    exe,
    exe2,
    a.binaries,
    a.datas,
    a2.binaries,
    a2.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='抖音直播录制工具',
)
