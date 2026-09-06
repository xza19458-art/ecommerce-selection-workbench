from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mysql_client import MySQLClient  # noqa: E402
from services.product_pool import (  # noqa: E402
    _build_keyword_join,
    _build_product_snapshot_join,
    _overlay_keyword_rank_context,
    _snapshot_state_matches,
    build_product_capture_history,
)
import services.product_pool as product_pool  # noqa: E402


def test_product_pool_uses_one_deterministic_score_and_context_rank() -> None:
    sql, params = _build_keyword_join(keyword=None, keyword_exact=False)

    assert params == []
    assert "LEFT JOIN product_scores ps" in sql
    assert "ORDER BY ps2.score_date DESC, ps2.total_score DESC, ps2.id DESC" in sql
    assert "LEFT JOIN keyword_rank_snapshots krs" in sql


def test_exact_keyword_pool_defaults_to_current_batch_and_aligns_product_snapshot() -> None:
    current_sql, current_params = _build_keyword_join(
        keyword="squishy",
        keyword_exact=True,
    )
    observed_sql, observed_params = _build_keyword_join(
        keyword="squishy",
        keyword_exact=True,
        keyword_scope="observed",
    )
    snapshot_sql = _build_product_snapshot_join(keyword="squishy", keyword_exact=True)

    assert current_params == ["squishy"]
    assert observed_params == ["squishy"]
    assert "k.marketplace = p.marketplace" in current_sql
    assert "SELECT MAX(krs2.snapshot_at)" in current_sql
    assert "krs2.product_id = p.id" not in current_sql
    assert "ORDER BY krs2.snapshot_at DESC, krs2.id DESC" in observed_sql
    assert "krs2.product_id = p.id" in observed_sql
    assert "snap.snapshot_at = krs.snapshot_at" in snapshot_sql


def test_keyword_rank_overlay_never_reuses_another_keyword_rank() -> None:
    snapshots = [
        {"snapshot_at": "2026-07-01 10:00:00", "organic_rank": 99},
        {"snapshot_at": "2026-07-02 10:00:00", "organic_rank": 88},
    ]
    ranks = [
        {
            "snapshot_at": datetime(2026, 7, 1, 10),
            "page_no": 2,
            "organic_rank": 37,
            "is_sponsored": 0,
            "rank_snapshot_id": 10,
        }
    ]

    rows = _overlay_keyword_rank_context(snapshots, ranks)

    assert rows[0]["organic_rank"] == 37
    assert rows[0]["rank_confidence"] == "batch_continuous"
    assert rows[1]["organic_rank"] is None
    assert rows[1]["rank_confidence"] == "unknown"


def test_warehouse_state_requires_same_count_and_latest_time() -> None:
    state = {"row_count": 2, "latest_snapshot_at": datetime(2026, 7, 2, 10)}
    current = [
        {"snapshot_at": "2026-07-01 10:00:00"},
        {"snapshot_at": "2026-07-02 10:00:00"},
    ]

    assert _snapshot_state_matches(current, state) is True
    assert _snapshot_state_matches(current[:1], state) is False


def test_product_capture_history_merges_search_and_detail_evidence_without_forward_fill() -> None:
    rows = build_product_capture_history(
        [
            {
                "snapshot_at": "2026-07-13 12:00:00",
                "price": 16.59,
                "rating": 4.6,
                "review_count": 31,
                "monthly_bought": 500,
                "organic_rank": 34,
                "is_deal": "否",
            }
        ],
        [
            {
                "snapshot_at": datetime(2026, 7, 29, 22, 55, 51),
                "current_price": 15.99,
                "availability_status": "in_stock",
                "fulfillment_channel": "FBA",
                "source_file": "html/_details/B0TEST0001/detail.html",
                "raw_json": (
                    '{"capture_source":"product_detail","detail_page_metrics":'
                    '{"rating":4.7,"review_count":37,"monthly_bought":400,"is_deal":true}}'
                ),
            }
        ],
    )

    assert len(rows) == 2
    assert rows[0]["source_type"] == "search"
    assert rows[1]["source_type"] == "detail"
    assert rows[1]["snapshot_at"] == "2026-07-29 22:55:51"
    assert rows[1]["price"] == 15.99
    assert rows[1]["rating"] == 4.7
    assert rows[1]["review_count"] == 37
    assert rows[1]["monthly_bought"] == 400
    assert rows[1]["organic_rank"] is None
    assert rows[1]["observed_fields"] == ["价格", "评分", "评论数", "近月购买", "库存", "履约"]


