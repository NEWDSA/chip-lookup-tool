# -*- mode: python ; coding: utf-8 -*-
# ChipLookup 单文件 spec
# 生成命令：
#   pyinstaller ChipLookup.spec --clean --noconfirm
#
# 关键点：
#   - datas 把 data/ 整个目录打包进 _MEIPASS
#   - runtime 启动时会把 _MEIPASS/data/chip_database.csv 拷一份到 exe 旁边 data/
#     (src/paths.py: ensure_user_database)
#   - 因此用户对数据库的所有增删改都写到「exe 旁边 data/」里，可长期持久
#
# 兼容性提示：
#   - 本 spec 用 Python 3.11 打包时，产物兼容 Win10+，不兼容 Windows 7
#   - 需要 Windows 7 单文件 exe 时，请在 Python 3.8 (32 或 64) + PyInstaller 5.13.x
#     下重新执行本文件。详见 README.md。

import sys
import os

block_cipher = None

# spec 解析时所在目录
spec_dir = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = spec_dir

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'src', 'main.py')],
    pathex=[os.path.join(PROJECT_ROOT, 'src')],
    binaries=[],
    datas=[
        (os.path.join(PROJECT_ROOT, 'data'), 'data'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 把体积大的能去掉的去掉（让单文件 < 30MB）
        'matplotlib', 'numpy', 'pandas', 'scipy', 'IPython', 'jupyter',
        'pytest', 'setuptools', 'pkg_resources',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ChipLookup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,            # 用 UPX 压缩可执行体，可在 build/ 中放 upx.exe
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # GUI 程序，不开控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=os.path.join(PROJECT_ROOT, 'assets', 'icon.ico') if os.path.exists(os.path.join(PROJECT_ROOT, 'assets', 'icon.ico')) else None,
)
