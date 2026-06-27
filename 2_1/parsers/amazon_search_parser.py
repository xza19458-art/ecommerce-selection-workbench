"""Robust parser for Amazon search result HTML.

The parser only emits records that are complete enough for database storage
and early product-selection scoring. Field names stay internal in English;
DISPLAY_FIELDS provides Chinese labels for UI/table output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


AMAZON_BASE_URL = "https://www.amazon.com"
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

DISPLAY_FIELDS = {
    "marketplace": "站点",
    "asin": "ASIN",
    "title": "商品标题",
    "title_zh": "商品标题中文",
    "title_lang": "商品标题语言",
    "title_translation_status": "商品标题翻译状态",
    "title_translation_engine": "商品标题翻译引擎",
    "title_translated_at": "商品标题翻译时间",
    "brand": "品牌",
    "category_path": "类目路径",
    "product_url": "商品链接",
    "image_url": "主图链接",
    "price": "价格",
    "rating": "评分",
    "review_count": "评论数",
    "monthly_bought": "近月购买量",
    "is_deal": "是否促销",
    "is_sponsored": "是否广告",
    "page_no": "页码",
    "organic_rank": "自然排名",
    "snapshot_at": "采集时间",
    "source_file": "来源文件",
    "quality_status": "数据质量",
}

# 入库完整性必填字段。monthly_bought（近月购买量）**不在**此列：很多正常 listing
# 本就没有 "X bought in past month" 徽标，100% 必填会误删真实商品、甚至把整页判为
# 有效=0 触发"空页即停"。改为最佳努力解析、缺失存 NULL（scoring._score_demand 已对
# None 返回 0，安全）。决策见 decisions/2026-06-24-monthly_bought-必填口径裁定.md。
STORAGE_REQUIRED_FIELDS = (
    "asin",
    "title",
    "product_url",
    "image_url",
    "price",
    "rating",
    "review_count",
)


@dataclass
class AmazonProductRecord:
    marketplace: str
    asin: str
    title: str | None
    product_url: str | None
    image_url: str | None
    price: float | None
    rating: float | None
    review_count: int | None
    monthly_bought: int | None
    is_deal: bool
    is_sponsored: bool
    page_no: int | None
    organic_rank: int | None
    snapshot_at: datetime
    source_file: str | None = None
    brand: str | None = None
    category_path: str | None = None
    title_zh: str | None = None
    title_lang: str | None = None
    title_translation_status: str | None = None
    title_translation_engine: str | None = None
    title_translated_at: datetime | None = None
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def is_valid_for_storage(self) -> bool:
        return not self.reject_reasons

    def to_storage_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["snapshot_at"] = self.snapshot_at.isoformat(sep=" ")
        data["quality_status"] = "完整" if self.is_valid_for_storage else "不完整"
        return data

    def to_chinese_dict(self) -> dict[str, Any]:
        data = self.to_storage_dict()
        return {DISPLAY_FIELDS.get(key, key): value for key, value in data.items() if key != "reject_reasons"}


@dataclass
class ParseResult:
    records: list[AmazonProductRecord]
    rejected_records: list[AmazonProductRecord]

    @property
    def total_found(self) -> int:
        return len(self.records) + len(self.rejected_records)

    @property
    def total_valid(self) -> int:
        return len(self.records)


def parse_amazon_search_html(
    html_path: str | Path,
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    require_complete: bool = True,
) -> ParseResult:
    path = Path(html_path)
    html = path.read_text(encoding="utf-8", errors="ignore")
    return parse_amazon_search_content(
        html,
        source_file=str(path),
        keyword=keyword,
        marketplace=marketplace,
        snapshot_at=snapshot_at,
        require_complete=require_complete,
    )


def parse_amazon_search_content(
    html: str,
    *,
    source_file: str | None = None,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    require_complete: bool = True,
) -> ParseResult:
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    snapshot = _normalize_snapshot_time(snapshot_at)
    page_no = _extract_page_no(soup)
    records: list[AmazonProductRecord] = []
    rejected: list[AmazonProductRecord] = []
    seen_asins: set[str] = set()

    for item in _iter_product_items(soup):
        record = _parse_item(
            item,
            marketplace=marketplace,
            snapshot_at=snapshot,
            source_file=source_file,
            page_no=page_no,
        )
        if record.asin in seen_asins:
            record.reject_reasons.append("重复ASIN")
        else:
            seen_asins.add(record.asin)

        _validate_record(record, require_complete=require_complete)
        if record.is_valid_for_storage:
            records.append(record)
        else:
            rejected.append(record)

    return ParseResult(records=records, rejected_records=rejected)


def parse_count(text: str | None) -> int | None:
    if not text:
        return None
    normalized = (
        text.replace(",", "")
        .replace("+", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm]?)", normalized)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


def clean_price(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def extract_monthly_bought_text(text: str | None) -> int | None:
    if not text:
        return None
    patterns = [
        r"(\d+(?:\.\d+)?\s*[KkMm]?\+?)\s+bought\s+in\s+past\s+month",
        r"(\d+(?:\.\d+)?\s*[KkMm]?\+?)\s+bought\s+in\s+the\s+past\s+month",
        r"(\d+(?:\.\d+)?\s*[KkMm]?\+?)\s+sold\s+in\s+past\s+month",
        r"(\d+(?:\.\d+)?\s*[KkMm]?\+?)\s+sold\s+this\s+month",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_count(match.group(1))
    return None


def _iter_product_items(soup: BeautifulSoup) -> list[Tag]:
    items: list[Tag] = []
    for item in soup.select("div[data-component-type='s-search-result'][data-asin]"):
        asin = (item.get("data-asin") or "").strip()
        if asin:
            items.append(item)
    return items


def _parse_item(
    item: Tag,
    *,
    marketplace: str,
    snapshot_at: datetime,
    source_file: str | None,
    page_no: int | None,
) -> AmazonProductRecord:
    asin = (item.get("data-asin") or "").strip().upper()
    title = _text_first(item, ["h2 span", "h2 a span", "h2"])
    product_url = _extract_product_url(item)
    image_url = _extract_image_url(item)
    price = _extract_price(item)
    rating = _extract_rating(item)
    review_count = _extract_review_count(item)
    monthly_bought = _extract_monthly_bought(item)
    organic_rank = _extract_rank(item)
    is_sponsored = _is_sponsored(item)
    is_deal = bool(item.select_one(".a-badge-text, .s-label-popover-default, [aria-label*='deal' i]"))

    return AmazonProductRecord(
        marketplace=marketplace,
        asin=asin,
        title=title,
        product_url=product_url,
        image_url=image_url,
        price=price,
        rating=rating,
        review_count=review_count,
        monthly_bought=monthly_bought,
        is_deal=is_deal,
        is_sponsored=is_sponsored,
        page_no=page_no,
        organic_rank=organic_rank,
        snapshot_at=snapshot_at,
        source_file=source_file,
    )


def _validate_record(record: AmazonProductRecord, *, require_complete: bool) -> None:
    if not ASIN_RE.match(record.asin or ""):
        record.reject_reasons.append("ASIN无效")
    if record.is_sponsored:
        record.reject_reasons.append("广告商品")
    if require_complete:
        for field_name in STORAGE_REQUIRED_FIELDS:
            value = getattr(record, field_name)
            if value is None or value == "":
                record.reject_reasons.append(f"缺少{DISPLAY_FIELDS.get(field_name, field_name)}")
    if record.price is not None and record.price <= 0:
        record.reject_reasons.append("价格无效")
    if record.rating is not None and not (0 < record.rating <= 5):
        record.reject_reasons.append("评分无效")
    if record.review_count is not None and record.review_count < 0:
        record.reject_reasons.append("评论数无效")
    if record.monthly_bought is not None and record.monthly_bought <= 0:
        record.reject_reasons.append("近月购买量无效")


def _extract_product_url(item: Tag) -> str | None:
    link = item.select_one("h2 a[href]") or item.select_one("a.a-link-normal.s-no-outline[href]")
    if not link:
        return None
    href = link.get("href")
    if not href:
        return None
    absolute = urljoin(AMAZON_BASE_URL, href)
    parsed = urlparse(absolute)
    clean_path = parsed.path.split("/ref=")[0]
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}"


def _extract_image_url(item: Tag) -> str | None:
    image = item.select_one("img.s-image[src]")
    if not image:
        return None
    return image.get("src")


def _extract_price(item: Tag) -> float | None:
    for selector in [".a-price .a-offscreen", ".a-price-whole", "[data-a-color='price'] .a-offscreen"]:
        node = item.select_one(selector)
        price = clean_price(_node_text(node))
        if price is not None:
            return price
    return None


def _extract_rating(item: Tag) -> float | None:
    candidates = []
    rating_icon = item.select_one(".a-icon-alt")
    if rating_icon:
        candidates.append(_node_text(rating_icon))
    for node in item.select("[aria-label*='out of 5 stars' i]"):
        candidates.append(node.get("aria-label", ""))
    for text in candidates:
        match = re.search(r"([0-5](?:\.\d)?)\s+out\s+of\s+5", text or "", re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _extract_review_count(item: Tag) -> int | None:
    selectors = [
        "a[href*='customerReviews'] span.a-size-base",
        "a[href*='customerReviews'] span",
        "span.a-size-base.s-underline-text",
    ]
    for selector in selectors:
        for node in item.select(selector):
            count = parse_count(_node_text(node))
            if count is not None:
                return count
    for node in item.select("[aria-label]"):
        label = node.get("aria-label", "")
        if re.search(r"\d", label) and "star" not in label.lower():
            count = parse_count(label)
            if count is not None:
                return count
    return None


def _extract_monthly_bought(item: Tag) -> int | None:
    for node in item.select(".a-size-base.a-color-secondary, .a-size-small.a-color-secondary, span"):
        value = extract_monthly_bought_text(_node_text(node))
        if value is not None:
            return value
    value = extract_monthly_bought_text(item.get_text(" ", strip=True))
    return value


def _extract_rank(item: Tag) -> int | None:
    for attr in ("data-index", "data-cel-widget"):
        value = item.get(attr)
        if not value:
            continue
        match = re.search(r"(\d+)", str(value))
        if match:
            return int(match.group(1))
    return None


def _extract_page_no(soup: BeautifulSoup) -> int | None:
    selected = soup.select_one(".s-pagination-selected")
    if selected:
        count = parse_count(selected.get_text(" ", strip=True))
        if count:
            return count
    return None


def _is_sponsored(item: Tag) -> bool:
    if "AdHolder" in (item.get("class") or []):
        return True
    text = item.get_text(" ", strip=True)
    if re.search(r"\bSponsored\b", text, re.IGNORECASE):
        return True
    return bool(item.select_one(".puis-sponsored-label-text, [aria-label='Sponsored']"))


def _text_first(item: Tag, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = item.select_one(selector)
        text = _node_text(node)
        if text:
            return _clean_spaces(text)
    return None


def _node_text(node: Tag | None) -> str | None:
    if node is None:
        return None
    return _clean_spaces(node.get_text(" ", strip=True))


def _clean_spaces(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _normalize_snapshot_time(snapshot_at: datetime | None) -> datetime:
    value = snapshot_at or datetime.now()
    return value.replace(minute=0, second=0, microsecond=0)
