"""User-triggered, one-page Amazon product-detail collection."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import logging
import random
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from database.mysql_client import MySQLClient
from parsers.amazon_detail_parser import AmazonDetailRecord, parse_amazon_detail_content
from pkg_paths import user_data_path
from services.amazon_urls import amazon_product_url, normalize_asin


logger = logging.getLogger(__name__)
ROOT = user_data_path()
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
            marketplace=str(product.get("marketplace") or "US"),
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
        if record.offer.current_price is None:
            missing.append("详情页价格")
        if record.offer.availability_status is None:
            missing.append("库存状态")
        if not record.physical_specs.has_data:
            missing.append("结构化物理规格")
        found_count = 6 - len(missing)
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
            "报价与履约": asdict(record.offer),
            "详情时序字段": {
                "价格": record.offer.current_price,
                "评分": record.offer.rating,
                "评论数": record.offer.review_count,
                "近月购买量": record.offer.monthly_bought,
            },
            "物理规格": asdict(record.physical_specs),
            "变体数量": len(record.variants),
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


def persist_detail_record(
    record: AmazonDetailRecord,
    *,
    collected_at: datetime,
    source_file: str,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Persist an already validated detail record using normal detail semantics."""
    normalized_asin = normalize_asin(record.asin or "")
    db = client or MySQLClient()
    product = _fetch_product(db, normalized_asin)
    if not product:
        raise ProductDetailCollectionError(f"商品库中不存在 ASIN：{normalized_asin}")
    _persist_detail(
        db,
        product_id=int(product["id"]),
        marketplace=str(product.get("marketplace") or "US"),
        record=record,
        collected_at=collected_at.replace(microsecond=0),
        source_file=source_file,
    )
    return product


