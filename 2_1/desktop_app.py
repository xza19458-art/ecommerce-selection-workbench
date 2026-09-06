"""桌面壳（打包路线阶段 2）：双击即用的本地选品分析工作台入口。

启动流程（见 decisions/2026-06-20-本地选品分析工作台打包路线.md §阶段2）：
    双击 → 自动选可用端口 → 后台线程起 FastAPI/Uvicorn → 轮询 /api/health 存活
    → 检查 /api/ready 数据库就绪
    → pywebview 打开桌面窗口加载本地 Web UI → 关闭窗口时优雅停止后端。

边界：桌面壳只是承载/分发形态，**不改现有 API 与数据口径、不绕过采集边界**。
路径运行时定位（不写死），兼容开发态与后续 PyInstaller 打包态。

本地运行（开发态）：
    cd 2_1 && ..\\.venv\\Scripts\\python.exe desktop_app.py
"""

from __future__ import annotations

import argparse
import logging
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_TITLE = "Amazon 选品助手"
HOST = "127.0.0.1"
HEALTH_TIMEOUT = 30.0  # 秒：后端就绪轮询上限
APP_BACKGROUND_COLOR = "#0b0e13"
APP_TITLE_BAR_TEXT_COLOR = "#e9eef5"
APP_TITLE_BAR_BORDER_COLOR = "#20262f"
APP_ICON_RESOURCE = ("web", "app-icon.ico")
TRAY_TOOLTIP = "Amazon 选品助手正在后台运行"

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

    config = uvicorn.Config(
        "api.app:app",
        host=HOST,
        port=port,
        log_level="warning",
        log_config=None,
    )
    return _ThreadedServer(config)


def _app_icon_path() -> Path | None:
    from pkg_paths import resource_path

    icon_path = resource_path(*APP_ICON_RESOURCE)
    return icon_path if icon_path.exists() else None


def _webview_start_options() -> dict[str, object]:
    """Use a stable user-writable profile so local UI state survives restarts."""
    from pkg_paths import user_data_path

    storage_path = user_data_path("webview_state").resolve()
    storage_path.mkdir(parents=True, exist_ok=True)
    return {"private_mode": False, "storage_path": str(storage_path)}


def _open_database_config(path: Path) -> None:
    opener = getattr(os, "startfile", None)
    if sys.platform != "win32" or opener is None:
        return
    try:
        opener(str(path))
    except OSError:
        logger.debug("无法自动打开数据库配置文件：%s", path, exc_info=True)


def _database_not_ready_message(reason: str, config_path: Path, *, frozen: bool) -> str:
    if frozen:
        initialization = (
            "如这是新数据库，请在 EXE 所在目录打开 PowerShell 并运行：\n"
            ".\\AmazonSelectionWorkbench.exe --init-mysql"
        )
    else:
        initialization = (
            "如这是新数据库，请在项目 2_1 目录运行：\n"
            "..\\.venv\\Scripts\\python.exe scripts\\init_mysql.py"
        )
    return (
        f"数据库尚未就绪：{reason}\n\n"
        f"请确认 MySQL 已启动，并检查配置文件：\n{config_path}\n\n"
        f"{initialization}\n\n"
        "详情见 logs/desktop.log。"
    )


def _initialize_database() -> int:
    try:
        from database.mysql_client import MySQLClient

        result = MySQLClient().initialize_schema()
        action_labels = {
            "fresh_baseline": "新数据库已创建并建立迁移基线",
            "legacy_baseline": "现有数据库已核验并纳入迁移基线",
            "migrated": "数据库增量迁移已完成",
            "current": "数据库已经是当前版本",
        }
        message = (
            f"{action_labels.get(result['action'], result['action'])}。\n\n"
            f"数据库：{result['database']}\n"
            f"迁移文件：{result['migration_count']}"
        )
        logger.info(message.replace("\n", " "))
        _message_box_info(None, message)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("桌面包初始化数据库失败")
        _message_box_info(None, f"数据库初始化失败：{exc}\n\n详情见 logs/desktop.log。")
        return 1


