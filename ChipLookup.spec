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
#   - fdnext/data/fdnext-core-3.2.0 是规则快照（~887KB：part/identifier/nand-die
#     specs），打包后资源层在 exe 内直接离线可用（src/fdnext/resources.py:
#     snapshot_dir() 会命中 _MEIPASS/fdnext/data/fdnext-core-3.2.0），
#     避免首启走网络下载规则、以及解码热路径在打包后退化的问题。
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
        # 规则快照：src/fdnext/data/fdnext-core-3.2.0/*.json → _MEIPASS/fdnext/data/fdnext-core-3.2.0/
        (os.path.join(PROJECT_ROOT, 'src', 'fdnext', 'data'),
         'fdnext/data'),
        # 窗口图标：运行时 _set_window_icon() 从 _MEIPASS/installer/ 读取
        (os.path.join(PROJECT_ROOT, 'installer', 'chip_lookup.ico'),
         os.path.join('installer', 'chip_lookup.ico')),
        # tools/ 整目录打包进 _MEIPASS/tools/，作为运行时兜底：
        # src/fdnext/indexes.py 历史曾从 tools/sync_upstream import 常量，
        # 迁移到 fdnext.upstream_common 后该 import 已消除，但保留打包避免
        # 任何残留引用再次触发 "No module named 'sync_upstream'" 类运行时错误。
        (os.path.join(PROJECT_ROOT, 'tools'), 'tools'),
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
    # exe 自身图标：任务栏 / Alt-Tab 切换 / 资源管理器缩略图都用这个
    # 路径：installer/chip_lookup.ico（7 档分辨率，256 / 128 / 64 / 48 / 32 / 24 / 16）
    icon=os.path.join(PROJECT_ROOT, 'installer', 'chip_lookup.ico'),
)
