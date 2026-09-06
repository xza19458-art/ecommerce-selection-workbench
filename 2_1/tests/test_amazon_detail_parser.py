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


def test_detail_parser_reads_offer_specs_quality_and_variants() -> None:
    html = f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Structured Product</span>
      <div id="corePrice_feature_div">
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        <span class="a-price a-text-price"><span class="a-offscreen">$24.99</span></span>
        <span class="savingsPercentage">-20%</span>
      </div>
      <span id="couponText">Save 10% with coupon</span>
      <div id="availability"><span>In Stock</span></div>
      <a id="sellerProfileTriggerId">Example Seller</a>
      <div id="fulfillerInfoFeature_feature_div">
        <span>Ships from</span><span class="offer-display-feature-text-message">Amazon.com</span>
      </div>
      <i class="a-icon-prime"></i>
      <div id="buybox-see-all-buying-choices">3 new offers</div>
      <div id="glow-ingress-line2">Delivering to New York 10001</div>
      <div id="averageCustomerReviews">
        <span id="acrPopover" title="4.7 out of 5 stars">4.7 out of 5 stars</span>
        <span id="acrCustomerReviewText" aria-label="1,234 Reviews">(1,234)</span>
      </div>
      <span id="social-proofing-faceout-title-tk_bought"><b>400+ bought</b> in past month</span>
      <div id="feature-bullets"><ul>
        <li><span class="a-list-item">First useful bullet</span></li>
        <li><span class="a-list-item">Second useful bullet</span></li>
      </ul></div>
      <div id="altImages"><ul>
        <li class="imageThumbnail"></li><li class="imageThumbnail"></li>
        <li class="videoThumbnail"></li>
      </ul></div>
      <div id="aplus_feature_div">A+ content</div>
      <table id="histogramTable">
        <tr><td>5 star</td><td>80%</td></tr><tr><td>1 star</td><td>5%</td></tr>
      </table>
      <table id="productDetails_detailBullets_sections1">
        <tr><th>Product Dimensions</th><td>7.5 x 4 x 2 inches</td></tr>
        <tr><th>Package Dimensions</th><td>25.4 x 12.7 x 5.08 cm</td></tr>
        <tr><th>Item Weight</th><td>1.5 pounds</td></tr>
        <tr><th>Package Weight</th><td>32 ounces</td></tr>
        <tr><th>Unit Count</th><td>36 Count</td></tr>
        <tr><th>Item model number</th><td>MODEL-36</td></tr>
        <tr><th>Parent ASIN</th><td>B0PARENT01</td></tr>
      </table>
      <script>
        var data = {{"parentAsin":"B0PARENT01","dimensionValuesDisplayData":{{
          "{ASIN}":["Red","36 Count"],"B0CHILD001":["Blue","36 Count"]
        }}}};
      </script>
    </body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.offer.current_price == 19.99
    assert record.offer.list_price == 24.99
    assert record.offer.currency == "USD"
    assert record.offer.rating == 4.7
    assert record.offer.review_count == 1234
    assert record.offer.monthly_bought == 400
    assert record.offer.discount_percent == 20.0
    assert record.offer.availability_status == "in_stock"
    assert record.offer.fulfillment_channel == "FBA"
    assert record.offer.is_prime is True
    assert record.offer.offer_count == 3
    assert record.offer.image_count == 2
    assert record.offer.video_count == 1
    assert record.offer.bullet_count == 2
    assert record.offer.has_a_plus is True
    assert record.offer.rating_histogram == {"5": 0.8, "1": 0.05}
    assert record.offer.postal_code == "10001"
    assert record.physical_specs.parent_asin == "B0PARENT01"
    assert record.physical_specs.item_length_in == 7.5
    assert record.physical_specs.package_length_in == 10.0
    assert record.physical_specs.item_weight_oz == 24.0
    assert record.physical_specs.package_weight_oz == 32.0
    assert record.physical_specs.unit_count == 36.0
    assert record.physical_specs.model_number == "MODEL-36"
    assert [item.child_asin for item in record.variants] == ["B0CHILD001", ASIN]
    assert next(item for item in record.variants if item.child_asin == ASIN).is_selected is True


def test_detail_parser_does_not_read_monthly_bought_from_recommendations() -> None:
    html = f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Product without main social proof</span>
      <div class="recommendation-carousel">
        <span>9K+ bought in past month</span>
      </div>
    </body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.offer.monthly_bought is None


def test_detail_page_classifier_rejects_robot_check() -> None:
    state, reason = classify_amazon_detail_page(
        "<html><body>Sorry, we just need to make sure you're not a robot</body></html>",
        current_url="https://www.amazon.com/errors/validateCaptcha",
        title="Robot Check",
    )

    assert state == "blocked"
    assert "验证码" in str(reason)


def test_self_parent_asin_is_not_exposed_as_parent_relation() -> None:
    html = f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Self-parent test product</span>
      <table><tr><th>Parent ASIN</th><td>{ASIN}</td></tr></table>
      <script>var data = {{"parentAsin":"{ASIN}"}};</script>
    </body></html>
    """

    record = parse_amazon_detail_content(html)

    assert record.asin == ASIN
    assert record.physical_specs.parent_asin is None


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
        test_detail_parser_reads_offer_specs_quality_and_variants,
        test_detail_parser_does_not_read_monthly_bought_from_recommendations,
        test_detail_page_classifier_rejects_robot_check,
        test_self_parent_asin_is_not_exposed_as_parent_relation,
        test_amazon_product_url_uses_only_known_amazon_hosts,
    ]
    for test in tests:
        test()
    print(f"amazon detail parser tests passed: {len(tests)}/{len(tests)}")
