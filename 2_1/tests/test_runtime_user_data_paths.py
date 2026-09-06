from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import api.app as api_app
import core.controller as controller_module
import services.analytics_warehouse as analytics_warehouse
import services.detail_reparse as detail_reparse
import services.llm_provider as llm_provider
import services.product_detail_collection as detail_collection
import services.settings as settings
import services.translation as translation
from core.controller import AppController
from pkg_paths import resolve_user_writable_path, user_data_path


def test_runtime_modules_use_writable_user_data_root() -> None:
    assert api_app._HTML_IMPORT_DIR == user_data_path("html")
    assert api_app._REVIEW_DIR == user_data_path("reviews")
    assert analytics_warehouse.CONFIG_PATH == user_data_path("config", "warehouse.json")
    assert analytics_warehouse.DEFAULT_ROOT_DIR == user_data_path("data_warehouse")
    assert settings.CONFIG_PATH == user_data_path("config", "settings.json")
    assert llm_provider.CONFIG_PATH == user_data_path("config", "agent.json")
    assert translation.CONFIG_PATH == user_data_path("config", "translation.json")
    assert translation.ARGOS_RUNTIME_ROOT == user_data_path(".argos")
    assert detail_collection.DETAIL_HTML_ROOT == user_data_path("html", "_details")
    assert detail_collection.BLOCKED_HTML_ROOT == user_data_path("html", "_blocked", "details")
    assert detail_reparse.DETAIL_ANALYSIS_CACHE_PATH == user_data_path(
        "cache", "detail_reparse_manifest.json"
    )


def test_relative_user_outputs_do_not_depend_on_working_directory(tmp_path: Path) -> None:
    relative = Path("数据结果") / "report.csv"
    assert resolve_user_writable_path(relative) == user_data_path("数据结果", "report.csv")
    assert resolve_user_writable_path(tmp_path / "absolute.csv") == tmp_path / "absolute.csv"


def test_manual_crawl_saves_under_user_data_root(monkeypatch, tmp_path: Path) -> None:
    import parsers.amazon_search_parser as search_parser
    import services.snapshot_collection_runner as snapshot_runner

    monkeypatch.setattr(
        controller_module,
        "user_data_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    monkeypatch.setattr(
        snapshot_runner,
        "classify_amazon_search_page",
        lambda *_args, **_kwargs: ("ok", None),
    )
    monkeypatch.setattr(
        search_parser,
        "parse_amazon_search_content",
        lambda *_args, **_kwargs: SimpleNamespace(total_found=2, total_valid=1),
    )

    controller = AppController()
    monkeypatch.setattr(controller, "_try_create_crawl_job", lambda *_args, **_kwargs: None)

    def collect(_url, *, pages, on_page, **_kwargs):
        assert pages == 1
        on_page(1, "<html>trusted search page</html>", _url, "Amazon.com : squishy")
        return []

    monkeypatch.setattr(controller, "collect_amazon_search_pages", collect)
    result = controller.run_keyword_crawl("squishy", pages=1)

    saved = result["页面"][0]["保存文件"]
    assert saved.startswith("html/squishy/")
    assert (tmp_path / saved).read_text(encoding="utf-8") == "<html>trusted search page</html>"
    assert result["保存目录"].startswith("html/squishy/")


def test_html_resolution_and_keyword_inference_use_user_data_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        controller_module,
        "user_data_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    controller = AppController()
    html_file = tmp_path / "html" / "squishy" / "batch" / "page.html"
    html_file.parent.mkdir(parents=True)
    html_file.write_text("<html></html>", encoding="utf-8")

    assert controller._resolve_html_file("html/squishy/batch/page.html") == html_file
    assert controller._resolve_html_file("squishy/batch/page.html") == html_file
    assert controller._infer_import_keyword([html_file]) == "squishy"
