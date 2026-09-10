# -*- mode: python ; coding: utf-8 -*-
# ChipLookup 打包 spec（含白板手写识别）
#
# 生成命令：
#   python -m PyInstaller ChipLookup.spec --clean --noconfirm
#
# 构建前必须先固化模型权重：
#   python tools/fetch_models.py
#
# ---------------------------------------------------------------------------
# 为什么默认 onedir 而不是 onefile
# ---------------------------------------------------------------------------
# 白板识别依赖 torch + transformers + TrOCR 权重（约 1.3GB），整包约 2GB。
# onefile 每次启动都要把 ~2GB 解压到临时目录，冷启动要几十秒、还要占用等量
# 磁盘，用户体验不可接受。onedir 不落地解压，启动即读。
#
# 启动速度不受体积影响的另一个前提：src/math_canvas.py 只在 _get_model()
# 里 import torch/transformers（延迟加载），进程启动阶段不会加载它们。
#
# 需要回到单文件轻量版（不含白板）时：
#   CHIPLOOKUP_ONEFILE=1 CHIPLOOKUP_NO_WHITEBOARD=1 python -m PyInstaller ChipLookup.spec
#
# ---------------------------------------------------------------------------
# 其它关键点
# ---------------------------------------------------------------------------
#   - datas 把 data/ 整个目录打包进 _MEIPASS
#   - runtime 启动时会把 _MEIPASS/data/chip_database.csv 拷一份到 exe 旁边 data/
#     (src/paths.py: ensure_user_database)
#   - fdnext/data/fdnext-core-3.2.0 是规则快照（~887KB），打包后离线可用
#   - models/trocr-math 是白板模型权重，src/math_canvas.py: _resolve_model_dir()
#     通过 paths.bundle_root() 定位到 _MEIPASS/models/trocr-math
#
# 兼容性提示：
#   - 本 spec 用 Python 3.11 打包时，产物兼容 Win10+，不兼容 Windows 7
#   - 需要 Windows 7 时请在 Python 3.8 (32/64) + PyInstaller 5.13.x 下重跑

import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

spec_dir = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = spec_dir

# 构建开关（环境变量控制）
ONEFILE = os.environ.get("CHIPLOOKUP_ONEFILE", "0") == "1"
NO_WHITEBOARD = os.environ.get("CHIPLOOKUP_NO_WHITEBOARD", "0") == "1"

MODEL_DIR = os.path.join(PROJECT_ROOT, 'models', 'trocr-math')
WHITEBOARD = (not NO_WHITEBOARD) and os.path.isdir(MODEL_DIR)

if not NO_WHITEBOARD and not os.path.isdir(MODEL_DIR):
    raise SystemExit(
        "缺少白板模型权重：%s\n请先执行：python tools/fetch_models.py\n"
        "（或设 CHIPLOOKUP_NO_WHITEBOARD=1 打一个不含白板的包）" % MODEL_DIR)

datas = [
    (os.path.join(PROJECT_ROOT, 'data'), 'data'),
    # 规则快照：src/fdnext/data/fdnext-core-3.2.0/*.json → _MEIPASS/fdnext/data/fdnext-core-3.2.0/
    (os.path.join(PROJECT_ROOT, 'src', 'fdnext', 'data'), 'fdnext/data'),
    # 窗口图标：运行时 _set_window_icon() 从 _MEIPASS/installer/ 读取
    (os.path.join(PROJECT_ROOT, 'installer', 'chip_lookup.ico'),
     os.path.join('installer', 'chip_lookup.ico')),
    # tools/ 整目录打包进 _MEIPASS/tools/，作为运行时兜底
    (os.path.join(PROJECT_ROOT, 'tools'), 'tools'),
]

hiddenimports = []

# torch / transformers 大量使用动态 import，PyInstaller 静态分析扫不到，
# 必须显式声明，否则打包后运行时报 ModuleNotFoundError。
if WHITEBOARD:
    # 白板模型权重（TrOCR = ViT 编码器 + TrOCR 解码器 + Roberta 分词器）
    datas.append((MODEL_DIR, os.path.join('models', 'trocr-math')))

    hiddenimports += [
        'torch',
        'torch._C',
        'torch._VF',
        'torch.utils.data',
        'numpy',
        'safetensors',
        'tokenizers',
        'regex',
        'filelock',
        'packaging',
        'yaml',
        'huggingface_hub',
        # transformers 的模型注册表与具体实现都是懒加载，按需列出
        'transformers',
        'transformers.models.trocr.modeling_trocr',
        'transformers.models.trocr.tokenization_trocr',
        'transformers.models.vit.modeling_vit',
        'transformers.models.vit.image_processing_vit',
        'transformers.models.vision_encoder_decoder.modeling_vision_encoder_decoder',
        'transformers.models.roberta.tokenization_roberta',
        'transformers.models.auto.modeling_auto',
        'transformers.models.auto.tokenization_auto',
        'transformers.models.auto.image_processing_auto',
        'transformers.models.auto.configuration_auto',
        # sympy 的 latex 解析走 antlr4 运行时，是运行时动态加载的
        'sympy.parsing.latex',
        'antlr4',
        'antlr4.atn',
        'antlr4.dfa',
        'antlr4.error',
        'antlr4.tree',
        'antlr4.xpath',
    ]
    # transformers 自带一批 json/txt 资源（tokenizer 模板、配置等）
    datas += collect_data_files('transformers')

excludes = [
    # 体积大且白板/查询都不用的
    'matplotlib', 'pandas', 'sklearn', 'IPython', 'jupyter',
    'pytest', 'setuptools', 'pkg_resources',
    # 实测被间接拖进来的大块头（共约 250MB），ViT+TrOCR 推理链路用不到：
    #   cv2       —— 112MB，某个依赖的 opencv 可选加速路径
    #   faiss     —— 63MB，向量检索，跟本工具无关
    #   scipy     —— 71MB，sympy/transformers 里都是「可选、条件」导入
    'cv2', 'faiss', 'faiss_cpu', 'scipy',
    # torch 的 CUDA / 分布式 / 训练侧组件，纯 CPU 推理用不上
    'torch.cuda', 'torch.distributed', 'torch.distributed.nn',
    'torch.testing', 'torch.utils.tensorboard',
    'torchvision', 'torchaudio',
    # 仅训练/导出用
    'onnx', 'onnxruntime', 'tensorboard', 'wandb',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'src', 'main.py')],
    pathex=[os.path.join(PROJECT_ROOT, 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_common_exe_kwargs = dict(
    name='ChipLookup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 注意：UPX 压缩 torch 的 DLL 会导致运行期崩溃，这里不开 UPX。
    # 体积主要来自模型权重，压缩收益本就有限。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # GUI 程序，不开控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJECT_ROOT, 'installer', 'chip_lookup.ico'),
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        **_common_exe_kwargs,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **_common_exe_kwargs,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='ChipLookup',
    )