def test_product_capture_history_keeps_missing_detail_deal_status_unknown() -> None:
    rows = build_product_capture_history(
        [],
        [
            {
                "snapshot_at": "2026-07-29 22:55:51",
                "current_price": 15.99,
                "raw_json": None,
            }
        ],
    )

    assert rows[0]["is_deal"] is None
    assert rows[0]["rating"] is None
    assert rows[0]["organic_rank"] is None


class _ScoreCursor:
    def __init__(self, update_rowcount: int) -> None:
        self.update_rowcount = update_rowcount
        self.rowcount = 0
        self.statements: list[str] = []

    def execute(self, sql, _params) -> None:
        self.statements.append(" ".join(str(sql).split()))
        self.rowcount = self.update_rowcount if self.statements[-1].startswith("UPDATE product_scores") else 1


def test_null_keyword_score_updates_before_insert() -> None:
    score = SimpleNamespace(
        total_score=60,
        demand_score=50,
        growth_score=50,
        competition_score=50,
        rating_score=50,
        price_score=50,
        rank_score=50,
        reason="test",
    )
    cursor = _ScoreCursor(update_rowcount=1)

    MySQLClient.upsert_score(SimpleNamespace(), cursor, 3, None, score, date(2026, 7, 16))

    assert len(cursor.statements) == 1
    assert "keyword_id IS NULL" in cursor.statements[0]


def test_null_keyword_score_inserts_when_no_existing_row() -> None:
    score = SimpleNamespace(
        total_score=60,
        demand_score=50,
        growth_score=50,
        competition_score=50,
        rating_score=50,
        price_score=50,
        rank_score=50,
        reason="test",
    )
    cursor = _ScoreCursor(update_rowcount=0)

    MySQLClient.upsert_score(SimpleNamespace(), cursor, 3, None, score, date(2026, 7, 16))

    assert len(cursor.statements) == 2
    assert cursor.statements[1].startswith("INSERT INTO product_scores")


class _PoolCursor(AbstractContextManager):
    def __init__(self) -> None:
        self.statements: list[tuple[str, list]] = []
        self._count_query = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params) -> None:
        normalized = " ".join(str(sql).split())
        self.statements.append((normalized, list(params)))
        self._count_query = normalized.startswith("SELECT COUNT(*) AS total")

    def fetchone(self):
        return {"total": 2} if self._count_query else None

    def fetchall(self):
        return [
            {"asin": "B0TEST0001", "title": "First", "review_count": 10, "is_deal": 0},
            {"asin": "B0TEST0002", "title": "Second", "review_count": 20, "is_deal": 1},
        ]


class _Connection(AbstractContextManager):
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class _PoolClient:
    def __init__(self, cursor) -> None:
        self.cursor = cursor

    def connect(self):
        return _Connection(self.cursor)

    def has_columns(self, _cursor, _table, _columns) -> bool:
        return True


