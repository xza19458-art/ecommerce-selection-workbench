from __future__ import annotations

from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parsers.amazon_detail_parser import parse_amazon_detail_content
from services.amazon_urls import amazon_product_url
from services.product_detail_collection import classify_amazon_detail_page


ASIN = "B0DQYSR28D"


def test_detail_parser_reads_breadcrumbs_date_and_multiple_bsr_rows() -> None:
    html = f"""
    <html><head><link rel="canonical" href="https://www.amazon.com/dp/{ASIN}" /></head><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Easter Mochi Squishy Toys</span>
      <div id="wayfinding-breadcrumbs_feature_div">
        <a href="/toys">Toys &amp; Games</a><span>›</span>
        <a href="/novelty">Novelty &amp; Gag Toys</a><span>›</span>
        <a href="/squeeze">Squeeze Toys</a>
      </div>
      <table id="productDetails_detailBullets_sections1">
        <tr><th>Date First Available</th><td>May 17, 2024</td></tr>
        <tr><th>Best Sellers Rank</th><td>
          #182,796 in <a href="/gp/bestsellers/toys-and-games">Toys &amp; Games</a>
          (See Top 100 in Toys &amp; Games)
          #5,390 in <a href="/gp/bestsellers/toys-and-games/166027011">Squeeze Toys</a>
        </td></tr>
      </table>
    </body></html>
    """

    record = parse_amazon_detail_content(html, expected_asin=ASIN)

    assert record.asin == ASIN
    assert record.title == "Easter Mochi Squishy Toys"
    assert record.category_path == "Toys & Games > Novelty & Gag Toys > Squeeze Toys"
    assert record.date_first_available == date(2024, 5, 17)
    assert [(item.rank, item.category_name) for item in record.best_seller_ranks] == [
        (182796, "Toys & Games"),
        (5390, "Squeeze Toys"),
    ]
    assert record.best_seller_ranks[0].is_primary is True
    assert record.best_seller_ranks[1].is_primary is False
    assert record.best_seller_ranks[1].category_url == (
        "https://www.amazon.com/gp/bestsellers/toys-and-games/166027011"
    )


def test_detail_parser_supports_detail_bullets_and_missing_breadcrumbs() -> None:
    html = f"""
    <html><body>
      <input name="ASIN" value="{ASIN}" />
      <h1><span id="productTitle">A Useful Product</span></h1>
      <div id="detailBullets_feature_div"><ul>
        <li><span class="a-text-bold">Date First Available :</span> 17 May 2024</li>
        <li><span class="a-text-bold">Best Sellers Rank :</span>
          #12 in Home &amp; Kitchen #3 in Storage Boxes
        </li>
      </ul></div>
    </body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.date_first_available == date(2024, 5, 17)
    assert record.category_path == "Home & Kitchen"
    assert [item.rank for item in record.best_seller_ranks] == [12, 3]


def test_detail_parser_uses_json_ld_only_as_optional_fallback() -> None:
    html = f"""
    <html><head>
      <link rel="canonical" href="https://www.amazon.com/dp/{ASIN}" />
      <script type="application/ld+json">
        {{"@type":"Product","name":"JSON Product","category":"Toys & Games > Squeeze Toys","releaseDate":"2025-01-02"}}
      </script>
    </head><body><div>Product content</div></body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.asin == ASIN
    assert record.title == "JSON Product"
    assert record.category_path == "Toys & Games > Squeeze Toys"
    assert record.date_first_available == date(2025, 1, 2)
    assert record.best_seller_ranks == ()


def test_detail_parser_keeps_legitimate_missing_fields_as_none() -> None:
    html = f"""
    <html><head><script type="application/ld+json">
      {{"@type":"Review","name":"Review title","datePublished":"2026-06-30"}}
    </script></head><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Product Without Detail Attributes</span>
      <div>Copyright 2026. No product-detail date or rank is shown.</div>
    </body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.date_first_available is None
    assert record.category_path is None
    assert record.best_seller_ranks == ()


def test_detail_page_classifier_rejects_robot_check() -> None:
    state, reason = classify_amazon_detail_page(
        "<html><body>Sorry, we just need to make sure you're not a robot</body></html>",
        current_url="https://www.amazon.com/errors/validateCaptcha",
        title="Robot Check",
    )

    assert state == "blocked"
    assert "验证码" in str(reason)


def test_amazon_product_url_uses_only_known_amazon_hosts() -> None:
    assert amazon_product_url(ASIN, marketplace="UK") == f"https://www.amazon.co.uk/dp/{ASIN}"
    assert amazon_product_url(
        ASIN,
        marketplace="US",
        source_url=f"https://www.amazon.de/gp/product/{ASIN}",
    ) == f"https://www.amazon.de/dp/{ASIN}"
    assert amazon_product_url(
        ASIN,
        marketplace="US",
        source_url=f"https://amazon.com.evil.example/dp/{ASIN}",
    ) == f"https://www.amazon.com/dp/{ASIN}"


if __name__ == "__main__":
    tests = [
        test_detail_parser_reads_breadcrumbs_date_and_multiple_bsr_rows,
        test_detail_parser_supports_detail_bullets_and_missing_breadcrumbs,
        test_detail_parser_uses_json_ld_only_as_optional_fallback,
        test_detail_parser_keeps_legitimate_missing_fields_as_none,
        test_detail_page_classifier_rejects_robot_check,
        test_amazon_product_url_uses_only_known_amazon_hosts,
    ]
    for test in tests:
        test()
    print(f"amazon detail parser tests passed: {len(tests)}/{len(tests)}")
