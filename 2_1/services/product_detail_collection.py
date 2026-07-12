"""User-triggered, one-page Amazon product-detail collection."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import random
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from database.mysql_client import MySQLClient
from parsers.amazon_detail_parser import AmazonDetailRecord, parse_amazon_detail_content
from services.amazon_urls import amazon_product_url, normalize_asin


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
DETAIL_HTML_ROOT = ROOT / "html" / "_details"
BLOCKED_HTML_ROOT = ROOT / "html" / "_blocked" / "details"

_BLOCK_MARKERS = (
    "sorry, we just need to make sure you're not a robot",
    "enter the characters you see below",
    "type the characters you see in this image",
    "automated access to amazon data",
)


class ProductDetailCollectionError(RuntimeError):
    """Raised when a detail page cannot be trusted for persistence."""


def collect_product_detail(
    asin: str,
    browser: Any,
    *,
    client: MySQLClient | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Collect one product page and persist only fields actually found."""
    normalized_asin = normalize_asin(asin)
    db = client or MySQLClient()
    product = _fetch_product(db, normalized_asin)
    if not product:
        raise ProductDetailCollectionError(f"商品库中不存在 ASIN：{normalized_asin}")

    collected_at = (now or datetime.now()).replace(microsecond=0)
    target_url = amazon_product_url(
        normalized_asin,
        marketplace=str(product.get("marketplace") or "US"),
        source_url=product.get("product_url"),
    )
    job_id = _try_create_job(db, normalized_asin, target_url)
    job_finished = False

    try:
        browser.get(target_url)
        _wait_until_loaded(browser)
        _scroll_product_details(browser, sleep=sleep)
        html = str(getattr(browser, "page_source", "") or "")
        current_url = str(getattr(browser, "current_url", target_url) or target_url)
        page_title = str(getattr(browser, "title", "") or "")
        base = urlsplit(current_url)
        base_url = f"{base.scheme or 'https'}://{base.netloc}" if base.netloc else "https://www.amazon.com"
        record = parse_amazon_detail_content(
            html,
            expected_asin=normalized_asin,
            base_url=base_url,
        )
        state, reason = classify_amazon_detail_page(
            html,
            current_url=current_url,
            title=page_title,
            record=record,
        )
        if state != "valid":
            blocked_path = _save_detail_html(
                html,
                normalized_asin,
                collected_at,
                blocked=True,
                suffix=state,
            )
            message = f"详情页未通过有效性检查：{reason or '页面不可用'}；已保留 HTML：{blocked_path}"
            _try_finish_job(db, job_id, "失败", error_message=message)
            job_finished = True
            raise ProductDetailCollectionError(message)

        if record.asin and record.asin != normalized_asin:
            blocked_path = _save_detail_html(
                html,
                normalized_asin,
                collected_at,
                blocked=True,
                suffix="asin_mismatch",
            )
            message = (
                f"详情页 ASIN 与目标不一致（目标 {normalized_asin}，页面 {record.asin}），"
                f"未写库；已保留 HTML：{blocked_path}"
            )
            _try_finish_job(db, job_id, "失败", error_message=message)
            job_finished = True
            raise ProductDetailCollectionError(message)

        source_file = _save_detail_html(html, normalized_asin, collected_at)
        _persist_detail(
            db,
            product_id=int(product["id"]),
            record=record,
            collected_at=collected_at,
            source_file=source_file,
        )
        _try_finish_job(db, job_id, "完成")
        job_finished = True

        missing = []
        if record.date_first_available is None:
            missing.append("首次上架日期")
        if not record.category_path:
            missing.append("商品类别")
        if not record.best_seller_ranks:
            missing.append("热销榜排名")
        found_count = 3 - len(missing)
        return {
            "状态": "完成",
            "ASIN": normalized_asin,
            "商品类别": record.category_path,
            "首次上架日期": record.date_first_available.isoformat() if record.date_first_available else None,
            "热销榜排名": [
                {
                    "排名": item.rank,
                    "类目": item.category_name,
                    "类目链接": item.category_url,
                    "主类目": item.is_primary,
                }
                for item in record.best_seller_ranks
            ],
            "已采集字段数": found_count,
            "未采集字段": missing,
            "详情采集时间": collected_at.isoformat(sep=" "),
            "来源文件": source_file,
            "message": (
                "详情页有效，已写入当前页面可见字段。"
                if not missing
                else f"详情页有效；{ '、'.join(missing) }未在当前页面出现，保留为未采集。"
            ),
        }
    except Exception as exc:
        if not job_finished:
            _try_finish_job(db, job_id, "失败", error_message=str(exc))
        raise