def test_product_pool_sorts_globally_before_limit_and_offset() -> None:
    cursor = _PoolCursor()

    page = product_pool.fetch_product_pool_page(
        limit=999,
        offset=-5,
        keyword="squishy",
        min_score=60,
        max_score=90,
        min_price=8,
        max_price=35,
        min_rating=4,
        max_rating=5,
        min_reviews=10,
        max_reviews=500,
        min_bought=50,
        max_bought=10000,
        min_rank=1,
        max_rank=60,
        deal_status="deal",
        size_status="known",
        sort_by="review_count",
        sort_dir="asc",
        client=_PoolClient(cursor),
    )

    count_sql, count_params = cursor.statements[0]
    data_sql, data_params = cursor.statements[1]
    assert count_sql.startswith("SELECT COUNT(*) AS total")
    assert "ORDER BY snap.review_count IS NULL, snap.review_count ASC" in data_sql
    assert data_sql.rfind("ORDER BY snap.review_count") < data_sql.rfind("LIMIT %s OFFSET %s")
    assert "ps.total_score >= %s" in count_sql
    assert "ps.total_score <= %s" in count_sql
    assert "snap.price >= %s" in count_sql
    assert "snap.price <= %s" in count_sql
    assert "snap.rating >= %s" in count_sql
    assert "snap.rating <= %s" in count_sql
    assert "snap.review_count >= %s" in count_sql
    assert "snap.review_count <= %s" in count_sql
    assert "snap.monthly_bought >= %s" in count_sql
    assert "snap.monthly_bought <= %s" in count_sql
    assert "krs.organic_rank >= %s" in count_sql
    assert "krs.organic_rank <= %s" in count_sql
    assert "snap.is_deal = 1" in count_sql
    assert "NULLIF(TRIM(p.product_size), '') IS NOT NULL" in count_sql
    assert data_params[:-2] == count_params
    assert data_params[-2:] == [500, 0]
    assert page == {
        "rows": [
            {"asin": "B0TEST0001", "title": "First", "review_count": 10, "is_deal": "否"},
            {"asin": "B0TEST0002", "title": "Second", "review_count": 20, "is_deal": "是"},
        ],
        "total": 2,
        "limit": 500,
        "offset": 0,
        "sort_by": "review_count",
        "sort_dir": "asc",
    }


class _HistoryCursor(AbstractContextManager):
    def __init__(self, product: dict) -> None:
        self.product = product

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params) -> None:
        return None

    def fetchone(self):
        return dict(self.product)


class _HistoryClient:
    def __init__(self, product: dict) -> None:
        self.cursor = _HistoryCursor(product)

    def connect(self):
        return _Connection(self.cursor)

    def has_columns(self, _cursor, _table, _columns) -> bool:
        return False

    def has_table(self, _cursor, _table) -> bool:
        return False


def test_empty_warehouse_product_history_warns_and_falls_back_to_mysql(monkeypatch) -> None:
    fake_db = _HistoryClient(
        {
            "asin": "B0TEST0001",
            "marketplace": "US",
            "title": "Example",
            "score_keyword": "squishy",
            "total_score": 70,
        }
    )
    mysql_rows = [
        {
            "snapshot_at": datetime(2026, 7, 19, 12),
            "price": 19.99,
            "is_deal": 0,
        }
    ]
    monkeypatch.setattr(product_pool, "MySQLClient", lambda: fake_db)
    monkeypatch.setattr(product_pool, "_fetch_product_snapshots_from_warehouse", lambda _asin: [])
    monkeypatch.setattr(
        product_pool,
        "_fetch_product_snapshot_state",
        lambda _cursor, _asin: {"row_count": 1, "latest_snapshot_at": datetime(2026, 7, 19, 12)},
    )
    monkeypatch.setattr(
        product_pool,
        "_fetch_product_snapshots_from_mysql",
        lambda _cursor, _asin: list(mysql_rows),
    )
    monkeypatch.setattr(product_pool, "_fetch_keyword_rank_history", lambda *_args: [])

    result = product_pool.fetch_product_history("B0TEST0001")

    assert result["snapshot_source"] == "mysql"
    assert result["snapshots"][0]["price"] == 19.99
    assert result["snapshot_warning"] == "分析仓库副本落后于 MySQL，已自动改读主库。"
