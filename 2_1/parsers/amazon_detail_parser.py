"""Best-effort parser for saved Amazon product-detail HTML.

Detail attributes are deliberately optional. A valid product page may omit
the first-available date, breadcrumbs, or Best Sellers Rank entirely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


AMAZON_BASE_URL = "https://www.amazon.com"
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
DATE_LABEL_RE = re.compile(r"date\s+first\s+available", re.IGNORECASE)
BSR_LABEL_RE = re.compile(r"best\s+sellers?\s+rank", re.IGNORECASE)
BSR_PAIR_RE = re.compile(
    r"#\s*([\d,]+)\s+in\s+(.+?)(?=\s*(?:\(\s*See\s+Top|#\s*[\d,]+\s+in\s+|$))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BestSellerRank:
    rank: int
    category_name: str
    category_url: str | None = None
    is_primary: bool = False
    raw_text: str | None = None


@dataclass(frozen=True)
class AmazonDetailRecord:
    asin: str | None
    title: str | None
    category_path: str | None
    date_first_available: date | None
    best_seller_ranks: tuple[BestSellerRank, ...]
    source_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.date_first_available is not None:
            data["date_first_available"] = self.date_first_available.isoformat()
        data["best_seller_ranks"] = [asdict(item) for item in self.best_seller_ranks]
        return data


def parse_amazon_detail_html(
    html_path: str | Path,
    *,
    expected_asin: str | None = None,
    base_url: str = AMAZON_BASE_URL,
) -> AmazonDetailRecord:
    path = Path(html_path)
    return parse_amazon_detail_content(
        path.read_text(encoding="utf-8", errors="ignore"),
        expected_asin=expected_asin,
        source_file=str(path),
        base_url=base_url,
    )


def parse_amazon_detail_content(
    html: str,
    *,
    expected_asin: str | None = None,
    source_file: str | None = None,
    base_url: str = AMAZON_BASE_URL,
) -> AmazonDetailRecord:
    soup = BeautifulSoup(html or "", "lxml")
    json_ld = tuple(_iter_json_ld(soup))
    asin = _extract_asin(soup) or _normalize_asin(expected_asin)
    title = _extract_title(soup) or _json_ld_product_text(json_ld, "name")
    ranks = _extract_best_seller_ranks(soup, base_url=base_url)
    category_path = _extract_category_path(soup)
    if not category_path:
        category_path = _normalize_category_path(_json_ld_product_text(json_ld, "category"))
    if not category_path and ranks:
        category_path = ranks[0].category_name
    first_available = _extract_first_available(soup)
    if first_available is None:
        first_available = _parse_date_value(_json_ld_product_text(json_ld, "releaseDate"))
    return AmazonDetailRecord(
        asin=asin,
        title=_clean_text(title),
        category_path=category_path,
        date_first_available=first_available,
        best_seller_ranks=tuple(ranks),
        source_file=source_file,
    )


def _extract_asin(soup: BeautifulSoup) -> str | None:
    for selector in ("input#ASIN", "input[name='ASIN']"):
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        value = node.get("value") or node.get("data-asin")
        normalized = _normalize_asin(value)
        if normalized:
            return normalized
    for selector, attribute in (("link[rel='canonical']", "href"), ("meta[property='og:url']", "content")):
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", str(node.get(attribute) or ""), re.I)
        if match:
            return match.group(1).upper()
    node = soup.select_one("[data-asin]")
    if isinstance(node, Tag):
        normalized = _normalize_asin(node.get("data-asin"))
        if normalized:
            return normalized
    return None


def _extract_title(soup: BeautifulSoup) -> str | None:
    for selector in ("#productTitle", "meta[property='og:title']"):
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        if _clean_text(value):
            return _clean_text(value)
    return None


def _extract_category_path(soup: BeautifulSoup) -> str | None:
    selectors = (
        "#wayfinding-breadcrumbs_feature_div a",
        "#wayfinding-breadcrumbs_container a",
    )
    for selector in selectors:
        parts = [_clean_text(node.get_text(" ", strip=True)) for node in soup.select(selector)]
        cleaned = [part for part in parts if part]
        if cleaned:
            return " > ".join(dict.fromkeys(cleaned))
    return None


def _extract_first_available(soup: BeautifulSoup) -> date | None:
    for row in soup.select("tr"):
        text = _clean_text(row.get_text(" ", strip=True)) or ""
        if not DATE_LABEL_RE.search(text):
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        values = [_clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        for value in reversed([item for item in values if item]):
            parsed = _parse_date_value(DATE_LABEL_RE.sub("", value, count=1))
            if parsed:
                return parsed
        parsed = _parse_date_value(DATE_LABEL_RE.sub("", text, count=1))
        if parsed:
            return parsed

    for text_node in soup.find_all(string=DATE_LABEL_RE):
        parent = text_node.find_parent(["tr", "li"])
        if parent is None:
            parent = text_node.find_parent("div") or text_node.find_parent("span")
        text = _clean_text(parent.get_text(" ", strip=True) if parent else str(text_node)) or ""
        parsed = _parse_date_value(DATE_LABEL_RE.sub("", text, count=1))
        if parsed:
            return parsed
    return None


def _extract_best_seller_ranks(soup: BeautifulSoup, *, base_url: str) -> list[BestSellerRank]:
    ranks: list[BestSellerRank] = []
    seen: set[tuple[int, str]] = set()
    nodes: list[Tag] = []
    for node in soup.select("tr, li"):
        text = _clean_text(node.get_text(" ", strip=True)) or ""
        if BSR_LABEL_RE.search(text):
            nodes.append(node)
    if not nodes:
        for text_node in soup.find_all(string=BSR_LABEL_RE):
            parent = text_node.find_parent(["tr", "li", "div"])
            if isinstance(parent, Tag):
                nodes.append(parent)

    for node in nodes:
        raw_text = _clean_text(node.get_text(" ", strip=True)) or ""
        clean_raw = BSR_LABEL_RE.sub("", raw_text, count=1).strip(" :\u200e\u200f")
        category_links = _category_links(node, base_url=base_url)
        for rank_text, category_text in BSR_PAIR_RE.findall(clean_raw):
            rank = int(rank_text.replace(",", ""))
            category = _clean_bsr_category(category_text)
            if not category:
                continue
            key = (rank, category.casefold())
            if key in seen:
                continue
            seen.add(key)
            ranks.append(
                BestSellerRank(
                    rank=rank,
                    category_name=category,
                    category_url=_match_category_url(category, category_links),
                    is_primary=not ranks,
                    raw_text=raw_text,
                )
            )
    return ranks


def _category_links(node: Tag, *, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for anchor in node.select("a[href]"):
        href = str(anchor.get("href") or "")
        if "/gp/bestsellers/" not in href:
            continue
        label = _clean_text(anchor.get_text(" ", strip=True))
        if label:
            links.append((label, urljoin(base_url, href)))
    return links


def _match_category_url(category: str, links: list[tuple[str, str]]) -> str | None:
    wanted = category.casefold()
    for label, url in links:
        current = label.casefold()
        if current == wanted or current in wanted or wanted in current:
            return url
    return None


def _clean_bsr_category(value: str | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    text = re.sub(r"\s*\(\s*See\s+Top.*$", "", text, flags=re.I)
    return text.strip(" :-") or None


def _parse_date_value(value: str | None) -> date | None:
    text = _clean_text(value)
    if not text:
        return None
    text = text.strip(" :\u200e\u200f")
    candidates: list[str] = []
    patterns = (
        r"\b[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?\s*,\s*\d{4}\b",
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4}\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidates.append(re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", match.group(0), flags=re.I))
    candidates.append(text)
    formats = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d")
    for candidate in dict.fromkeys(candidates):
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _iter_json_ld(soup: BeautifulSoup) -> Iterable[dict[str, Any]]:
    for script in soup.select("script[type='application/ld+json']"):
        try:
            value = json.loads(script.string or script.get_text() or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        yield from _walk_json_dicts(value)


def _walk_json_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _json_ld_product_text(objects: Iterable[dict[str, Any]], key: str) -> str | None:
    for item in objects:
        item_type = item.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if not any(str(value or "").casefold() == "product" for value in types):
            continue
        value = item.get(key)
        if isinstance(value, str) and _clean_text(value):
            return _clean_text(value)
    return None


def _normalize_category_path(value: str | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parts = [_clean_text(part) for part in re.split(r"\s*(?:>|›|/+)\s*", text)]
    cleaned = [part for part in parts if part]
    return " > ".join(cleaned) if cleaned else None


def _normalize_asin(value: Any) -> str | None:
    asin = str(value or "").strip().upper()
    return asin if ASIN_RE.fullmatch(asin) else None


def _clean_text(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or None
