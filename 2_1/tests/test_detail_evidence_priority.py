from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.detail_reparse import (  # noqa: E402
    DetailReparseError,
    _decorate_detail_gap_disposition,
    _decorate_detail_gap_priority,
    _detail_disposition_summary,
    _detail_project_id,
    _detail_priority_filter,
    _fetch_product_gaps,
)


class _Cursor:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        self.current = self.responses[len(self.calls) - 1]

    def fetchone(self):
        assert isinstance(self.current, dict)
        return dict(self.current)

    def fetchall(self):
        assert isinstance(self.current, list)
        return [dict(row) for row in self.current]


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class _Client:
    def __init__(self, responses):
        self.cursor = _Cursor(responses)

    def connect(self):
        return _Connection(self.cursor)


def _priority_row() -> dict:
    return {
        "source_total": 2,
        "filtered_total": 1,
        "project_candidate_total": 1,
        "project_benchmark_total": 0,
        "project_related_total": 0,
        "recent_observation_total": 1,
        "historical_project_total": 0,
        "routine_total": 0,
        "planned_total": 1,
        "due_plan_total": 1,
    }


def test_global_priority_filter_and_project_context_are_applied_before_paging() -> None:
    now = datetime(2026, 8, 13, 10, 0)
    client = _Client(
        [
            {
                "product_total": 2,
                "not_collected_total": 1,
                "stale_total": 0,
                "partial_total": 1,
            },
            _priority_row(),
            [
                {
                    "product_id": 10,
                    "asin": "B0TEST0001",
                    "marketplace": "US",
                    "title": "Priority candidate",
                    "title_zh": None,
                    "category_path": None,
                    "date_first_available": None,
                    "detail_collected_at": datetime(2026, 8, 10, 9, 0),
                    "detail_source_file": "C:/private/detail.html",
                    "first_seen_at": datetime(2026, 7, 1, 9, 0),
                    "last_seen_at": datetime(2026, 8, 12, 9, 0),
                    "project_relation_count": 1,
                    "active_project_count": 1,
                    "active_candidate_count": 1,
                    "active_benchmark_count": 0,
                    "active_plan_count": 1,
                    "due_plan_count": 1,
                    "nearest_review_on": date(2026, 8, 12),
                    "priority_order": 0,
                    "evidence_order": 2,
                }
            ],
            [
                {
                    "product_id": 10,
                    "role": "candidate",
                    "project_id": 4,
                    "project_name": "减压玩具验证",
                    "project_status": "collecting",
                    "plan_status": "active",
                    "next_review_on": date(2026, 8, 12),
                }
            ],
        ]
    )

    summary, page = _fetch_product_gaps(
        client,
        limit=10,
        offset=0,
        stale_days=30,
        now=now,
        priority="focus",
    )

    assert summary["gap_total"] == 2
    assert page["total"] == 1
    assert page["source_total"] == 2
    assert page["priority_filter"] == "focus"
    assert page["priority_summary"]["project_focus_total"] == 1
    assert page["priority_summary"]["planned_total"] == 1
    assert page["priority_policy"]["numeric_score"] is False
    assert page["priority_policy"]["automatic_collection"] is False

    row = page["rows"][0]
    assert row["priority_tier"] == "project_candidate"
    assert row["priority_label"] == "项目候选"
    assert row["evidence_status"] == "partial"
    assert row["research_context"][0]["role_label"] == "候选商品"
    assert row["research_context"][0]["plan_due"] is True
    assert row["monitoring"]["due_plan_count"] == 1
    assert "不会自动采集" in row["priority_reason"]
    assert row["recommended_action"]["code"] == "collect_gap"
    assert row["recommended_action"]["local_evidence_checked"] is False

    row_query, row_params = client.cursor.calls[2]
    assert "FROM prioritized_gaps" in row_query
    assert "WHERE priority_order IN (0, 1)" in row_query
    assert "ORDER BY\n                  priority_order" in row_query
    assert row_params[-2:] == (10, 0)
    context_query, context_params = client.cursor.calls[3]
    assert "research_project_products" in context_query
    assert context_params == (10,)


