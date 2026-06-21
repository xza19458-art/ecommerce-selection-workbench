"""运行时路径定位（打包路线阶段 3）。

区分两类路径（见 decisions/2026-06-20-本地选品分析工作台打包路线.md §四：路径不写死）：

- `resource_path`：随程序分发的**只读资源**（如 `web/` 静态前端、`config/*.example.json` 模板）。
  开发态根于 `2_1/`；PyInstaller 冻结态根于 `sys._MEIPASS`（onedir 的 `_internal`）。
- `user_data_path`：**用户可写数据**（真实 `config/`、`data_warehouse/`、`logs/`、`html/`、`reviews/`）。
  开发态根于 `2_1/`；冻结态根于 **exe 同级目录**，便于用户查看/编辑且不被打包覆盖。

**开发态（未冻结）两者都根于 `2_1/`，与打包前行为完全一致——零回归。**
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV_ROOT = Path(__file__).resolve().parent  # 开发态代码根 = 2_1


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结产物中。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _DEV_ROOT


def user_data_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _DEV_ROOT


def resource_path(*parts: str) -> Path:
    """定位只读分发资源（web/、example 模板等）。"""
    return resource_root().joinpath(*parts)


def user_data_path(*parts: str) -> Path:
    """定位用户可写数据（config/data_warehouse/logs/html/reviews 等）。"""
    return user_data_root().joinpath(*parts)