def classify_amazon_detail_page(
    html: str,
    *,
    current_url: str = "",
    title: str = "",
    record: AmazonDetailRecord | None = None,
) -> tuple[str, str | None]:
    """Reject blocked, sign-in, empty, and non-product pages before storage."""
    lower_url = current_url.lower()
    lower_title = title.lower()
    lower_html = (html or "").lower()
    if not html.strip():
        return "empty", "页面 HTML 为空"
    if "/errors/validatecaptcha" in lower_url or "captcha" in lower_url:
        return "blocked", "Amazon 返回验证码页面"
    if "/ap/signin" in lower_url or "amazon sign-in" in lower_title:
        return "blocked", "Amazon 要求登录"
    if "robot check" in lower_title or any(marker in lower_html for marker in _BLOCK_MARKERS):
        return "blocked", "Amazon 触发机器人验证"
    parsed = record or parse_amazon_detail_content(html)
    if not parsed.title:
        return "invalid", "未找到商品标题，无法确认是有效商品详情页"
    return "valid", None


def _fetch_product(db: MySQLClient, asin: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_product_detail_schema(cursor)
            cursor.execute(
                """
                SELECT id, marketplace, asin, product_url
                FROM products
                WHERE asin = %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (asin,),
            )
            return cursor.fetchone()


def _persist_detail(
    db: MySQLClient,
    *,
    product_id: int,
    record: AmazonDetailRecord,
    collected_at: datetime,
    source_file: str,
) -> None:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_product_detail_schema(cursor)
            cursor.execute(
                """
                UPDATE products
                SET category_path = CASE
                      WHEN %s IS NULL OR %s = '' THEN category_path
                      WHEN category_path IS NULL OR category_path = '' THEN %s
                      WHEN CHAR_LENGTH(%s) >= CHAR_LENGTH(category_path) THEN %s
                      ELSE category_path
                    END,
                    date_first_available = COALESCE(%s, date_first_available),
                    detail_collected_at = %s,
                    detail_source_file = %s
                WHERE id = %s
                """,
                (
                    record.category_path,
                    record.category_path,
                    record.category_path,
                    record.category_path,
                    record.category_path,
                    record.date_first_available,
                    collected_at,
                    source_file,
                    product_id,
                ),
            )
            for rank in record.best_seller_ranks:
                cursor.execute(
                    """
                    INSERT INTO product_bsr_snapshots (
                      product_id, snapshot_at, rank_value, category_name,
                      category_url, is_primary, raw_text, source_file
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      rank_value = VALUES(rank_value),
                      category_url = COALESCE(VALUES(category_url), category_url),
                      is_primary = VALUES(is_primary),
                      raw_text = VALUES(raw_text),
                      source_file = VALUES(source_file)
                    """,
                    (
                        product_id,
                        collected_at,
                        rank.rank,
                        rank.category_name,
                        rank.category_url,
                        int(rank.is_primary),
                        rank.raw_text,
                        source_file,
                    ),
                )


def _wait_until_loaded(browser: Any) -> None:
    try:
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(browser, 20).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        logger.info("详情页等待 document.readyState 超时，继续做页面有效性检查。", exc_info=True)


def _scroll_product_details(browser: Any, *, sleep: Callable[[float], None]) -> None:
    try:
        browser.execute_script(
            "window.scrollTo(0, Math.min(document.body.scrollHeight * 0.65, 4800));"
        )
        sleep(random.uniform(1.2, 2.0))
        selectors = (
            "#productDetails_detailBullets_sections1",
            "#detailBullets_feature_div",
            "#prodDetails",
        )
        for selector in selectors:
            elements = browser.find_elements("css selector", selector)
            if elements:
                browser.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    elements[0],
                )
                sleep(random.uniform(1.0, 1.8))
                break
    except Exception:
        logger.info("详情区滚动未完成，仍使用当前完整页面 HTML 解析。", exc_info=True)


def _save_detail_html(
    html: str,
    asin: str,
    collected_at: datetime,
    *,
    blocked: bool = False,
    suffix: str = "detail",
) -> str:
    root = BLOCKED_HTML_ROOT if blocked else DETAIL_HTML_ROOT
    output_dir = root / asin
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_suffix = re.sub(r"[^a-z0-9_-]+", "_", suffix.lower()).strip("_") or "detail"
    output = output_dir / f"{asin}_{collected_at.strftime('%Y%m%d_%H%M%S')}_{safe_suffix}.html"
    output.write_text(html, encoding="utf-8")
    return output.relative_to(ROOT).as_posix()


def _try_create_job(db: MySQLClient, asin: str, url: str) -> int | None:
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                return db.create_job(cursor, f"详情:{asin}", url, 1)
    except Exception:
        logger.warning("详情采集任务日志创建失败，继续执行。", exc_info=True)
        return None


def _try_finish_job(
    db: MySQLClient,
    job_id: int | None,
    status: str,
    *,
    error_message: str | None = None,
) -> None:
    if job_id is None:
        return
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                db.finish_job(
                    cursor,
                    job_id,
                    status,
                    total_found=1,
                    total_valid=1 if status == "完成" else 0,
                    total_inserted=0,
                    error_message=error_message,
                )
    except Exception:
        logger.warning("详情采集任务日志更新失败。", exc_info=True)