def test_project_scope_controls_priority_cte_context_and_paging() -> None:
    now = datetime(2026, 8, 13, 10, 0)
    priority_row = {**_priority_row(), "source_total": 1, "filtered_total": 1}
    client = _Client(
        [
            {
                "id": 4,
                "name": "减压玩具验证",
                "marketplace": "US",
                "status": "collecting",
            },
            {
                "product_total": 100,
                "not_collected_total": 50,
                "stale_total": 10,
                "partial_total": 40,
            },
            priority_row,
            [
                {
                    "product_id": 10,
                    "asin": "B0TEST0001",
                    "marketplace": "US",
                    "title": "Scoped candidate",
                    "title_zh": None,
                    "category_path": None,
                    "date_first_available": None,
                    "detail_collected_at": datetime(2026, 8, 10, 9, 0),
                    "detail_source_file": None,
                    "first_seen_at": datetime(2026, 7, 1, 9, 0),
                    "last_seen_at": datetime(2026, 8, 12, 9, 0),
                    "project_relation_count": 1,
                    "active_project_count": 1,
                    "active_candidate_count": 1,
                    "active_benchmark_count": 0,
                    "active_plan_count": 0,
                    "due_plan_count": 0,
                    "nearest_review_on": None,
                    "priority_order": 0,
                    "evidence_order": 2,
                }
            ],
            [
                {
                    "product_id": 10,
                    "role": "candidate",
                    "project_id": 4,
                    "project_name": "减压玩具验证",
                    "project_status": "collecting",
                    "plan_status": None,
                    "next_review_on": None,
                }
            ],
        ]
    )

    summary, page = _fetch_product_gaps(
        client,
        limit=10,
        offset=20,
        stale_days=30,
        now=now,
        priority="all",
        project_id=4,
    )

    assert summary["product_total"] == 100
    assert summary["gap_total"] == 1
    assert page["total"] == 1
    assert page["project_filter"] == {
        "active": True,
        "project_id": 4,
        "project_name": "减压玩具验证",
        "marketplace": "US",
        "project_status": "collecting",
        "project_status_label": "收集证据",
        "meaning": "队列成员、优先级计数、排序和分页均限定在当前研究项目。",
    }
    assert [context["project_id"] for context in page["rows"][0]["research_context"]] == [4]

    project_query, project_params = client.cursor.calls[0]
    assert "FROM research_projects" in project_query
    assert project_params == (4,)
    aggregate_query, aggregate_params = client.cursor.calls[2]
    assert "WHERE rpp.project_id = %s" in aggregate_query
    assert "JOIN research_priority rp ON rp.product_id = p.id" in aggregate_query
    assert "LEFT JOIN research_priority rp ON rp.product_id = p.id" not in aggregate_query
    assert aggregate_params == (
        date(2026, 8, 13),
        4,
        datetime(2026, 7, 14, 10, 0),
        datetime(2026, 7, 14, 10, 0),
        datetime(2026, 7, 14, 10, 0),
    )
    row_query, row_params = client.cursor.calls[3]
    assert "WHERE rpp.project_id = %s" in row_query
    assert "LIMIT %s OFFSET %s" in row_query
    assert row_params[-2:] == (10, 20)
    context_query, context_params = client.cursor.calls[4]
    assert "AND rpp.project_id = %s" in context_query
    assert context_params == (10, 4)


def test_project_scope_rejects_invalid_or_missing_projects() -> None:
    assert _detail_project_id(None) is None
    assert _detail_project_id("4") == 4
    with pytest.raises(DetailReparseError, match="研究项目 ID 必须为正整数"):
        _detail_project_id(0)

    client = _Client([{}])
    with pytest.raises(DetailReparseError, match="研究项目 #999 不存在"):
        _fetch_product_gaps(
            client,
            limit=10,
            offset=0,
            stale_days=30,
            now=datetime(2026, 8, 13, 10, 0),
            project_id=999,
        )
    assert len(client.cursor.calls) == 1


def test_terminal_project_plan_does_not_become_active_collection_priority() -> None:
    item = {
        "priority_order": 4,
        "project_relation_count": 1,
        "active_project_count": 0,
        "active_candidate_count": 0,
        "active_benchmark_count": 0,
        "active_plan_count": 0,
        "due_plan_count": 0,
        "nearest_review_on": None,
        "evidence_order": 0,
    }
    _decorate_detail_gap_priority(
        item,
        [
            {
                "project_id": 9,
                "project_name": "已结束项目",
                "project_status": "approved",
                "role": "candidate",
                "plan_status": "active",
                "next_review_on": date(2026, 8, 1),
            }
        ],
        now=datetime(2026, 8, 13),
    )

    assert item["priority_tier"] == "historical_project"
    assert item["research_context"][0]["project_active"] is False
    assert item["research_context"][0]["plan_active"] is False
    assert item["research_context"][0]["plan_due"] is False
    assert item["monitoring"]["active_plan_count"] == 0


