from __future__ import annotations

from datetime import datetime
import json

import pytest

from parsers.amazon_detail_parser import parse_amazon_detail_content
import services.product_detail_collection as detail_collection


ASIN = "B0DQYSR28D"


class _FakeBrowser:
    def __init__(self, *, html: str = "", current_url: str = "", title: str = "", error=None) -> None:
        self.page_source = html
        self.current_url = current_url
        self.title = title
        self.error = error
        self.visited: list[str] = []

    def get(self, url: str) -> None:
        self.visited.append(url)
        if self.error is not None:
            raise self.error


def _product() -> dict:
    return {
        "id": 11,
        "marketplace": "US",
        "asin": ASIN,
        "product_url": f"https://www.amazon.com/dp/{ASIN}",
    }


def _patch_browser_helpers(monkeypatch) -> None:
    monkeypatch.setattr(detail_collection, "_wait_until_loaded", lambda _browser: None)
    monkeypatch.setattr(detail_collection, "_scroll_product_details", lambda _browser, sleep: None)


def test_unknown_product_is_rejected_before_browser_navigation(monkeypatch) -> None:
    browser = _FakeBrowser()
    monkeypatch.setattr(detail_collection, "_fetch_product", lambda _db, _asin: None)
    monkeypatch.setattr(
        detail_collection,
        "_try_create_job",
        lambda *_args: pytest.fail("unknown product must not create a crawl job"),
    )

    with pytest.raises(detail_collection.ProductDetailCollectionError, match="商品库中不存在"):
        detail_collection.collect_product_detail(ASIN, browser, client=object())

    assert browser.visited == []


def test_blocked_detail_page_is_quarantined_without_business_persistence(monkeypatch) -> None:
    browser = _FakeBrowser(
        html="<html><body>Sorry, we just need to make sure you're not a robot</body></html>",
        current_url="https://www.amazon.com/errors/validateCaptcha",
        title="Robot Check",
    )
    saved: list[tuple[bool, str]] = []
    finished: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(detail_collection, "_fetch_product", lambda _db, _asin: _product())
    monkeypatch.setattr(detail_collection, "_try_create_job", lambda *_args: 77)
    monkeypatch.setattr(
        detail_collection,
        "_try_finish_job",
        lambda _db, job_id, status, *, error_message=None: finished.append(
            (job_id, status, error_message)
        ),
    )
    monkeypatch.setattr(
        detail_collection,
        "_save_detail_html",
        lambda _html, _asin, _at, *, blocked=False, suffix="detail": (
            saved.append((blocked, suffix)) or f"html/_blocked/details/{ASIN}/blocked.html"
        ),
    )
    monkeypatch.setattr(
        detail_collection,
        "_persist_detail",
        lambda *_args, **_kwargs: pytest.fail("blocked HTML must not be persisted"),
    )
    _patch_browser_helpers(monkeypatch)

    with pytest.raises(detail_collection.ProductDetailCollectionError, match="验证码"):
        detail_collection.collect_product_detail(
            ASIN,
            browser,
            client=object(),
            now=datetime(2026, 7, 19, 12),
            sleep=lambda _seconds: None,
        )

    assert saved == [(True, "blocked")]
    assert len(finished) == 1
    assert finished[0][0:2] == (77, "失败")
    assert "已保留 HTML" in str(finished[0][2])


def test_mismatched_asin_is_quarantined_without_persistence(monkeypatch) -> None:
    other_asin = "B0BADASIN1"
    browser = _FakeBrowser(
        html=(
            f'<input id="ASIN" value="{other_asin}">'
            '<span id="productTitle">Wrong product</span>'
        ),
        current_url=f"https://www.amazon.com/dp/{other_asin}",
        title="Wrong product",
    )
    saved: list[tuple[bool, str]] = []
    monkeypatch.setattr(detail_collection, "_fetch_product", lambda _db, _asin: _product())
    monkeypatch.setattr(detail_collection, "_try_create_job", lambda *_args: 78)
    monkeypatch.setattr(detail_collection, "_try_finish_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        detail_collection,
        "_save_detail_html",
        lambda _html, _asin, _at, *, blocked=False, suffix="detail": (
            saved.append((blocked, suffix)) or "html/_blocked/details/mismatch.html"
        ),
    )
    monkeypatch.setattr(
        detail_collection,
        "_persist_detail",
        lambda *_args, **_kwargs: pytest.fail("mismatched ASIN must not be persisted"),
    )
    _patch_browser_helpers(monkeypatch)

    with pytest.raises(detail_collection.ProductDetailCollectionError, match="ASIN 与目标不一致"):
        detail_collection.collect_product_detail(
            ASIN,
            browser,
            client=object(),
            now=datetime(2026, 7, 19, 12),
            sleep=lambda _seconds: None,
        )

    assert saved == [(True, "asin_mismatch")]


