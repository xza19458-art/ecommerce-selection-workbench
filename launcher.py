"""Developer launcher for the local Amazon selection workbench desktop shell.

This small wrapper keeps the double-click entry point at the repository root
while reusing ``2_1/desktop_app.py`` for the actual lifecycle:

    launcher -> desktop_app -> FastAPI/Uvicorn -> pywebview -> shutdown server

It intentionally does not duplicate port allocation or backend shutdown logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
APP_DIR = REPO_ROOT / "2_1"


def main() -> int:
    if not APP_DIR.is_dir():
        print(f"Cannot find app directory: {APP_DIR}")
        return 1

    os.chdir(APP_DIR)
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    from desktop_app import main as desktop_main

    return int(desktop_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