def _utility_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Amazon 选品助手部署与迁移工具")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--deployment-preflight",
        action="store_true",
        help="只读检查数据库、浏览器、分析仓库和本地数据目录",
    )
    modes.add_argument(
        "--backup-local-data",
        action="store_true",
        help="备份本地证据；不包含 MySQL 和浏览器会话",
    )
    modes.add_argument(
        "--restore-local-data",
        type=Path,
        metavar="ARCHIVE",
        help="校验并恢复本地证据迁移包",
    )
    parser.add_argument("--backup-output", type=Path, help="指定迁移包输出路径")
    parser.add_argument(
        "--include-private-config",
        action="store_true",
        help="备份时显式加入明文数据库与 Agent 等私有配置",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="恢复时覆盖不同内容，并先备份原文件",
    )
    return parser


def _run_utility_mode(argv: list[str]) -> int | None:
    mode_flags = {
        "--deployment-preflight",
        "--backup-local-data",
        "--restore-local-data",
    }
    if not any(flag in argv for flag in mode_flags):
        return None
    try:
        args = _utility_parser().parse_args(argv)
        if not args.backup_local_data and (
            args.backup_output is not None or args.include_private_config
        ):
            raise ValueError("--backup-output 与 --include-private-config 只能用于本地数据备份。")
        if args.restore_local_data is None and args.overwrite_existing:
            raise ValueError("--overwrite-existing 只能用于本地数据恢复。")
        if args.deployment_preflight:
            from services.deployment_preflight import get_deployment_preflight

            result = get_deployment_preflight()
            message = (
                f"{result['message']}\n\n"
                f"分析功能：{'已就绪' if result['ready_for_analysis'] else '未就绪'}\n"
                f"联网采集：{'已就绪' if result['ready_for_collection'] else '未就绪'}\n\n"
                "该检查未下载驱动、未启动 Chrome、未修改数据库或业务数据。"
            )
            exit_code = 0 if result["ready_for_analysis"] else 1
        elif args.backup_local_data:
            from services.user_data_transfer import create_user_data_backup

            result = create_user_data_backup(
                output_path=args.backup_output,
                include_private_config=args.include_private_config,
            ).to_dict()
            private_note = (
                "\n\n注意：本次迁移包包含明文私有配置，请按敏感文件保管。"
                if args.include_private_config
                else ""
            )
            message = (
                f"{result['message']}\n\n"
                f"路径：{result['archive_path']}\n"
                f"文件：{result['file_count']} 个\n"
                f"SHA-256：{result['archive_sha256']}"
                f"{private_note}"
            )
            exit_code = 0
        else:
            from services.user_data_transfer import restore_user_data_backup

            result = restore_user_data_backup(
                args.restore_local_data,
                overwrite=args.overwrite_existing,
            ).to_dict()
            message = (
                f"{result['message']}\n\n"
                f"目标：{result['target_root']}\n"
                f"恢复前备份：{result['backup_dir'] or '未产生'}\n\n"
                "MySQL 业务数据不在该迁移包中。"
            )
            exit_code = 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        _message_box_info(None, message)
        return exit_code
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - utility modes need a stable desktop error.
        print(f"操作失败：{exc}")
        _message_box_info(None, f"操作失败：{exc}")
        return 1


def _hex_to_colorref(hex_color: str) -> int:
    """把 #RRGGBB 转成 Windows DWM 使用的 COLORREF。"""
    value = hex_color.removeprefix("#")
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)


def _set_dwm_attribute(hwnd: int, attribute: int, value: int) -> bool:
    """Best-effort 设置 Windows DWM 属性，老系统不支持时返回 False。"""
    import ctypes
    from ctypes import wintypes

    data = ctypes.c_int(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(attribute),
        ctypes.byref(data),
        ctypes.sizeof(data),
    )
    return result == 0


def _apply_window_chrome_theme(window) -> None:
    """尽力把 Windows 原生标题栏调整为深色，避免应用顶部突兀白边。"""
    if sys.platform != "win32":
        return

    try:
        if not window.events.shown.wait(timeout=10):
            logger.debug("窗口显示事件超时，跳过标题栏主题设置。")
            return

        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            logger.debug("未获取到 Windows 窗口句柄，跳过标题栏主题设置。")
            return

        hwnd = int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle.ToInt32())

        # DWMWA_USE_IMMERSIVE_DARK_MODE：Win10/11 新版本为 20，旧版本常见为 19。
        for attribute in (20, 19):
            if _set_dwm_attribute(hwnd, attribute, 1):
                break

        # Win11 支持显式标题栏/边框/文字色；老版本不支持时会安静失败。
        title_bar_colors = {
            34: _hex_to_colorref(APP_TITLE_BAR_BORDER_COLOR),  # DWMWA_BORDER_COLOR
            35: _hex_to_colorref(APP_BACKGROUND_COLOR),  # DWMWA_CAPTION_COLOR
            36: _hex_to_colorref(APP_TITLE_BAR_TEXT_COLOR),  # DWMWA_TEXT_COLOR
        }
        for attribute, value in title_bar_colors.items():
            _set_dwm_attribute(hwnd, attribute, value)
    except Exception:  # noqa: BLE001
        logger.debug("设置 Windows 深色标题栏失败，继续使用系统默认外观。", exc_info=True)