def test_valid_local_detail_html_reaches_normal_persistence(monkeypatch) -> None:
    html = f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Local fixture product</span>
      <div id="wayfinding-breadcrumbs_feature_div"><a>Toys &amp; Games</a></div>
      <table id="productDetails_detailBullets_sections1">
        <tr><th>Date First Available</th><td>May 17, 2024</td></tr>
        <tr><th>Best Sellers Rank</th><td>#12 in Toys &amp; Games</td></tr>
      </table>
    </body></html>
    """
    browser = _FakeBrowser(
        html=html,
        current_url=f"https://www.amazon.com/dp/{ASIN}",
        title="Local fixture product",
    )
    persisted: list[dict] = []
    finished: list[str] = []
    monkeypatch.setattr(detail_collection, "_fetch_product", lambda _db, _asin: _product())
    monkeypatch.setattr(detail_collection, "_try_create_job", lambda *_args: 79)
    monkeypatch.setattr(
        detail_collection,
        "_try_finish_job",
        lambda _db, _job_id, status, **_kwargs: finished.append(status),
    )
    monkeypatch.setattr(
        detail_collection,
        "_save_detail_html",
        lambda *_args, **_kwargs: f"html/_details/{ASIN}/fixture.html",
    )
    monkeypatch.setattr(
        detail_collection,
        "_persist_detail",
        lambda _db, **kwargs: persisted.append(kwargs),
    )
    _patch_browser_helpers(monkeypatch)

    result = detail_collection.collect_product_detail(
        ASIN,
        browser,
        client=object(),
        now=datetime(2026, 7, 19, 12),
        sleep=lambda _seconds: None,
    )

    assert result["状态"] == "完成"
    assert result["首次上架日期"] == "2024-05-17"
    assert result["已采集字段数"] == 3
    assert result["未采集字段"] == ["详情页价格", "库存状态", "结构化物理规格"]
    assert persisted[0]["product_id"] == 11
    assert persisted[0]["record"].asin == ASIN
    assert finished == ["完成"]


def test_browser_failure_finishes_job_once_without_saving_html(monkeypatch) -> None:
    browser = _FakeBrowser(error=RuntimeError("browser unavailable"))
    finished: list[tuple[str, str | None]] = []
    monkeypatch.setattr(detail_collection, "_fetch_product", lambda _db, _asin: _product())
    monkeypatch.setattr(detail_collection, "_try_create_job", lambda *_args: 80)
    monkeypatch.setattr(
        detail_collection,
        "_try_finish_job",
        lambda _db, _job_id, status, *, error_message=None: finished.append(
            (status, error_message)
        ),
    )
    monkeypatch.setattr(
        detail_collection,
        "_save_detail_html",
        lambda *_args, **_kwargs: pytest.fail("browser failure has no HTML to save"),
    )

    with pytest.raises(RuntimeError, match="browser unavailable"):
        detail_collection.collect_product_detail(ASIN, browser, client=object())

    assert finished == [("失败", "browser unavailable")]


def test_offer_snapshot_keeps_detail_page_time_series_metrics_in_raw_json() -> None:
    html = f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Observed product</span>
      <div id="corePrice_feature_div">
        <span class="a-price"><span class="a-offscreen">$15.99</span></span>
        <span class="savingsPercentage">-5%</span>
      </div>
      <span id="acrPopover">4.7 out of 5 stars</span>
      <span id="acrCustomerReviewText">(37)</span>
      <span id="social-proofing-faceout-title-tk_bought">400+ bought in past month</span>
    </body></html>
    """
    record = parse_amazon_detail_content(html)

    class Cursor:
        params = None

        def execute(self, _sql, params):
            self.params = params

    cursor = Cursor()
    detail_collection._upsert_offer_snapshot(
        cursor,
        product_id=11,
        record=record,
        collected_at=datetime(2026, 7, 29, 22, 55, 51),
        source_file=f"html/_details/{ASIN}/fixture.html",
    )

    payload = json.loads(cursor.params[-1])
    assert payload["capture_source"] == "product_detail"
    assert payload["detail_page_metrics"] == {
        "rating": 4.7,
        "review_count": 37,
        "monthly_bought": 400,
        "is_deal": True,
    }