def test_unknown_priority_filter_is_rejected_in_chinese() -> None:
    with pytest.raises(DetailReparseError, match="不支持的详情优先级筛选"):
        _detail_priority_filter("score-desc")


def test_page_missing_does_not_treat_history_actions_as_new_fields() -> None:
    item = {
        "evidence_status": "partial",
        "detail_collected_at": datetime(2026, 8, 10, 9, 0),
    }
    local = {
        "status": "page_missing",
        "path": "details/B0TEST0001.html",
        "can_apply": True,
        "_captured_at": datetime(2026, 8, 10, 9, 0),
        "changes": [
            {"field": "offer", "action": "snapshot"},
            {"field": "variants", "action": "history"},
            {"field": "date_first_available", "action": "missing"},
        ],
    }

    _decorate_detail_gap_disposition(item, local, local_evidence_checked=True)

    action = item["recommended_action"]
    assert action["code"] == "page_missing"
    assert action["kind"] == "hold"
    assert action["accesses_amazon"] is False
    assert action["primary_command"] == "preview_local"
    assert action["useful_local_change_count"] == 0
    assert "重复采集未必有效" in action["reason"]


def test_local_fill_is_replayed_but_parser_marker_requires_review_first() -> None:
    base = {
        "evidence_status": "partial",
        "detail_collected_at": datetime(2026, 8, 10, 9, 0),
    }
    replay = {
        "status": "page_missing",
        "path": "details/B0TEST0001.html",
        "can_apply": True,
        "_captured_at": datetime(2026, 8, 10, 9, 0),
        "changes": [{"field": "category_path", "action": "fill"}],
    }
    replay_item = dict(base)
    _decorate_detail_gap_disposition(replay_item, replay, local_evidence_checked=True)
    assert replay_item["recommended_action"]["code"] == "replay_local"
    assert replay_item["recommended_action"]["useful_local_change_count"] == 1

    parser_item = dict(base)
    parser_local = {**replay, "status": "parser_unrecognized"}
    _decorate_detail_gap_disposition(parser_item, parser_local, local_evidence_checked=True)
    assert parser_item["recommended_action"]["code"] == "adapt_parser"
    assert parser_item["recommended_action"]["accesses_amazon"] is False


@pytest.mark.parametrize(
    ("evidence_status", "expected_code", "button_label"),
    [
        ("not_collected", "collect_first", "首次采集"),
        ("stale", "refresh_stale", "刷新详情"),
        ("partial", "collect_gap", "补采详情"),
    ],
)
def test_database_only_disposition_requires_explicit_single_product_action(
    evidence_status: str,
    expected_code: str,
    button_label: str,
) -> None:
    item = {"evidence_status": evidence_status, "detail_collected_at": None}
    _decorate_detail_gap_disposition(item, None, local_evidence_checked=True)

    action = item["recommended_action"]
    assert action["code"] == expected_code
    assert action["button_label"] == button_label
    assert action["accesses_amazon"] is True
    assert action["user_confirmation_required"] is True
    assert action["automatic"] is False


def test_incomplete_local_scan_does_not_claim_that_no_local_evidence_exists() -> None:
    item = {"evidence_status": "not_collected", "detail_collected_at": None}
    _decorate_detail_gap_disposition(
        item,
        None,
        local_evidence_checked=True,
        local_evidence_scan_complete=False,
    )

    action = item["recommended_action"]
    assert action["local_evidence_checked"] is True
    assert action["local_evidence_scan_complete"] is False
    assert "当前扫描范围未发现" in action["reason"]
    assert "已完成本地详情目录核对" not in action["reason"]


def test_disposition_summary_is_explicitly_current_page_only() -> None:
    rows = []
    for status in ("not_collected", "stale", "partial"):
        item = {"evidence_status": status, "detail_collected_at": None}
        _decorate_detail_gap_disposition(item, None, local_evidence_checked=True)
        rows.append(item)

    summary = _detail_disposition_summary(rows)

    assert summary["scope"] == "current_page"
    assert summary["row_total"] == 3
    assert summary["network_action_total"] == 3
    assert summary["local_evidence_checked_total"] == 3
    assert summary["local_evidence_scan_complete_total"] == 3
