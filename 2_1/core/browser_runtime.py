"""Chrome/ChromeDriver runtime discovery for the shared collection browser.

The application must never download a browser driver implicitly.  This module
first reuses the locally installed Google Chrome and any compatible driver
already present in PATH or a known cache.  A download only happens through the
explicit ``install_matching_chromedriver`` entry point after UI confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from typing import Iterable

from pkg_paths import user_data_path


logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?)(?!\d)")
_DRIVER_DOWNLOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class ChromeInstallation:
    path: Path
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": "Google Chrome",
            "path": str(self.path),
            "version": self.version,
        }


@dataclass(frozen=True)
class ChromeDriverInstallation:
    path: Path
    version: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "version": self.version,
            "source": self.source,
        }


@dataclass(frozen=True)
class BrowserRuntime:
    chrome: ChromeInstallation
    driver: ChromeDriverInstallation

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ready",
            "browser": self.chrome.to_dict(),
            "driver": self.driver.to_dict(),
            "message": "已找到本机 Chrome 和匹配的浏览器驱动。",
        }


class BrowserRuntimeError(RuntimeError):
    """Structured runtime error returned to the local Web UI."""

    def __init__(self, message: str, *, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class BrowserBusyError(BrowserRuntimeError):
    def __init__(self, operation: str = "浏览器操作") -> None:
        super().__init__(
            f"共享采集浏览器正在执行其他任务，暂不能开始{operation}。请等待当前任务完成后重试。",
            code="browser_busy",
            details={"operation": operation, "retryable": True},
        )


class ChromeNotFoundError(BrowserRuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "未检测到本机 Google Chrome。请先安装或修复 Chrome 后再使用预开启和采集功能；应用不会自动下载浏览器本体。",
            code="chrome_not_found",
            details={"can_download_driver": False},
        )


class ChromeVersionUnknownError(BrowserRuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            "已找到本机 Chrome，但无法识别版本，因此不能安全匹配浏览器驱动。请更新或重新安装 Chrome 后重试。",
            code="chrome_version_unknown",
            details={
                "can_download_driver": False,
                "browser": {"name": "Google Chrome", "path": str(path), "version": ""},
            },
        )


class ChromeDriverRequiredError(BrowserRuntimeError):
    def __init__(
        self,
        chrome: ChromeInstallation,
        detected_drivers: Iterable[ChromeDriverInstallation] = (),
        *,
        reason: str | None = None,
    ) -> None:
        detected = list(detected_drivers)
        found_versions = sorted({item.version for item in detected}, key=_version_parts, reverse=True)
        expected = expected_driver_family(chrome.version)
        message = (
            f"已检测到本机 Chrome {chrome.version}，但未找到匹配的 ChromeDriver"
            f"（需要 {expected}）。"
        )
        if reason:
            message = f"{message}{reason}"
        super().__init__(
            message,
            code="chrome_driver_required",
            details={
                "can_download_driver": True,
                "browser": chrome.to_dict(),
                "expected_driver": expected,
                "detected_driver_versions": found_versions,
                "download_source": "Google Chrome for Testing",
            },
        )


class ChromeDriverInstallError(BrowserRuntimeError):
    def __init__(self, message: str, chrome: ChromeInstallation | None = None) -> None:
        details: dict[str, object] = {"can_download_driver": True}
        if chrome:
            details["browser"] = chrome.to_dict()
            details["expected_driver"] = expected_driver_family(chrome.version)
        super().__init__(message, code="chrome_driver_download_failed", details=details)


class ChromeDriverRepairRequiredError(BrowserRuntimeError):
    def __init__(self, runtime: BrowserRuntime) -> None:
        super().__init__(
            (
                f"本机 Chrome {runtime.chrome.version} 与 ChromeDriver {runtime.driver.version} "
                "未能正常建立会话，需要重新下载匹配驱动。"
            ),
            code="chrome_driver_required",
            details={
                "can_download_driver": True,
                "browser": runtime.chrome.to_dict(),
                "driver": runtime.driver.to_dict(),
                "expected_driver": expected_driver_family(runtime.chrome.version),
                "detected_driver_versions": [runtime.driver.version],
                "download_source": "Google Chrome for Testing",
            },
        )


class ChromeLaunchError(BrowserRuntimeError):
    def __init__(self, message: str, runtime: BrowserRuntime | None = None) -> None:
        details: dict[str, object] = {"can_download_driver": False}
        if runtime:
            details["browser"] = runtime.chrome.to_dict()
            details["driver"] = runtime.driver.to_dict()
        super().__init__(message, code="chrome_launch_failed", details=details)


def _version_parts(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return ()


def versions_are_compatible(chrome_version: str, driver_version: str) -> bool:
    """Apply Chrome's matching rule without starting either executable."""
    chrome = _version_parts(chrome_version)
    driver = _version_parts(driver_version)
    if not chrome or not driver:
        return False
    if chrome[0] >= 115:
        return len(chrome) >= 3 and len(driver) >= 3 and chrome[:3] == driver[:3]
    return chrome[0] == driver[0]