def _window_hwnd(window) -> int:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return 0
    return int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle.ToInt32())


def _winforms_close_choice(window) -> str | None:
    """Show a small Windows dialog with exact Chinese action labels."""
    if sys.platform != "win32":
        return None

    try:
        import clr

        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")

        from System.Drawing import Color, Font, FontStyle, Point, Size
        from System.Windows.Forms import (
            Button,
            DialogResult,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Label,
        )

        owner = getattr(window, "native", None)
        form = Form()
        form.Text = APP_TITLE
        form.ClientSize = Size(460, 178)
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.StartPosition = FormStartPosition.CenterParent if owner else FormStartPosition.CenterScreen
        form.MaximizeBox = False
        form.MinimizeBox = False
        form.ControlBox = False
        form.ShowInTaskbar = False
        form.TopMost = owner is None
        form.BackColor = Color.FromArgb(246, 248, 252)

        title = Label()
        title.Text = "关闭 Amazon 选品助手？"
        title.AutoSize = False
        title.Location = Point(24, 22)
        title.Size = Size(410, 28)
        title.Font = Font("Microsoft YaHei UI", 11, FontStyle.Bold)

        detail = Label()
        detail.Text = "隐藏后，后端会继续运行；可从任务托盘恢复窗口或退出程序。"
        detail.AutoSize = False
        detail.Location = Point(24, 58)
        detail.Size = Size(410, 44)
        detail.Font = Font("Microsoft YaHei UI", 9)

        hide_button = Button()
        hide_button.Text = "隐藏到任务托盘"
        hide_button.Location = Point(142, 122)
        hide_button.Size = Size(132, 34)
        hide_button.DialogResult = DialogResult.Yes

        exit_button = Button()
        exit_button.Text = "退出程序"
        exit_button.Location = Point(292, 122)
        exit_button.Size = Size(110, 34)
        exit_button.DialogResult = DialogResult.No

        form.Controls.Add(title)
        form.Controls.Add(detail)
        form.Controls.Add(hide_button)
        form.Controls.Add(exit_button)
        form.AcceptButton = hide_button

        result = form.ShowDialog(owner) if owner else form.ShowDialog()
        form.Dispose()
        if result == DialogResult.Yes:
            return "hide"
        if result == DialogResult.No:
            return "exit"
        return "hide"
    except Exception:  # noqa: BLE001
        logger.debug("WinForms 自定义关闭对话框不可用，尝试 TaskDialog。", exc_info=True)
        return None


