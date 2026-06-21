"""Offline parser for saved Amazon review-page HTML.

This module does not request Amazon pages. It only parses local HTML that the
user has already saved, then emits rows compatible with the existing review
CSV/JSON import flow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


AMAZON_BASE_URL = "https://www.amazon.com"
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

REVIEW_DISPLAY_FIELDS = {
    "asin": "ASIN",
    "review_id": "评论ID",
    "rating": "评分",
    "title": "评论标题",
    "body": "评论正文",
    "review_at": "评论时间",
    "reviewer_name": "评论者",
    "verified_purchase": "是否验证购买",
    "helpful_votes": "有用票数",
    "variant_info": "变体信息",
    "source_url": "来源链接",
    "source_file": "来源文件",
}

REVIEW_EXPORT_FIELDS = (
    "asin",
    "review_id",
    "rating",
    "title",
    "body",
    "review_at",
    "reviewer_name",
    "verified_purchase",
    "helpful_votes",
    "variant_info",
    "source_url",
    "source_file",
)


@dataclass
class AmazonReviewRecord:
    asin: str | None
    review_id: str | None
    rating: float | None
    title: str | None
    body: str | None
    review_at: str | None
    reviewer_name: str | None
    verified_purchase: bool | None
    helpful_votes: int | None
    variant_info: str | None
    source_url: str | None
    source_file: str | None = None
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def is_valid_for_import(self) -> bool:
        return not self.reject_reasons

    @property
    def content_hash(self) -> str:
        text = "|".join(
            [
                (self.asin or "").strip().upper(),
                str(self.rating or ""),
                (self.title or "").strip().lower(),
                (self.body or "").strip().lower(),
            ]
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_import_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("reject_reasons", None)
        return {key: row.get(key) for key in REVIEW_EXPORT_FIELDS}

    def to_rejected_dict(self) -> dict[str, Any]:
        row = self.to_import_dict()
        row["reject_reasons"] = "；".join(self.reject_reasons)
        row["拒绝原因"] = row["reject_reasons"]
        return row


@dataclass
class ReviewParseResult:
    records: list[AmazonReviewRecord]
    rejected_records: list[AmazonReviewRecord]

    @property
    def total_found(self) -> int:
        return len(self.records) + len(self.rejected_records)

    @property
    def total_valid(self) -> int:
        return len(self.records)


def parse_amazon_review_html(
    html_path: str | Path,
    *,
    default_asin: str | None = None,
) -> ReviewParseResult:
    path = Path(html_path)
    html = path.read_text(encoding="utf-8", errors="ignore")
    return parse_amazon_review_content(html, default_asin=default_asin, source_file=str(path))


def parse_amazon_review_content(
    html: str,
    *,
    default_asin: str | None = None,
    source_file: str | None = None,
) -> ReviewParseResult:
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    asin = _normalize_asin(default_asin) or _extract_asin(soup, html)
    records: list[AmazonReviewRecord] = []
    rejected: list[AmazonReviewRecord] = []
    seen_hashes: set[str] = set()

    for review in _iter_review_nodes(soup):
        record = _parse_review_node(review, asin=asin, source_file=source_file)
        _validate_record(record)
        if record.content_hash in seen_hashes:
            record.reject_reasons.append("文件内重复评论")
        seen_hashes.add(record.content_hash)
        if record.is_valid_for_import:
            records.append(record)
        else:
            rejected.append(record)

    return ReviewParseResult(records=records, rejected_records=rejected)


def count_review_rejected_reasons(records: list[AmazonReviewRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for reason in record.reject_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _iter_review_nodes(soup: BeautifulSoup) -> list[Tag]:
    selectors = [
        "div[data-hook='review']",
        "div[id^='customer_review-']",
        "div.a-section.review",
    ]
    nodes: list[Tag] = []
    seen: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            marker = id(node)
            if marker not in seen:
                seen.add(marker)
                nodes.append(node)
    return nodes


def _parse_review_node(node: Tag, *, asin: str | None, source_file: str | None) -> AmazonReviewRecord:
    title_node = _first_node(node, ["[data-hook='review-title']", "a.review-title", ".review-title"])
    body_node = _first_node(node, ["[data-hook='review-body']", ".review-text-content", ".reviewText"])
    date_node = _first_node(node, ["[data-hook='review-date']", ".review-date"])
    rating_node = _first_node(
        node,
        [
            "[data-hook='review-star-rating'] .a-icon-alt",
            "[data-hook='cmps-review-star-rating'] .a-icon-alt",
            "i.review-rating .a-icon-alt",
            ".review-rating",
            ".a-icon-alt",
        ],
    )
    variant_node = _first_node(node, ["[data-hook='format-strip']", ".review-format-strip"])
    verified_node = _first_node(node, ["[data-hook='avp-badge']", ".a-size-mini.a-color-state"])
    helpful_node = _first_node(node, ["[data-hook='helpful-vote-statement']", ".cr-vote-text"])

    source_url = _extract_review_url(node)

    return AmazonReviewRecord(
        asin=asin,
        review_id=_extract_review_id(node, source_url),
        rating=_extract_rating(_node_text(rating_node) or _node_text(title_node) or ""),
        title=_clean_title(_node_text(title_node)),
        body=_node_text(body_node),
        review_at=_parse_review_date(_node_text(date_node)),
        reviewer_name=_node_text(_first_node(node, [".a-profile-name", "[data-hook='review-author']"])),
        verified_purchase=_parse_verified(_node_text(verified_node)),
        helpful_votes=_parse_helpful_votes(_node_text(helpful_node)),
        variant_info=_node_text(variant_node),
        source_url=source_url,
        source_file=source_file,
    )


def _validate_record(record: AmazonReviewRecord) -> None:
    if not record.asin or not ASIN_RE.match(record.asin):
        record.reject_reasons.append("缺少 ASIN")
    if record.rating is None:
        record.reject_reasons.append("缺少评分")
    elif not 0 <= record.rating <= 5:
        record.reject_reasons.append("评论评分无效")
    if not record.body:
        record.reject_reasons.append("缺少评论正文")


def _extract_asin(soup: BeautifulSoup, html: str) -> str | None:
    for node in soup.select("[data-asin]"):
        asin = _normalize_asin(node.get("data-asin"))
        if asin:
            return asin

    candidates = [
        r"/(?:product-reviews|dp|gp/product)/([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
        r'"asin"\s*:\s*"([A-Z0-9]{10})"',
    ]
    for pattern in candidates:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            asin = _normalize_asin(match.group(1))
            if asin:
                return asin
    return None


def _normalize_asin(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    return text if ASIN_RE.match(text) else None


def _extract_review_id(node: Tag, source_url: str | None) -> str | None:
    if source_url:
        match = re.search(r"/customer-reviews/([A-Z0-9]{8,})", source_url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    for value in (node.get("id"), node.get("data-review-id")):
        if value:
            text = str(value).upper()
            match = re.search(r"(?:CUSTOMER_REVIEW-|REVIEW-)([A-Z0-9]{8,})", text)
            if match:
                return match.group(1)
            match = re.search(r"\b(R[A-Z0-9]{7,})\b", text)
            if match:
                return match.group(1)
    return None


def _extract_review_url(node: Tag) -> str | None:
    link = node.select_one("a[href*='/gp/customer-reviews/'], a[href*='/customer-reviews/']")
    if not link:
        return None
    href = link.get("href")
    return urljoin(AMAZON_BASE_URL, href) if href else None


def _extract_rating(text: str) -> float | None:
    match = re.search(r"([0-5](?:\.\d+)?)\s+out\s+of\s+5", text, re.IGNORECASE)
    if not match:
        match = re.search(r"([0-5](?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _clean_title(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"^[0-5](?:\.\d+)?\s+out\s+of\s+5\s+stars\s*", "", text, flags=re.IGNORECASE)
    return _clean_spaces(cleaned)


def _parse_review_date(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _clean_spaces(text) or ""
    chinese_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", cleaned)
    if chinese_match:
        year, month, day = (int(part) for part in chinese_match.groups())
        return datetime(year, month, day).strftime("%Y-%m-%d")

    if " on " in cleaned.lower():
        cleaned = re.split(r"\bon\b", cleaned, flags=re.IGNORECASE)[-1].strip()

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_verified(text: str | None) -> bool | None:
    if not text:
        return None
    normalized = text.lower()
    if "verified" in normalized or "已验证" in normalized or "验证购买" in normalized:
        return True
    return None


def _parse_helpful_votes(text: str | None) -> int | None:
    if not text:
        return None
    normalized = text.replace(",", "").strip().lower()
    if normalized.startswith("one "):
        return 1
    match = re.search(r"(\d+)", normalized)
    return int(match.group(1)) if match else None


def _first_node(node: Tag, selectors: list[str]) -> Tag | None:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            return found
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