def expected_driver_family(chrome_version: str) -> str:
    parts = _version_parts(chrome_version)
    if not parts:
        return "与 Chrome 匹配的版本"
    if parts[0] >= 115 and len(parts) >= 3:
        return ".".join(str(part) for part in parts[:3]) + ".x"
    return f"{parts[0]}.x"


def _path_key(path: Path) -> str:
    value = str(path.resolve())
    return value.casefold() if sys.platform == "win32" else value


def _windows_registry_chrome_paths() -> list[Path]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    paths: list[Path] = []
    key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    access_modes = [winreg.KEY_READ]
    for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, flag_name, 0)
        if flag:
            access_modes.append(winreg.KEY_READ | flag)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in access_modes:
            try:
                with winreg.OpenKey(hive, key_path, 0, access) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                if value:
                    paths.append(Path(str(value).strip('"')))
            except OSError:
                continue
    return paths


def _chrome_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("AMAZON_CHROME_BINARY", "CHROME_BINARY"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))

    if sys.platform == "win32":
        for env_name in ("PROGRAMW6432", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
        candidates.extend(_windows_registry_chrome_paths())
    elif sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )

    for command in ("chrome", "google-chrome", "google-chrome-stable"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            if not path.is_file():
                continue
            key = _path_key(path)
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            unique.append(path.resolve())
    return unique


def _windows_file_version(path: Path) -> str:
    if sys.platform != "win32":
        return ""

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return ""
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        if info.dwSignature != 0xFEEF04BD:
            return ""
        return ".".join(
            str(part)
            for part in (
                info.dwFileVersionMS >> 16,
                info.dwFileVersionMS & 0xFFFF,
                info.dwFileVersionLS >> 16,
                info.dwFileVersionLS & 0xFFFF,
            )
        )
    except (AttributeError, OSError, ValueError):
        return ""


def _version_from_application_directory(path: Path) -> str:
    try:
        versions = [
            child.name
            for child in path.parent.iterdir()
            if child.is_dir() and _VERSION_RE.fullmatch(child.name)
        ]
    except OSError:
        return ""
    return max(versions, key=_version_parts, default="")


def _command_version(path: Path) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION_RE.search(f"{result.stdout}\n{result.stderr}")
    return match.group(1) if match else ""


def _read_chrome_version(path: Path) -> str:
    version = _windows_file_version(path)
    if version:
        return version
    version = _version_from_application_directory(path)
    if version:
        return version
    version = _command_version(path)
    if version:
        return version
    try:
        from webdriver_manager.core.os_manager import OperationSystemManager

        return OperationSystemManager().get_browser_version_from_os("google-chrome") or ""
    except Exception:  # noqa: BLE001 - final best-effort fallback only
        return ""


def find_local_chrome() -> ChromeInstallation:
    candidates = _chrome_candidate_paths()
    if not candidates:
        raise ChromeNotFoundError()
    for path in candidates:
        version = _read_chrome_version(path)
        if version:
            return ChromeInstallation(path=path, version=version)
    raise ChromeVersionUnknownError(candidates[0])


def _driver_candidate_paths() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for env_name in ("AMAZON_CHROMEDRIVER", "SE_CHROMEDRIVER"):
        value = os.environ.get(env_name)
        if value:
            candidates.append((Path(value), "环境变量"))

    found = shutil.which("chromedriver")
    if found:
        candidates.append((Path(found), "PATH"))

    executable_name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
    roots = [
        (Path.home() / ".wdm" / "drivers" / "chromedriver", "webdriver-manager 缓存"),
        (Path.home() / ".cache" / "selenium" / "chromedriver", "Selenium 缓存"),
        (Path(sys.executable).resolve().parent / "drivers", "应用驱动目录"),
        (user_data_path("drivers"), "应用数据驱动目录"),
    ]
    for root, source in roots:
        if not root.is_dir():
            continue
        try:
            candidates.extend((path, source) for path in root.rglob(executable_name) if path.is_file())
        except OSError:
            continue

    unique: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        try:
            if not path.is_file():
                continue
            key = _path_key(path)
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            unique.append((path.resolve(), source))
    return unique


def read_chromedriver_version(path: Path) -> str:
    return _command_version(path)


def _driver_version_hint(path: Path) -> str:
    for part in reversed(path.parts[:-1]):
        if _VERSION_RE.fullmatch(part):
            return part
    return ""


def discover_chromedrivers() -> list[ChromeDriverInstallation]:
    drivers: list[ChromeDriverInstallation] = []
    for path, source in _driver_candidate_paths():
        version = _driver_version_hint(path) or read_chromedriver_version(path)
        if version:
            drivers.append(ChromeDriverInstallation(path=path, version=version, source=source))
    return drivers


def select_compatible_driver(
    chrome_version: str,
    drivers: Iterable[ChromeDriverInstallation],
) -> ChromeDriverInstallation | None:
    compatible = [item for item in drivers if versions_are_compatible(chrome_version, item.version)]
    if not compatible:
        return None
    return max(
        compatible,
        key=lambda item: (
            item.version == chrome_version,
            _version_parts(item.version),
            item.source == "PATH",
        ),
    )


def resolve_browser_runtime() -> BrowserRuntime:
    chrome = find_local_chrome()
    detected = discover_chromedrivers()
    remaining = list(detected)
    while remaining:
        driver = select_compatible_driver(chrome.version, remaining)
        if not driver:
            break
        actual_version = read_chromedriver_version(driver.path)
        if actual_version and versions_are_compatible(chrome.version, actual_version):
            verified = ChromeDriverInstallation(
                path=driver.path,
                version=actual_version,
                source=driver.source,
            )
            return BrowserRuntime(chrome=chrome, driver=verified)
        remaining.remove(driver)
    raise ChromeDriverRequiredError(chrome, detected)


def get_browser_runtime_status() -> dict[str, object]:
    return resolve_browser_runtime().to_dict()


def install_matching_chromedriver() -> dict[str, object]:
    """Download and cache a matching driver after explicit user confirmation."""
    chrome = find_local_chrome()

    with _DRIVER_DOWNLOAD_LOCK:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.core.driver_cache import DriverCacheManager
            from webdriver_manager.core.os_manager import OperationSystemManager

            class PinnedChromeOSManager(OperationSystemManager):
                def get_browser_version_from_os(self, browser_type=None):
                    if browser_type in (None, "google-chrome"):
                        return chrome.version
                    return super().get_browser_version_from_os(browser_type)

            os_manager = PinnedChromeOSManager()
            # valid_range=0 intentionally forces a fresh download for this explicit repair action.
            cache_manager = DriverCacheManager(valid_range=0, os_system_manager=os_manager)
            path = Path(
                ChromeDriverManager(
                    cache_manager=cache_manager,
                    os_system_manager=os_manager,
                ).install()
            ).resolve()
            driver_version = read_chromedriver_version(path)
        except BrowserRuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize network/cache/vendor failures
            logger.warning("下载 ChromeDriver 失败：%s", exc)
            raise ChromeDriverInstallError(
                "匹配驱动下载失败。请检查网络、代理或安全软件后重试；原有 Chrome 不会被修改。",
                chrome,
            ) from exc

        if not path.is_file() or not versions_are_compatible(chrome.version, driver_version):
            raise ChromeDriverInstallError(
                f"已下载驱动，但版本 {driver_version or '未知'} 与 Chrome {chrome.version} 不匹配。请更新 Chrome 后重试。",
                chrome,
            )

        driver = ChromeDriverInstallation(path=path, version=driver_version, source="webdriver-manager 缓存")
        logger.info("已为 Chrome %s 安装匹配驱动 %s", chrome.version, driver.version)
        return {
            "status": "installed",
            "browser": chrome.to_dict(),
            "driver": driver.to_dict(),
            "downloaded": True,
            "message": "匹配的 ChromeDriver 已下载并缓存，后续会直接复用。",
        }