def _task_dialog_close_choice(window) -> str | None:
    """Show a Windows TaskDialog with custom button labels."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class TASKDIALOG_BUTTON(ctypes.Structure):
            _fields_ = [
                ("nButtonID", ctypes.c_int),
                ("pszButtonText", wintypes.LPCWSTR),
            ]

        CALLBACK = ctypes.WINFUNCTYPE(
            wintypes.HRESULT,
            wintypes.HWND,
            ctypes.c_uint,
            wintypes.WPARAM,
            wintypes.LPARAM,
            ctypes.c_longlong,
        )

        class TASKDIALOGCONFIG(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwndParent", wintypes.HWND),
                ("hInstance", wintypes.HINSTANCE),
                ("dwFlags", ctypes.c_uint),
                ("dwCommonButtons", ctypes.c_uint),
                ("pszWindowTitle", wintypes.LPCWSTR),
                ("hMainIcon", wintypes.HANDLE),
                ("pszMainInstruction", wintypes.LPCWSTR),
                ("pszContent", wintypes.LPCWSTR),
                ("cButtons", ctypes.c_uint),
                ("pButtons", ctypes.POINTER(TASKDIALOG_BUTTON)),
                ("nDefaultButton", ctypes.c_int),
                ("cRadioButtons", ctypes.c_uint),
                ("pRadioButtons", ctypes.c_void_p),
                ("nDefaultRadioButton", ctypes.c_int),
                ("pszVerificationText", wintypes.LPCWSTR),
                ("pszExpandedInformation", wintypes.LPCWSTR),
                ("pszExpandedControlText", wintypes.LPCWSTR),
                ("pszCollapsedControlText", wintypes.LPCWSTR),
                ("hFooterIcon", wintypes.HANDLE),
                ("pszFooter", wintypes.LPCWSTR),
                ("pfCallback", CALLBACK),
                ("lpCallbackData", ctypes.c_longlong),
                ("cxWidth", ctypes.c_uint),
            ]

        hide_id = 1001
        exit_id = 1002
        buttons = (TASKDIALOG_BUTTON * 2)(
            TASKDIALOG_BUTTON(hide_id, "隐藏到任务托盘"),
            TASKDIALOG_BUTTON(exit_id, "退出程序"),
        )
        result = ctypes.c_int(0)
        config = TASKDIALOGCONFIG()
        config.cbSize = ctypes.sizeof(TASKDIALOGCONFIG)
        config.hwndParent = wintypes.HWND(_window_hwnd(window))
        config.dwFlags = 0x01000000  # size to content
        config.pszWindowTitle = APP_TITLE
        config.pszMainInstruction = "关闭 Amazon 选品助手？"
        config.pszContent = "隐藏后，后端会继续运行；可从任务托盘恢复窗口或退出程序。"
        config.cButtons = 2
        config.pButtons = buttons
        config.nDefaultButton = hide_id
        hr = ctypes.windll.comctl32.TaskDialogIndirect(
            ctypes.byref(config),
            ctypes.byref(result),
            None,
            None,
        )
        if hr != 0:
            return None
        return {hide_id: "hide", exit_id: "exit"}.get(result.value, "hide")
    except Exception:  # noqa: BLE001
        logger.debug("TaskDialog 关闭对话框不可用。", exc_info=True)
        return None


def _show_close_choice(window) -> str:
    if sys.platform != "win32":
        return "exit"
    return _winforms_close_choice(window) or _task_dialog_close_choice(window) or "hide"


class DesktopShellController:
    def __init__(self, window, base_url: str, icon_path: Path | None):
        self.window = window
        self.base_url = base_url
        self.icon_path = icon_path
        self.exiting = False
        self.hidden_to_tray = False
        self._tray_icon = None
        self._tray_lock = threading.Lock()

    def attach(self) -> None:
        self.window.events.closing += self.on_closing

    def on_closing(self) -> bool:
        if self.exiting:
            return True

        choice = _show_close_choice(self.window)
        if choice == "exit":
            self.exiting = True
            return True
        if choice == "hide":
            if self.hide_to_tray():
                return False
            _message_box_info(self.window, "无法创建任务托盘图标，已取消关闭。请查看 logs/desktop.log。")
            return False
        return False

    def hide_to_tray(self) -> bool:
        try:
            self._ensure_tray_icon()
            self.hidden_to_tray = True
            self.window.hide()
            self._notify_tray()
            logger.info("窗口已隐藏到任务托盘。")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("隐藏到任务托盘失败")
            return False

    def restore_window(self, *_args) -> None:
        try:
            self.hidden_to_tray = False
            self.window.show()
            logger.info("窗口已从任务托盘恢复。")
        except Exception:  # noqa: BLE001
            logger.exception("从任务托盘恢复窗口失败")

    def exit_from_tray(self, *_args) -> None:
        logger.info("从任务托盘退出程序。")
        self.exiting = True
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            logger.exception("从任务托盘退出窗口失败")

    def stop_tray(self) -> None:
        with self._tray_lock:
            icon = self._tray_icon
            self._tray_icon = None
        if icon:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                logger.debug("停止任务托盘图标失败。", exc_info=True)

    def _ensure_tray_icon(self) -> None:
        with self._tray_lock:
            if self._tray_icon:
                return

            import pystray
            from PIL import Image, ImageDraw

            image = None
            if self.icon_path and self.icon_path.exists():
                image = Image.open(self.icon_path).convert("RGBA")
            if image is None:
                image = Image.new("RGBA", (64, 64), "#0b0e13")
                draw = ImageDraw.Draw(image)
                draw.polygon([(32, 8), (56, 54), (8, 54)], fill="#4d8dff")

            menu = pystray.Menu(
                pystray.MenuItem("打开窗口", self.restore_window, default=True),
                pystray.MenuItem("退出程序", self.exit_from_tray),
            )
            self._tray_icon = pystray.Icon("amazon_selection_workbench", image, TRAY_TOOLTIP, menu)
            self._tray_icon.run_detached()

    def _notify_tray(self) -> None:
        icon = self._tray_icon
        if not icon:
            return
        try:
            icon.notify("应用已隐藏到任务托盘。", APP_TITLE)
        except Exception:  # noqa: BLE001
            logger.debug("任务托盘通知不可用，忽略。", exc_info=True)


def _message_box_info(window, text: str) -> None:
    if sys.platform != "win32":
        print(text)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(_window_hwnd(window), text, APP_TITLE, 0x00000040)


def _on_window_started(window, controller: DesktopShellController) -> None:
    _apply_window_chrome_theme(window)


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


def wait_for_readiness(base_url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """等待数据库 readiness，并保留后端返回的中文失败原因。"""
    deadline = time.monotonic() + timeout
    url = f"{base_url}/api/ready"
    last_message = "数据库就绪检查未返回结果"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                report = payload.get("data") or {}
                if resp.status == 200 and report.get("ready"):
                    return True, str(report.get("message") or "数据库已就绪")
                last_message = str(payload.get("message") or last_message)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                last_message = str(payload.get("message") or last_message)
            except (UnicodeDecodeError, json.JSONDecodeError):
                last_message = f"数据库就绪检查返回 HTTP {exc.code}"
        except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_message = f"数据库就绪检查失败: {exc}"
        time.sleep(0.4)
    return False, last_message


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
    if "--smoke" in sys.argv:
        return _smoke_check()

    utility_result = _run_utility_mode(sys.argv[1:])
    if utility_result is not None:
        return utility_result

    log_path = _setup_logging()
    logger.info("桌面壳启动，日志：%s", log_path)

    try:
        from database.config_bootstrap import prepare_database_config

        config_preparation = prepare_database_config()
    except Exception as exc:  # noqa: BLE001
        logger.exception("准备数据库配置失败")
        _message_box_info(None, f"无法准备数据库配置：{exc}\n\n详情见 logs/desktop.log。")
        return 1

    if config_preparation.status == "created":
        msg = (
            "已为桌面版创建数据库配置模板。\n\n"
            f"请填写 MySQL 信息后重新打开应用：\n{config_preparation.path}"
        )
        logger.warning(msg.replace("\n", " "))
        _message_box_info(None, msg)
        _open_database_config(config_preparation.path)
        return 1
    if config_preparation.status == "migrated":
        logger.info(
            "已将误放在 _internal/config 的数据库配置复制到可写目录：%s",
            config_preparation.path,
        )

    if "--init-mysql" in sys.argv:
        return _initialize_database()

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

        database_ready, readiness_message = wait_for_readiness(base_url)
        if not database_ready:
            server.should_exit = True
            thread.join(timeout=5)
            from pkg_paths import is_frozen

            msg = _database_not_ready_message(
                readiness_message,
                config_preparation.path,
                frozen=is_frozen(),
            )
            logger.error(msg.replace("\n", " "))
            _message_box_info(None, msg)
            return 1

        logger.info("后端与数据库就绪，打开桌面窗口。")
        icon_path = _app_icon_path()
        window = webview.create_window(
            APP_TITLE,
            base_url,
            width=1280,
            height=860,
            min_size=(960, 640),
            background_color=APP_BACKGROUND_COLOR,
        )
        controller = DesktopShellController(window, base_url, icon_path)
        controller.attach()
        webview.start(
            func=_on_window_started,
            args=(window, controller),
            icon=str(icon_path) if icon_path else None,
            **_webview_start_options(),
        )  # 阻塞直到窗口关闭

        logger.info("窗口已关闭，停止后端。")
        controller.stop_tray()
        server.should_exit = True
        thread.join(timeout=5)
        return 0
    except Exception as exc:  # 兜底：任何启动异常都给中文提示、不裸抛栈
        controller = locals().get("controller")
        if controller:
            controller.stop_tray()
        logger.exception("桌面壳启动失败")
        print(f"启动失败：{exc}（详见 logs/desktop.log）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
