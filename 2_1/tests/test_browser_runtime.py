from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import browser_runtime
from core.browser_runtime import (
    ChromeDriverInstallation,
    ChromeDriverRequiredError,
    ChromeInstallation,
    expected_driver_family,
    select_compatible_driver,
    versions_are_compatible,
)


def test_chrome_115_plus_requires_matching_build_family() -> None:
    assert versions_are_compatible("149.0.7827.201", "149.0.7827.155") is True
    assert versions_are_compatible("149.0.7827.201", "149.0.7810.10") is False
    assert versions_are_compatible("149.0.7827.201", "148.0.7778.178") is False
    assert expected_driver_family("149.0.7827.201") == "149.0.7827.x"


def test_legacy_chrome_matches_driver_major_version() -> None:
    assert versions_are_compatible("114.0.5735.120", "114.0.5735.90") is True
    assert versions_are_compatible("114.0.5735.120", "113.0.5672.63") is False
    assert expected_driver_family("114.0.5735.120") == "114.x"


def test_select_compatible_driver_prefers_exact_then_highest_patch() -> None:
    drivers = [
        ChromeDriverInstallation(Path("old.exe"), "149.0.7827.155", "缓存"),
        ChromeDriverInstallation(Path("new.exe"), "149.0.7827.180", "缓存"),
        ChromeDriverInstallation(Path("exact.exe"), "149.0.7827.201", "PATH"),
        ChromeDriverInstallation(Path("wrong.exe"), "149.0.7810.99", "PATH"),
    ]

    selected = select_compatible_driver("149.0.7827.201", drivers)
    fallback = select_compatible_driver("149.0.7827.199", drivers[:2])

    assert selected is not None and selected.path == Path("exact.exe")
    assert fallback is not None and fallback.path == Path("new.exe")


def test_find_local_chrome_uses_discovered_binary_and_version() -> None:
    original_candidates = browser_runtime._chrome_candidate_paths
    original_read_version = browser_runtime._read_chrome_version
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            chrome_path = Path(tmp_dir) / "chrome.exe"
            chrome_path.touch()
            browser_runtime._chrome_candidate_paths = lambda: [chrome_path]
            browser_runtime._read_chrome_version = lambda _path: "149.0.7827.201"

            chrome = browser_runtime.find_local_chrome()

        assert chrome.path == chrome_path
        assert chrome.version == "149.0.7827.201"
    finally:
        browser_runtime._chrome_candidate_paths = original_candidates
        browser_runtime._read_chrome_version = original_read_version


def test_missing_chrome_does_not_offer_driver_only_download() -> None:
    original_candidates = browser_runtime._chrome_candidate_paths
    try:
        browser_runtime._chrome_candidate_paths = lambda: []

        try:
            browser_runtime.find_local_chrome()
        except browser_runtime.ChromeNotFoundError as error:
            assert error.code == "chrome_not_found"
            assert error.details["can_download_driver"] is False
        else:
            raise AssertionError("缺少 Chrome 时应返回结构化错误")
    finally:
        browser_runtime._chrome_candidate_paths = original_candidates


def test_cached_driver_version_is_read_from_cache_path_without_launching_every_binary() -> None:
    original_candidates = browser_runtime._driver_candidate_paths
    original_read_version = browser_runtime.read_chromedriver_version
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            driver_path = Path(tmp_dir) / "149.0.7827.155" / "chromedriver.exe"
            driver_path.parent.mkdir()
            driver_path.touch()
            browser_runtime._driver_candidate_paths = lambda: [(driver_path, "测试缓存")]
            browser_runtime.read_chromedriver_version = lambda _path: (_ for _ in ()).throw(
                AssertionError("缓存目录已有版本线索时不应启动驱动")
            )

            drivers = browser_runtime.discover_chromedrivers()

        assert len(drivers) == 1
        assert drivers[0].version == "149.0.7827.155"
    finally:
        browser_runtime._driver_candidate_paths = original_candidates
        browser_runtime.read_chromedriver_version = original_read_version


def test_missing_matching_driver_returns_downloadable_structured_error() -> None:
    chrome = ChromeInstallation(Path("chrome.exe"), "149.0.7827.201")
    old_driver = ChromeDriverInstallation(Path("chromedriver.exe"), "148.0.7778.178", "PATH")
    error = ChromeDriverRequiredError(chrome, [old_driver])

    assert error.code == "chrome_driver_required"
    assert error.details["can_download_driver"] is True
    assert error.details["expected_driver"] == "149.0.7827.x"
    assert error.details["detected_driver_versions"] == ["148.0.7778.178"]


if __name__ == "__main__":
    tests = [
        test_chrome_115_plus_requires_matching_build_family,
        test_legacy_chrome_matches_driver_major_version,
        test_select_compatible_driver_prefers_exact_then_highest_patch,
        test_find_local_chrome_uses_discovered_binary_and_version,
        test_missing_chrome_does_not_offer_driver_only_download,
        test_cached_driver_version_is_read_from_cache_path_without_launching_every_binary,
        test_missing_matching_driver_returns_downloadable_structured_error,
    ]
    for test in tests:
        test()
    print(f"browser runtime tests passed: {len(tests)}/{len(tests)}")
