# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格（打包路线阶段 3：onedir，不做 onefile）。

构建（在 2_1 目录下，用项目唯一 venv）：
    ..\\.venv\\Scripts\\python.exe -m PyInstaller AmazonSelectionWorkbench.spec --noconfirm

产物：dist/AmazonSelectionWorkbench/AmazonSelectionWorkbench.exe

关键点：
- 入口 = desktop_app.py（桌面壳）。后端经 `uvicorn.Config("api.app:app")` 以**字符串**加载，
  PyInstaller 静态分析探测不到；且本项目大量使用**函数内惰性 import**（`from services.x import ...`）。
  故用 collect_submodules 显式收集 api/core/services/analysis/database/parsers 全部子模块 + uvicorn。
- datas：打包 web/ 静态前端 + config/*.example.json 模板（**不打包真实密钥** database.json 等）。
- 路径：运行时由 pkg_paths 定位（web 走 _MEIPASS，用户数据走 exe 同级），不写死。
- console=False（窗口应用，错误写 logs/desktop.log）；首次排错可临时改 True 看控制台。
"""

from PyInstaller.utils.hooks import collect_submodules

# services 里跳过 translation* 模块：它们 import argostranslate→torch（仅 backfill 离线用），
# Web 运行时不需要，跳过可避免分析阶段拽入数 GB ML 栈、并大幅加快构建。
def _no_translation(name: str) -> bool:
    return "translation" not in name


hiddenimports = (
    ["api.app", "pkg_paths"]
    + collect_submodules("api")
    + collect_submodules("core")
    + collect_submodules("services", filter=_no_translation)
    + collect_submodules("analysis")
    + collect_submodules("database")
    + collect_submodules("parsers")
    + collect_submodules("uvicorn")
    # selenium 用 __getattr__ 懒加载子模块（如 webdriver.chrome.webdriver），静态分析抓不到，全量收。
    + collect_submodules("selenium")
    + collect_submodules("webdriver_manager")
    + ["webview", "clr_loader", "bottle", "duckdb", "pymysql"]
)

datas = [
    ("web", "web"),
    ("config/database.example.json", "config"),
    ("config/warehouse.example.json", "config"),
    ("config/translation.example.json", "config"),
    ("config/agent.example.json", "config"),
]

# 排除桌面 Web 应用**运行时用不到**的重型依赖：
# - Argos 翻译栈（torch/tensorflow/onnxruntime/spacy 等）由 backfill 离线脚本用，
#   Web 请求路径只读库里 title_zh，不在运行时跑翻译 → 全部排除，避免产物暴增数 GB。
# 注：matplotlib **不能排除**——`core/controller.py` 顶层 `from main import ...`，而 `main.py`
# 顶层 `import matplotlib.pyplot`，是 import-time 硬依赖（首次构建漏排除导致冻结 exe 起不来）。
excludes = [
    "torch", "tensorflow", "tensorboard", "onnxruntime", "spacy", "thinc",
    "argostranslate", "ctranslate2", "sentencepiece", "stanza",
    "blis", "cymem", "preshed", "murmurhash", "srsly", "catalogue", "wasabi",
    "IPython", "notebook", "jupyter", "pytest",
]

a = Analysis(
    ["desktop_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AmazonSelectionWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="web/app-icon.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AmazonSelectionWorkbench",
)