def _persist_detail(
    db: MySQLClient,
    *,
    product_id: int,
    marketplace: str,
    record: AmazonDetailRecord,
    collected_at: datetime,
    source_file: str,
) -> None:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE products
                SET category_path = CASE
                      WHEN %s IS NULL OR %s = '' THEN category_path
                      WHEN category_path IS NULL OR category_path = '' THEN %s
                      WHEN (detail_collected_at IS NULL OR %s >= detail_collected_at)
                           AND CHAR_LENGTH(%s) >= CHAR_LENGTH(category_path) THEN %s
                      ELSE category_path
                    END,
                    date_first_available = COALESCE(%s, date_first_available),
                    detail_source_file = CASE
                      WHEN detail_collected_at IS NULL OR %s >= detail_collected_at THEN %s
                      ELSE detail_source_file
                    END,
                    detail_collected_at = CASE
                      WHEN detail_collected_at IS NULL OR %s >= detail_collected_at THEN %s
                      ELSE detail_collected_at
                    END
                WHERE id = %s
                """,
                (
                    record.category_path,
                    record.category_path,
                    record.category_path,
                    collected_at,
                    record.category_path,
                    record.category_path,
                    record.date_first_available,
                    collected_at,
                    source_file,
                    collected_at,
                    collected_at,
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
            _upsert_physical_specs(
                cursor,
                product_id=product_id,
                record=record,
                collected_at=collected_at,
                source_file=source_file,
            )
            _upsert_offer_snapshot(
                cursor,
                product_id=product_id,
                record=record,
                collected_at=collected_at,
                source_file=source_file,
            )
            _upsert_variants(
                cursor,
                product_id=product_id,
                marketplace=marketplace,
                record=record,
                collected_at=collected_at,
                source_file=source_file,
            )


def _upsert_physical_specs(
    cursor: Any,
    *,
    product_id: int,
    record: AmazonDetailRecord,
    collected_at: datetime,
    source_file: str,
) -> None:
    specs = record.physical_specs
    if record.asin:
        cursor.execute(
            """
            UPDATE product_physical_specs
            SET parent_asin = NULL
            WHERE product_id = %s AND parent_asin = %s
            """,
            (product_id, record.asin),
        )
    if not specs.has_data:
        return
    cursor.execute(
        """
        INSERT INTO product_physical_specs (
          product_id, parent_asin,
          item_length_in, item_width_in, item_height_in,
          package_length_in, package_width_in, package_height_in,
          item_weight_oz, package_weight_oz, unit_count, model_number,
          raw_dimensions_json, raw_weight_json, source_file, collected_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          parent_asin = CASE WHEN parent_asin IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(parent_asin), parent_asin) ELSE parent_asin END,
          item_length_in = CASE WHEN item_length_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(item_length_in), item_length_in) ELSE item_length_in END,
          item_width_in = CASE WHEN item_width_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(item_width_in), item_width_in) ELSE item_width_in END,
          item_height_in = CASE WHEN item_height_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(item_height_in), item_height_in) ELSE item_height_in END,
          package_length_in = CASE WHEN package_length_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(package_length_in), package_length_in) ELSE package_length_in END,
          package_width_in = CASE WHEN package_width_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(package_width_in), package_width_in) ELSE package_width_in END,
          package_height_in = CASE WHEN package_height_in IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(package_height_in), package_height_in) ELSE package_height_in END,
          item_weight_oz = CASE WHEN item_weight_oz IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(item_weight_oz), item_weight_oz) ELSE item_weight_oz END,
          package_weight_oz = CASE WHEN package_weight_oz IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(package_weight_oz), package_weight_oz) ELSE package_weight_oz END,
          unit_count = CASE WHEN unit_count IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(unit_count), unit_count) ELSE unit_count END,
          model_number = CASE WHEN model_number IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(model_number), model_number) ELSE model_number END,
          raw_dimensions_json = CASE WHEN raw_dimensions_json IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(raw_dimensions_json), raw_dimensions_json) ELSE raw_dimensions_json END,
          raw_weight_json = CASE WHEN raw_weight_json IS NULL OR VALUES(collected_at) >= collected_at THEN COALESCE(VALUES(raw_weight_json), raw_weight_json) ELSE raw_weight_json END,
          source_file = CASE WHEN VALUES(collected_at) >= collected_at THEN VALUES(source_file) ELSE source_file END,
          collected_at = GREATEST(collected_at, VALUES(collected_at))
        """,
        (
            product_id,
            specs.parent_asin,
            specs.item_length_in,
            specs.item_width_in,
            specs.item_height_in,
            specs.package_length_in,
            specs.package_width_in,
            specs.package_height_in,
            specs.item_weight_oz,
            specs.package_weight_oz,
            specs.unit_count,
            specs.model_number,
            _json_or_none(specs.raw_dimensions),
            _json_or_none(specs.raw_weights),
            source_file,
            collected_at,
        ),
    )


def _upsert_offer_snapshot(
    cursor: Any,
    *,
    product_id: int,
    record: AmazonDetailRecord,
    collected_at: datetime,
    source_file: str,
) -> None:
    offer = record.offer
    if not offer.has_data:
        return
    cursor.execute(
        """
        INSERT INTO product_offer_snapshots (
          product_id, snapshot_at, current_price, list_price, currency,
          discount_percent, coupon_text, availability_status,
          featured_offer_seller, ships_from, fulfillment_channel, is_prime,
          offer_count, badges_json, image_count, video_count, bullet_count,
          has_a_plus, rating_histogram_json, postal_code, source_file, raw_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          current_price = VALUES(current_price),
          list_price = VALUES(list_price),
          currency = VALUES(currency),
          discount_percent = VALUES(discount_percent),
          coupon_text = VALUES(coupon_text),
          availability_status = VALUES(availability_status),
          featured_offer_seller = VALUES(featured_offer_seller),
          ships_from = VALUES(ships_from),
          fulfillment_channel = VALUES(fulfillment_channel),
          is_prime = VALUES(is_prime),
          offer_count = VALUES(offer_count),
          badges_json = VALUES(badges_json),
          image_count = VALUES(image_count),
          video_count = VALUES(video_count),
          bullet_count = VALUES(bullet_count),
          has_a_plus = VALUES(has_a_plus),
          rating_histogram_json = VALUES(rating_histogram_json),
          postal_code = VALUES(postal_code),
          source_file = VALUES(source_file),
          raw_json = VALUES(raw_json)
        """,
        (
            product_id,
            collected_at,
            offer.current_price,
            offer.list_price,
            offer.currency,
            offer.discount_percent,
            offer.coupon_text,
            offer.availability_status,
            offer.featured_offer_seller,
            offer.ships_from,
            offer.fulfillment_channel,
            None if offer.is_prime is None else int(offer.is_prime),
            offer.offer_count,
            _json_or_none(list(offer.badges)),
            offer.image_count,
            offer.video_count,
            offer.bullet_count,
            None if offer.has_a_plus is None else int(offer.has_a_plus),
            _json_or_none(offer.rating_histogram),
            offer.postal_code,
            source_file,
            _json_or_none(_offer_raw_payload(offer)),
        ),
    )


def _offer_raw_payload(offer: Any) -> dict[str, Any]:
    payload = dict(offer.raw_values or {})
    payload["detail_page_metrics"] = {
        "rating": offer.rating,
        "review_count": offer.review_count,
        "monthly_bought": offer.monthly_bought,
        "is_deal": _offer_is_deal(offer),
    }
    payload["capture_source"] = "product_detail"
    return payload


def _offer_is_deal(offer: Any) -> bool:
    if offer.discount_percent is not None and float(offer.discount_percent) > 0:
        return True
    if offer.coupon_text:
        return True
    if (
        offer.current_price is not None
        and offer.list_price is not None
        and float(offer.current_price) < float(offer.list_price)
    ):
        return True
    return any(
        marker in str(badge or "").casefold()
        for badge in offer.badges
        for marker in ("deal", "limited time", "prime exclusive")
    )


def _upsert_variants(
    cursor: Any,
    *,
    product_id: int,
    marketplace: str,
    record: AmazonDetailRecord,
    collected_at: datetime,
    source_file: str,
) -> None:
    if not record.variants:
        return
    parent_asin = record.physical_specs.parent_asin or record.asin
    if not parent_asin:
        return
    for variant in record.variants:
        cursor.execute(
            """
            INSERT INTO product_variants (
              source_product_id, marketplace, parent_asin, child_asin,
              attributes_json, product_url, is_selected,
              first_seen_at, last_seen_at, source_file
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          source_product_id = VALUES(source_product_id),
          attributes_json = CASE WHEN attributes_json IS NULL OR VALUES(last_seen_at) >= last_seen_at THEN COALESCE(VALUES(attributes_json), attributes_json) ELSE attributes_json END,
          product_url = CASE WHEN product_url IS NULL OR VALUES(last_seen_at) >= last_seen_at THEN COALESCE(VALUES(product_url), product_url) ELSE product_url END,
          is_selected = CASE WHEN VALUES(last_seen_at) >= last_seen_at THEN VALUES(is_selected) ELSE is_selected END,
          first_seen_at = LEAST(first_seen_at, VALUES(first_seen_at)),
          source_file = CASE WHEN VALUES(last_seen_at) >= last_seen_at THEN VALUES(source_file) ELSE source_file END,
          last_seen_at = GREATEST(last_seen_at, VALUES(last_seen_at))
            """,
            (
                product_id,
                marketplace,
                parent_asin,
                variant.child_asin,
                _json_or_none(variant.attributes),
                variant.product_url,
                int(variant.is_selected),
                collected_at,
                collected_at,
                source_file,
            ),
        )


def _json_or_none(value: Any) -> str | None:
    if value in (None, {}, [], ()):
        return None
    return json.dumps(value, ensure_ascii=False)


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
