"""Validated Amazon product URL helpers shared by API and collectors."""

from __future__ import annotations

import re
from urllib.parse import urlparse


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

MARKETPLACE_DOMAINS = {
    "US": "amazon.com",
    "CA": "amazon.ca",
    "MX": "amazon.com.mx",
    "BR": "amazon.com.br",
    "UK": "amazon.co.uk",
    "GB": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "IT": "amazon.it",
    "ES": "amazon.es",
    "NL": "amazon.nl",
    "SE": "amazon.se",
    "PL": "amazon.pl",
    "BE": "amazon.com.be",
    "JP": "amazon.co.jp",
    "AU": "amazon.com.au",
    "IN": "amazon.in",
    "SG": "amazon.sg",
    "TR": "amazon.com.tr",
    "SA": "amazon.sa",
    "AE": "amazon.ae",
    "EG": "amazon.eg",
}


def normalize_asin(value: str) -> str:
    asin = str(value or "").strip().upper()
    if not ASIN_RE.fullmatch(asin):
        raise ValueError("ASIN 必须是 10 位字母或数字")
    return asin


def amazon_product_url(
    asin: str,
    *,
    marketplace: str = "US",
    source_url: str | None = None,
) -> str:
    """Build a canonical /dp URL without accepting arbitrary external hosts."""
    normalized_asin = normalize_asin(asin)
    domain = _amazon_domain_from_url(source_url)
    if domain is None:
        domain = MARKETPLACE_DOMAINS.get(str(marketplace or "US").strip().upper(), MARKETPLACE_DOMAINS["US"])
    return f"https://www.{domain}/dp/{normalized_asin}"


def _amazon_domain_from_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        host = (urlparse(str(value).strip()).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    allowed = set(MARKETPLACE_DOMAINS.values())
    return host if host in allowed else None
