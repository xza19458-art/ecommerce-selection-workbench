"""桌面壳（打包路线阶段 2）：双击即用的本地选品分析工作台入口。

启动流程（见 decisions/2026-06-20-本地选品分析工作台打包路线.md §阶段2）：
    双击 → 自动选可用端口 → 后台线程起 FastAPI/Uvicorn → 轮询 /api/health 就绪
    → pywebview 打开桌面窗口加载本地 Web UI → 关闭窗口时优雅停止后端。

边界：桌面壳只是承载/分发形态，**不改现有 API 与数据口径、不绕过采集边界**。
路径运行时定位（不写死），兼容开发态与后续 PyInstaller 打包态。

本地运行（开发态）：
    cd 2_1 && ..\\.venv\\Scripts\\python.exe desktop_app.py
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_TITLE = "Amazon 选品助手"
HOST = "127.0.0.1"
HEALTH_TIMEOUT = 30.0  # 秒：后端就绪轮询上限

logger = logging.getLogger("desktop_app")


def _setup_logging() -> Path:
    """日志写入运行时定位的用户可写目录（开发态 2_1/logs；冻结态 exe 同级 logs）。"""
    from pkg_paths import user_data_path

    log_dir = user_data_path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "desktop.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    return log_path


def find_free_port(host: str = HOST, preferred: int = 8000) -> int:
    """优先用 preferred 端口；被占用则让系统分配一个空闲端口。"""
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, candidate))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise OSError("无法找到可用端口")


def _make_server(port: int):
    """构造一个不在子线程安装信号处理器的 Uvicorn Server。"""
    import uvicorn

    class _ThreadedServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:  # 子线程里不装信号处理器
            pass

    config = uvicorn.Config("api.app:app", host=HOST, port=port, log_level="warning")
    return _ThreadedServer(config)


def serve_in_thread(port: int):
    """后台线程启动后端服务，返回 (server, thread)。调用方用 server.should_exit=True 停止。"""
    server = _make_server(port)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


def wait_for_health(base_url: str, timeout: float = HEALTH_TIMEOUT) -> bool:
    """轮询 /api/health 直到后端就绪或超时。"""
    deadline = time.monotonic() + timeout
    url = f"{base_url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _smoke_check() -> int:
    """打包自检：起后端 + health + 取首页后退出，不开窗（无显示器/CI/冻结产物可用）。

    用于打包发布门禁（见打包路线 §四）：验证冻结产物的惰性 import 与 web/ 资源
    确实被正确打包、后端能起、Web 首页能取到。
    """
    try:
        port = find_free_port()
        base_url = f"http://{HOST}:{port}"
        server, thread = serve_in_thread(port)
        ok = wait_for_health(base_url)
        if ok:
            try:
                with urlopen(f"{base_url}/", timeout=5) as resp:
                    ok = resp.status == 200 and "选品助手" in resp.read().decode("utf-8", "ignore")
            except (URLError, OSError):
                ok = False
        server.should_exit = True
        thread.join(timeout=5)
        print("SMOKE_OK" if ok else "SMOKE_FAIL")
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("自检失败")
        print(f"SMOKE_FAIL：{exc}")
        return 1


def main() -> int:
    log_path = _setup_logging()
    logger.info("桌面壳启动，日志：%s", log_path)

    if "--smoke" in sys.argv:
        return _smoke_check()

    try:
        import webview  # 延迟导入：缺依赖时给中文提示而非裸栈
    except ImportError:
        msg = "未安装 pywebview，无法启动桌面窗口。请先安装：pip install pywebview"
        logger.error(msg)
        print(msg)
        return 1

    try:
        port = find_free_port()
        base_url = f"http://{HOST}:{port}"
        logger.info("启动后端：%s", base_url)
        server, thread = serve_in_thread(port)

        if not wait_for_health(base_url):
            server.should_exit = True
            thread.join(timeout=5)
            msg = "后端服务启动超时，请检查依赖与端口占用（详见 logs/desktop.log）。"
            logger.error(msg)
            print(msg)
            return 1

        logger.info("后端就绪，打开桌面窗口。")
        webview.create_window(APP_TITLE, base_url, width=1280, height=860, min_size=(960, 640))
        # 注意：数据相关报错（如 MySQL 未启动）由 Web 页内统一中文提示，不影响窗口启动。
        webview.start()  # 阻塞直到窗口关闭

        logger.info("窗口已关闭，停止后端。")
        server.should_exit = True
        thread.join(timeout=5)
        return 0
    except Exception as exc:  # 兜底：任何启动异常都给中文提示、不裸抛栈
        logger.exception("桌面壳启动失败")
        print(f"启动失败：{exc}（详见 logs/desktop.log）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
