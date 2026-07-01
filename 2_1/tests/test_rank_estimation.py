from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.scoring import score_record
from parsers.amazon_search_parser import parse_amazon_search_content
from services.ingestion import parse_html_files


SNAPSHOT_AT = datetime(2026, 6, 30, 10, 0, 0)


def _item(
    asin: str,
    title: str,
    *,
    data_index: int,
    sponsored: bool = False,
) -> str:
    sponsored_text = "<span>Sponsored</span>" if sponsored else ""
    return f"""
    <div data-component-type="s-search-result" data-asin="{asin}" data-index="{data_index}">
      {sponsored_text}
      <h2><a href="/dp/{asin}"><span>{title}</span></a></h2>
      <img class="s-image" src="https://m.media-amazon.com/images/I/{asin}.jpg" />
      <span class="a-price"><span class="a-offscreen">$22.00</span></span>
      <span class="a-icon-alt">4.7 out of 5 stars</span>
      <a href="/dp/{asin}#customerReviews"><span class="a-size-base">1,234</span></a>
      <span class="a-size-base a-color-secondary">1K+ bought in past month</span>
    </div>
    """


def _page(page_no: int, items: list[str]) -> str:
    return f"""
    <html>
      <body>
        <span class="s-pagination-selected">{page_no}</span>
        {"".join(items)}
      </body>
    </html>
    """


def test_parser_uses_non_sponsored_dom_order_for_page_rank() -> None:
    html = _page(
        1,
        [
            _item("B000000001", "Ad Product", data_index=8, sponsored=True),
            _item("B000000002", "First Organic", data_index=12),
            _item("B000000003", "Second Organic", data_index=20),
        ],
    )

    result = parse_amazon_search_content(html, snapshot_at=SNAPSHOT_AT)

    assert [record.asin for record in result.records] == ["B000000002", "B000000003"]
    assert [record.result_slot for record in result.records] == [2, 3]
    assert [record.raw_result_position for record in result.records] == [12, 20]
    assert [record.page_organic_rank for record in result.records] == [1, 2]
    assert [record.organic_rank for record in result.records] == [1, 2]
    assert {record.rank_confidence for record in result.records} == {"page_first"}
    assert result.rejected_records[0].rank_confidence == "sponsored"


def test_ingestion_continues_estimated_rank_across_consecutive_pages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p1 = root / "kw_p1.html"
        p2 = root / "kw_p2.html"
        p1.write_text(
            _page(
                1,
                [
                    _item("B000000001", "Ad Product", data_index=1, sponsored=True),
                    _item("B000000002", "First Organic", data_index=5),
                    _item("B000000003", "Second Organic", data_index=8),
                ],
            ),
            encoding="utf-8",
        )
        p2.write_text(
            _page(2, [_item("B000000004", "Third Organic", data_index=2)]),
            encoding="utf-8",
        )

        records, rejected = parse_html_files([p1, p2], snapshot_at=SNAPSHOT_AT)

    assert [record.asin for record in records] == ["B000000002", "B000000003", "B000000004"]
    assert [record.organic_rank for record in records] == [1, 2, 3]
    assert {record.rank_confidence for record in records} == {"batch_continuous"}
    assert rejected[0].is_sponsored is True


def test_ingestion_does_not_create_global_rank_when_page_one_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kw_p2.html"
        path.write_text(
            _page(2, [_item("B000000004", "Second Page Organic", data_index=1)]),
            encoding="utf-8",
        )

        records, _rejected = parse_html_files([path], snapshot_at=SNAPSHOT_AT)

    assert len(records) == 1
    assert records[0].organic_rank is None
    assert records[0].page_organic_rank == 1
    assert records[0].rank_confidence == "page_gap"


def test_scoring_treats_low_confidence_rank_as_neutral() -> None:
    record = SimpleNamespace(
        monthly_bought=1000,
        review_count=500,
        rating=4.6,
        price=22.0,
        organic_rank=1,
        rank_confidence="page_gap",
        is_deal=False,
    )

    score = score_record(record)

    assert score.rank_score == 50.0
    assert "置信度不足" in score.reason


if __name__ == "__main__":
    tests = [
        test_parser_uses_non_sponsored_dom_order_for_page_rank,
        test_ingestion_continues_estimated_rank_across_consecutive_pages,
        test_ingestion_does_not_create_global_rank_when_page_one_is_missing,
        test_scoring_treats_low_confidence_rank_as_neutral,
    ]
    for test in tests:
        test()
    print(f"rank estimation tests passed: {len(tests)}/{len(tests)}")
