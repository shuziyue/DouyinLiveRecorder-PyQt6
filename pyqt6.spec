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
    ('assets', 'assets'),
]

# 运行时不用的重量级库：程序代码里没有任何 import，纯属被连带打进来，
# 排掉可以少打包约 100MB（pandas/scipy/numpy/matplotlib/PIL）。
# 以后如果要加图表/数据分析功能，把用到的名字从这里删掉再重新打包即可。
excludes = [
    'pandas', 'matplotlib', 'scipy', 'numpy', 'PIL', 'Pillow',
    'IPython', 'jupyter', 'notebook', 'pytest', 'sphinx', 'docutils',
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
    excludes=excludes,
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
    icon='assets/icon.ico',
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
    excludes=excludes,
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
