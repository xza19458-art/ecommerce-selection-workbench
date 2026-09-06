"""Best-effort parser for saved Amazon product-detail HTML.

Detail attributes are deliberately optional. A valid product page may omit
the first-available date, breadcrumbs, or Best Sellers Rank entirely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from parsers.amazon_search_parser import extract_monthly_bought_text, parse_count


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
class ProductPhysicalSpecs:
    parent_asin: str | None = None
    item_length_in: float | None = None
    item_width_in: float | None = None
    item_height_in: float | None = None
    package_length_in: float | None = None
    package_width_in: float | None = None
    package_height_in: float | None = None
    item_weight_oz: float | None = None
    package_weight_oz: float | None = None
    unit_count: float | None = None
    model_number: str | None = None
    raw_dimensions: dict[str, str] = field(default_factory=dict)
    raw_weights: dict[str, str] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return any(
            value is not None and value != {}
            for value in asdict(self).values()
        )


@dataclass(frozen=True)
class ProductOfferFacts:
    current_price: float | None = None
    list_price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    monthly_bought: int | None = None
    discount_percent: float | None = None
    coupon_text: str | None = None
    availability_status: str | None = None
    featured_offer_seller: str | None = None
    ships_from: str | None = None
    fulfillment_channel: str | None = None
    is_prime: bool | None = None
    offer_count: int | None = None
    badges: tuple[str, ...] = ()
    image_count: int | None = None
    video_count: int | None = None
    bullet_count: int | None = None
    has_a_plus: bool | None = None
    rating_histogram: dict[str, float] = field(default_factory=dict)
    postal_code: str | None = None
    raw_values: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return any(
            value is not None and value not in ((), {})
            for value in asdict(self).values()
        )


@dataclass(frozen=True)
class ProductVariant:
    child_asin: str
    attributes: dict[str, Any] = field(default_factory=dict)
    product_url: str | None = None
    is_selected: bool = False


@dataclass(frozen=True)
class AmazonDetailRecord:
    asin: str | None
    title: str | None
    category_path: str | None
    date_first_available: date | None
    best_seller_ranks: tuple[BestSellerRank, ...]
    physical_specs: ProductPhysicalSpecs = field(default_factory=ProductPhysicalSpecs)
    offer: ProductOfferFacts = field(default_factory=ProductOfferFacts)
    variants: tuple[ProductVariant, ...] = ()
    source_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.date_first_available is not None:
            data["date_first_available"] = self.date_first_available.isoformat()
        data["best_seller_ranks"] = [asdict(item) for item in self.best_seller_ranks]
        data["physical_specs"] = asdict(self.physical_specs)
        data["offer"] = asdict(self.offer)
        data["variants"] = [asdict(item) for item in self.variants]
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
    detail_rows = _extract_detail_rows(soup)
    physical_specs = _extract_physical_specs(soup, detail_rows, current_asin=asin)
    offer = _extract_offer_facts(soup, json_ld)
    variants = _extract_variants(soup, asin=asin, base_url=base_url)
    return AmazonDetailRecord(
        asin=asin,
        title=_clean_text(title),
        category_path=category_path,
        date_first_available=first_available,
        best_seller_ranks=tuple(ranks),
        physical_specs=physical_specs,
        offer=offer,
        variants=tuple(variants),
        source_file=source_file,
    )


def _extract_detail_rows(soup: BeautifulSoup) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = _clean_text(cells[0].get_text(" ", strip=True))
        value = _clean_text(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
        _store_detail_row(rows, label, value)

    for item in soup.select("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"):
        label_node = item.select_one(".a-text-bold")
        if not isinstance(label_node, Tag):
            continue
        label = _clean_text(label_node.get_text(" ", strip=True))
        full_text = _clean_text(item.get_text(" ", strip=True))
        if not label or not full_text:
            continue
        value = _clean_text(full_text[len(label):]) if full_text.startswith(label) else full_text
        _store_detail_row(rows, label, value)
    return rows


def _store_detail_row(
    rows: dict[str, tuple[str, str]],
    label: str | None,
    value: str | None,
) -> None:
    if not label or not value:
        return
    normalized = _normalize_label(label)
    if normalized and normalized not in rows:
        rows[normalized] = (label.strip(" :\u200e\u200f"), value.strip(" :\u200e\u200f"))


def _normalize_label(value: str | None) -> str:
    text = _clean_text(value) or ""
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _find_detail_value(
    rows: dict[str, tuple[str, str]],
    aliases: tuple[str, ...],
) -> tuple[str, str] | None:
    wanted = tuple(_normalize_label(alias) for alias in aliases)
    for alias in wanted:
        if alias in rows:
            return rows[alias]
    for key, pair in rows.items():
        if any(alias and (alias in key or key in alias) for alias in wanted):
            return pair
    return None


def _extract_physical_specs(
    soup: BeautifulSoup,
    rows: dict[str, tuple[str, str]],
    *,
    current_asin: str | None = None,
) -> ProductPhysicalSpecs:
    item_dimensions = _find_detail_value(
        rows,
        ("product dimensions", "item dimensions lxwxh", "item dimensions", "assembled product dimensions"),
    )
    package_dimensions = _find_detail_value(rows, ("package dimensions", "parcel dimensions"))
    item_weight = _find_detail_value(rows, ("item weight", "product weight", "assembled product weight"))
    package_weight = _find_detail_value(rows, ("package weight", "shipping weight"))
    unit_count = _find_detail_value(rows, ("unit count", "number of items", "item package quantity"))
    model_number = _find_detail_value(rows, ("item model number", "model number", "manufacturer part number"))
    parent_asin = _find_detail_value(rows, ("parent asin",))

    item_values = _parse_dimensions_inches(item_dimensions[1] if item_dimensions else None)
    package_values = _parse_dimensions_inches(package_dimensions[1] if package_dimensions else None)
    raw_dimensions = {
        pair[0]: pair[1]
        for pair in (item_dimensions, package_dimensions)
        if pair is not None and pair[0] and pair[1]
    }
    raw_weights = {
        pair[0]: pair[1]
        for pair in (item_weight, package_weight)
        if pair is not None and pair[0] and pair[1]
    }
    detected_parent_asin = (
        _normalize_asin(parent_asin[1] if parent_asin else None)
        or _extract_parent_asin_from_scripts(soup)
    )
    if detected_parent_asin == current_asin:
        detected_parent_asin = None
    return ProductPhysicalSpecs(
        parent_asin=detected_parent_asin,
        item_length_in=item_values[0] if item_values else None,
        item_width_in=item_values[1] if item_values else None,
        item_height_in=item_values[2] if item_values else None,
        package_length_in=package_values[0] if package_values else None,
        package_width_in=package_values[1] if package_values else None,
        package_height_in=package_values[2] if package_values else None,
        item_weight_oz=_parse_weight_ounces(item_weight[1] if item_weight else None),
        package_weight_oz=_parse_weight_ounces(package_weight[1] if package_weight else None),
        unit_count=_parse_unit_count(unit_count[1] if unit_count else None),
        model_number=_clean_text(model_number[1] if model_number else None),
        raw_dimensions=raw_dimensions,
        raw_weights=raw_weights,
    )


def _parse_dimensions_inches(value: str | None) -> tuple[float, float, float] | None:
    text = _clean_text(value)
    if not text:
        return None
    normalized = text.replace("\u00d7", "x")
    normalized = re.sub(r"(?<=\d)\s*[\"\u201d']?\s*[lwh]\b", "", normalized, flags=re.I)
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:x|by)\s*"
        r"(\d+(?:\.\d+)?)\s*(?:x|by)\s*"
        r"(\d+(?:\.\d+)?)\s*"
        r"(inches?|in\.?|centimeters?|cm|millimeters?|mm|feet|foot|ft\.?)?",
        normalized,
        re.I,
    )
    if not match:
        return None
    unit = match.group(4) or _find_measurement_unit(normalized)
    factor = _length_to_inches_factor(unit)
    if factor is None:
        return None
    return tuple(round(float(match.group(index)) * factor, 3) for index in (1, 2, 3))


def _find_measurement_unit(value: str) -> str | None:
    match = re.search(
        r"\b(inches?|in\.?|centimeters?|cm|millimeters?|mm|feet|foot|ft\.?)\b",
        value,
        re.I,
    )
    return match.group(1) if match else None


def _length_to_inches_factor(unit: str | None) -> float | None:
    normalized = str(unit or "").casefold().rstrip(".")
    if normalized in {"inch", "inches", "in"}:
        return 1.0
    if normalized in {"centimeter", "centimeters", "cm"}:
        return 1.0 / 2.54
    if normalized in {"millimeter", "millimeters", "mm"}:
        return 1.0 / 25.4
    if normalized in {"foot", "feet", "ft"}:
        return 12.0
    return None


def _parse_weight_ounces(value: str | None) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(ounces?|oz\.?|pounds?|lbs?\.?|kilograms?|kg|grams?|g)\b",
        text,
        re.I,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).casefold().rstrip(".")
    if unit in {"ounce", "ounces", "oz"}:
        factor = 1.0
    elif unit in {"pound", "pounds", "lb", "lbs"}:
        factor = 16.0
    elif unit in {"kilogram", "kilograms", "kg"}:
        factor = 35.27396195
    else:
        factor = 0.03527396195
    return round(amount * factor, 3)


def _parse_unit_count(value: str | None) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(match.group(1)) if match else None


def _extract_parent_asin_from_scripts(soup: BeautifulSoup) -> str | None:
    pattern = re.compile(r'["\'](?:parentAsin|parent_asin)["\']\s*:\s*["\']([A-Z0-9]{10})["\']', re.I)
    for script in soup.select("script"):
        match = pattern.search(script.string or script.get_text() or "")
        if match:
            return _normalize_asin(match.group(1))
    return None


def _extract_offer_facts(
    soup: BeautifulSoup,
    json_ld: tuple[dict[str, Any], ...],
) -> ProductOfferFacts:
    product_json = _first_json_ld_product(json_ld)
    offer_json = _first_json_ld_offer(product_json)
    current_text = _text_from_selectors(
        soup,
        (
            "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#price_inside_buybox",
            "#tp_price_block_total_price_ww .a-offscreen",
        ),
    )
    list_text = _text_from_selectors(
        soup,
        (
            "#corePrice_feature_div .a-price.a-text-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price.a-text-price .a-offscreen",
            "[data-a-strike='true'] .a-offscreen",
            ".basisPrice .a-offscreen",
            "#listPrice",
        ),
    )
    current_price = _parse_price(current_text)
    if current_price is None:
        current_price = _float_or_none((offer_json or {}).get("price"))
    list_price = _parse_price(list_text)
    currency = _currency_from_text(current_text or list_text)
    if not currency:
        currency = _clean_text((offer_json or {}).get("priceCurrency"))

    availability_raw = _text_from_selectors(soup, ("#availability", "#outOfStock", "#deliveryBlockMessage"))
    if not availability_raw:
        availability_raw = _clean_text((offer_json or {}).get("availability"))
    seller = _text_from_selectors(
        soup,
        ("#sellerProfileTriggerId", "#merchant-info a", "#merchant-info"),
    )
    if not seller:
        json_seller = (offer_json or {}).get("seller")
        if isinstance(json_seller, dict):
            seller = _clean_text(json_seller.get("name"))
    ships_from = _extract_offer_feature_value(soup, "#fulfillerInfoFeature_feature_div")
    coupon_text = _text_from_selectors(
        soup,
        ("#couponTextpctch", "#couponText", "#vpcButton .a-color-success", ".promoPriceBlockMessage"),
    )
    discount_text = _text_from_selectors(
        soup,
        ("#corePrice_feature_div .savingsPercentage", "#corePriceDisplay_desktop_feature_div .savingsPercentage"),
    )
    badges = _extract_badges(soup)
    image_count, video_count = _extract_media_counts(soup, product_json)
    bullet_count = _count_feature_bullets(soup)
    rating_histogram = _extract_rating_histogram(soup)
    postal_code = _extract_postal_code(soup)
    is_prime = True if soup.select_one(".a-icon-prime, [aria-label*='Prime' i]") else None
    rating = _extract_product_rating(soup, product_json)
    review_count = _extract_product_review_count(soup, product_json)
    monthly_bought = _extract_product_monthly_bought(soup)

    return ProductOfferFacts(
        current_price=current_price,
        list_price=list_price,
        currency=currency,
        rating=rating,
        review_count=review_count,
        monthly_bought=monthly_bought,
        discount_percent=_parse_discount_percent(discount_text or coupon_text),
        coupon_text=coupon_text,
        availability_status=_normalize_availability(availability_raw),
        featured_offer_seller=seller,
        ships_from=ships_from,
        fulfillment_channel=_infer_fulfillment_channel(seller, ships_from),
        is_prime=is_prime,
        offer_count=_extract_offer_count(soup),
        badges=badges,
        image_count=image_count,
        video_count=video_count,
        bullet_count=bullet_count,
        has_a_plus=bool(soup.select_one("#aplus, #aplus_feature_div, #aplusBrandStory_feature_div")),
        rating_histogram=rating_histogram,
        postal_code=postal_code,
        raw_values={
            "current_price_text": current_text,
            "list_price_text": list_text,
            "availability_text": availability_raw,
            "discount_text": discount_text,
            "seller_text": seller,
            "ships_from_text": ships_from,
            "rating": rating,
            "review_count": review_count,
            "monthly_bought": monthly_bought,
        },
    )


def _text_from_selectors(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            value = _clean_text(node.get_text(" ", strip=True))
            if value:
                return value
    return None


def _parse_price(value: str | None) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(?:US\$|[$\u00a3\u20ac\u00a5])?\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text)
    if not match:
        return None
    return _float_or_none(match.group(1).replace(",", ""))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _currency_from_text(value: str | None) -> str | None:
    text = str(value or "")
    if "US$" in text or "$" in text:
        return "USD"
    if "\u00a3" in text:
        return "GBP"
    if "\u20ac" in text:
        return "EUR"
    if "\u00a5" in text:
        return "JPY"
    return None


def _parse_discount_percent(value: str | None) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(?:-|save\s*)?(\d+(?:\.\d+)?)\s*%", text, re.I)
    return float(match.group(1)) if match else None


def _normalize_availability(value: str | None) -> str | None:
    text = (_clean_text(value) or "").casefold()
    if not text:
        return None
    if "outofstock" in text or "out of stock" in text or "currently unavailable" in text:
        return "out_of_stock"
    if "in stock" in text or text.endswith("/instock"):
        return "in_stock"
    if "only " in text and " left" in text:
        return "limited"
    if "pre-order" in text or "preorder" in text:
        return "preorder"
    return "unknown"


def _extract_offer_feature_value(soup: BeautifulSoup, selector: str) -> str | None:
    container = soup.select_one(selector)
    if not isinstance(container, Tag):
        return None
    value = container.select_one(".offer-display-feature-text-message")
    if isinstance(value, Tag):
        return _clean_text(value.get_text(" ", strip=True))
    texts = [_clean_text(node.get_text(" ", strip=True)) for node in container.select("span")]
    cleaned = [text for text in texts if text]
    return cleaned[-1] if cleaned else _clean_text(container.get_text(" ", strip=True))


def _infer_fulfillment_channel(seller: str | None, ships_from: str | None) -> str | None:
    seller_text = (seller or "").casefold()
    ships_text = (ships_from or "").casefold()
    seller_is_amazon = "amazon" in seller_text
    ships_is_amazon = "amazon" in ships_text
    if seller_is_amazon and ships_is_amazon:
        return "Amazon Retail"
    if ships_is_amazon:
        return "FBA"
    if seller or ships_from:
        return "FBM"
    return None


def _extract_offer_count(soup: BeautifulSoup) -> int | None:
    text = _text_from_selectors(
        soup,
        ("#buybox-see-all-buying-choices", "#olpLinkWidget_feature_div", "#olp_feature_div"),
    )
    if not text:
        return None
    patterns = (r"(\d[\d,]*)\s+(?:new|used)\s+(?:offers?|from)", r"(?:new|used)\s*\((\d[\d,]*)\)")
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _extract_badges(soup: BeautifulSoup) -> tuple[str, ...]:
    badges: list[str] = []
    selectors = (
        "#acBadge_feature_div",
        "#dealBadge_feature_div",
        ".badge-wrapper",
        ".a-badge-text",
        "#zeitgeistBadge_feature_div",
    )
    for selector in selectors:
        for node in soup.select(selector):
            text = _clean_text(node.get_text(" ", strip=True))
            if text and len(text) <= 120 and text not in badges:
                badges.append(text)
    return tuple(badges[:12])


def _extract_media_counts(
    soup: BeautifulSoup,
    product_json: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    images = soup.select("#altImages li.imageThumbnail, #altImages li.item.imageThumbnail")
    videos = soup.select("#altImages li.videoThumbnail, #altImages .videoThumbnail")
    image_count: int | None = len(images) if images else None
    video_count: int | None = len(videos) if videos else 0 if soup.select_one("#altImages") else None
    if image_count is None and isinstance(product_json, dict):
        value = product_json.get("image")
        if isinstance(value, list):
            image_count = len([item for item in value if item])
        elif value:
            image_count = 1
    return image_count, video_count


def _count_feature_bullets(soup: BeautifulSoup) -> int | None:
    values: list[str] = []
    for node in soup.select("#feature-bullets li .a-list-item"):
        text = _clean_text(node.get_text(" ", strip=True))
        if text and "make sure this fits" not in text.casefold():
            values.append(text)
    return len(values) if values else None


def _extract_rating_histogram(soup: BeautifulSoup) -> dict[str, float]:
    histogram: dict[str, float] = {}
    for row in soup.select("#histogramTable tr, [data-hook='review-star-count']"):
        text = _clean_text(row.get_text(" ", strip=True)) or ""
        match = re.search(r"([1-5])\s*star\D{0,20}(\d+(?:\.\d+)?)\s*%", text, re.I)
        if not match:
            match = re.search(r"(\d+(?:\.\d+)?)\s*%\D{0,20}([1-5])\s*star", text, re.I)
            if match:
                histogram[match.group(2)] = round(float(match.group(1)) / 100.0, 4)
            continue
        histogram[match.group(1)] = round(float(match.group(2)) / 100.0, 4)
    return histogram


def _extract_product_rating(
    soup: BeautifulSoup,
    product_json: dict[str, Any] | None,
) -> float | None:
    candidates: list[str] = []
    for selector in (
        "#acrPopover",
        "#averageCustomerReviews .a-icon-alt",
        "[data-hook='average-star-rating'] .a-icon-alt",
        "[data-hook='rating-out-of-text']",
    ):
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        candidates.extend(
            str(value)
            for value in (
                node.get_text(" ", strip=True),
                node.get("title"),
                node.get("aria-label"),
            )
            if value
        )
    for text in candidates:
        match = re.search(r"([0-5](?:\.\d+)?)\s*(?:out\s+of\s+5|stars?)", text, re.I)
        if match:
            value = float(match.group(1))
            if 0 < value <= 5:
                return value

    aggregate = product_json.get("aggregateRating") if isinstance(product_json, dict) else None
    value = _float_or_none(aggregate.get("ratingValue")) if isinstance(aggregate, dict) else None
    return value if value is not None and 0 < value <= 5 else None


def _extract_product_review_count(
    soup: BeautifulSoup,
    product_json: dict[str, Any] | None,
) -> int | None:
    for selector in ("#acrCustomerReviewText", "[data-hook='total-review-count']"):
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        for text in (node.get_text(" ", strip=True), node.get("aria-label")):
            value = parse_count(str(text or ""))
            if value is not None and value >= 0:
                return value

    aggregate = product_json.get("aggregateRating") if isinstance(product_json, dict) else None
    if isinstance(aggregate, dict):
        for key in ("reviewCount", "ratingCount"):
            value = parse_count(str(aggregate.get(key) or ""))
            if value is not None and value >= 0:
                return value
    return None


def _extract_product_monthly_bought(soup: BeautifulSoup) -> int | None:
    # This ID belongs to the current product's social-proof faceout. Do not
    # scan the whole page because recommendation carousels contain other
    # products' "bought in past month" text.
    node = soup.select_one("#social-proofing-faceout-title-tk_bought")
    if not isinstance(node, Tag):
        return None
    return extract_monthly_bought_text(node.get_text(" ", strip=True))


def _extract_postal_code(soup: BeautifulSoup) -> str | None:
    text = _text_from_selectors(soup, ("#glow-ingress-line2", "#contextualIngressPtLabel_deliveryShortLine"))
    if not text:
        return None
    match = re.search(r"\b\d{5}(?:-\d{4})?\b", text)
    return match.group(0) if match else None


def _first_json_ld_product(objects: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for item in objects:
        item_type = item.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(value or "").casefold() == "product" for value in types):
            return item
    return None


def _first_json_ld_offer(product: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(product, dict):
        return None
    offers = product.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        return next((item for item in offers if isinstance(item, dict)), None)
    return None


def _extract_variants(
    soup: BeautifulSoup,
    *,
    asin: str | None,
    base_url: str,
) -> list[ProductVariant]:
    parent_asin = _extract_parent_asin_from_scripts(soup) or asin
    variants: dict[str, dict[str, Any]] = {}
    for container in soup.select("#twister, #twister_feature_div, [id^='variation_']"):
        for node in container.select("[data-asin], [data-defaultasin], [data-dp-url], a[href*='/dp/']"):
            child = _variant_asin_from_node(node)
            if not child:
                continue
            label = _clean_text(node.get("aria-label") or node.get("title") or node.get_text(" ", strip=True))
            current = variants.setdefault(child, {"attributes": {}, "selected": False, "url": None})
            if label and len(label) <= 255:
                current["attributes"].setdefault("label", label)
            current["selected"] = bool(current["selected"] or child == asin or "selected" in " ".join(node.get("class") or []).casefold())
            href = node.get("data-dp-url") or node.get("href")
            if href:
                current["url"] = urljoin(base_url, str(href))

    for script in soup.select("script"):
        payload = script.string or script.get_text() or ""
        display_data = _extract_json_object_after_key(payload, "dimensionValuesDisplayData")
        if not isinstance(display_data, dict):
            continue
        for child_value, attributes in display_data.items():
            child = _normalize_asin(child_value)
            if not child:
                continue
            current = variants.setdefault(child, {"attributes": {}, "selected": False, "url": None})
            if isinstance(attributes, list):
                current["attributes"]["display_values"] = [
                    _clean_text(item) for item in attributes if _clean_text(item)
                ]
            current["selected"] = bool(current["selected"] or child == asin)

    if len(variants) <= 1 and parent_asin == asin:
        return []
    result: list[ProductVariant] = []
    for child, item in sorted(variants.items()):
        result.append(
            ProductVariant(
                child_asin=child,
                attributes=item["attributes"],
                product_url=item["url"] or urljoin(base_url, f"/dp/{child}"),
                is_selected=bool(item["selected"]),
            )
        )
    return result


def _variant_asin_from_node(node: Tag) -> str | None:
    for attribute in ("data-asin", "data-defaultasin"):
        normalized = _normalize_asin(node.get(attribute))
        if normalized:
            return normalized
    for attribute in ("data-dp-url", "href"):
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", str(node.get(attribute) or ""), re.I)
        if match:
            return match.group(1).upper()
    return None


def _extract_json_object_after_key(payload: str, key: str) -> dict[str, Any] | None:
    match = re.search(rf'["\']{re.escape(key)}["\']\s*:\s*', payload)
    if not match:
        return None
    start = payload.find("{", match.end())
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(payload)):
        char = payload[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(payload[start:index + 1])
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
                return value if isinstance(value, dict) else None
    return None


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
