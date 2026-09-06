from __future__ import annotations

from datetime import date, datetime
import json

from services import research_detail_readiness as readiness


NOW = datetime(2026, 8, 16, 12, 0, 0)


def _product(
    asin: str,
    *,
    role: str = "candidate",
    detail_at: datetime | None = datetime(2026, 8, 10, 9, 0, 0),
    category: str | None = "Toys & Games > Squeeze Toys",
    available: date | None = date(2026, 5, 1),
    bsr_count: int = 1,
) -> dict:
    return {
        "product_id": int(asin[-1]),
        "asin": asin,
        "title": f"Product {asin}",
        "title_zh": None,
        "role": role,
        "detail_collected_at": detail_at,
        "category_path": category,
        "date_first_available": available,
        "bsr_count": bsr_count,
    }


def _detail(products: list[dict]) -> dict:
    return {
        "project": {
            "id": 4,
            "name": "减压玩具验证",
            "marketplace": "US",
            "status": "collecting",
            "status_label": "收集证据",
        },
        "products": products,
    }


def test_project_readiness_keeps_status_actions_and_report_gate_separate() -> None:
    products = [
        _product("B0TEST0001"),
        _product("B0TEST0002", available=None),
        _product(
            "B0TEST0003",
            detail_at=None,
            category=None,
            available=None,
            bsr_count=0,
        ),
        _product("B0TEST0004", role="reference", detail_at=datetime(2026, 6, 1, 9, 0, 0)),
    ]
    local = {
        "asin": "B0TEST0002",
        "path": "private/details/B0TEST0002.html",
        "status": "page_missing",
        "can_apply": True,
        "captured_at": "2026-08-10 09:00:00",
        "_captured_at": datetime(2026, 8, 10, 9, 0, 0),
        "coverage": {"date_first_available": {"status": "page_missing"}},
        "changes": [
            {"field": "date_first_available", "action": "missing"},
            {"field": "category_path", "action": "update"},
        ],
    }

    result = readiness.assemble_research_project_detail_readiness(
        _detail(products),
        local_items=[local],
        now=NOW,
        stale_days=30,
        local_evidence_checked=True,
        local_evidence_scan_complete=True,
        local_file_count=18,
        local_file_limit=100,
    )

    rows = {row["asin"]: row for row in result["rows"]}
    assert rows["B0TEST0001"]["evidence_status"] == "ready"
    assert rows["B0TEST0001"]["recommended_action"]["code"] == "current_ready"
    assert rows["B0TEST0002"]["recommended_action"]["code"] == "page_missing"
    assert rows["B0TEST0002"]["recommended_action"]["useful_local_change_count"] == 0
    assert rows["B0TEST0002"]["recommended_action"]["requires_amazon"] is False
    assert rows["B0TEST0003"]["recommended_action"]["code"] == "collect_first"
    assert rows["B0TEST0004"]["recommended_action"]["code"] == "refresh_stale"
    assert "path" not in rows["B0TEST0002"]

    summary = result["summary"]
    assert summary["level"] == "action_required"
    assert summary["product_total"] == 4
    assert summary["ready_total"] == 1
    assert summary["partial_total"] == 1
    assert summary["not_collected_total"] == 1
    assert summary["stale_total"] == 1
    assert summary["network_action_total"] == 2
    assert summary["hold_total"] == 1
    assert summary["next_action"]["code"] == "collect_first"
    assert result["policy"]["numeric_score"] is False
    assert result["policy"]["changes_decision_gate"] is False
    assert result["policy"]["changes_report_fingerprint"] is False
    assert result["local_scan"]["complete"] is True


def test_missing_bsr_can_use_a_local_snapshot_only_when_it_fills_project_gap() -> None:
    product = _product("B0TEST0005", bsr_count=0)
    local = {
        "asin": "B0TEST0005",
        "path": "private/details/B0TEST0005.html",
        "status": "page_missing",
        "can_apply": True,
        "captured_at": "2026-08-10 09:00:00",
        "_captured_at": datetime(2026, 8, 10, 9, 0, 0),
        "coverage": {"best_seller_ranks": {"status": "collected"}},
        "changes": [{"field": "best_seller_ranks", "action": "snapshot"}],
    }

    result = readiness.assemble_research_project_detail_readiness(
        _detail([product]),
        local_items=[local],
        now=NOW,
        local_evidence_checked=True,
        local_evidence_scan_complete=True,
    )

    action = result["rows"][0]["recommended_action"]
    assert action["code"] == "replay_local"
    assert action["useful_local_change_count"] == 1
    assert action["requires_amazon"] is False
    assert result["summary"]["local_action_total"] == 1


def test_agent_compact_readiness_never_exposes_local_paths() -> None:
    product = _product("B0TEST0006", available=None)
    local = {
        "asin": "B0TEST0006",
        "path": "C:/private/details/B0TEST0006.html",
        "status": "page_missing",
        "can_apply": True,
        "captured_at": "2026-08-10 09:00:00",
        "_captured_at": datetime(2026, 8, 10, 9, 0, 0),
        "coverage": {"date_first_available": {"status": "page_missing"}},
        "changes": [],
    }
    full = readiness.assemble_research_project_detail_readiness(
        _detail([product]),
        local_items=[local],
        now=NOW,
        local_evidence_checked=True,
        local_evidence_scan_complete=True,
    )

    compact = readiness.compact_research_project_detail_readiness(full)
    encoded = json.dumps(compact, ensure_ascii=False)
    assert compact["rows"][0]["recommended_action"]["code"] == "page_missing"
    assert "C:/private" not in encoded
    assert '"path"' not in encoded
    assert "routes" not in compact


def test_loader_clamps_scan_and_reuses_cached_analysis(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        readiness,
        "get_research_project",
        lambda project_id, *, client=None: _detail([]),
    )
    monkeypatch.setattr(
        readiness,
        "list_detail_html_files",
        lambda *, limit: calls.append(("list", limit)) or [],
    )
    monkeypatch.setattr(
        readiness,
        "analyze_detail_html_files_cached",
        lambda paths, **kwargs: (
            [],
            {
                "hit_count": 0,
                "miss_count": 0,
                "duration_ms": 3,
                "warning": "C:/private/cache.json access denied",
            },
        ),
    )

    result = readiness.get_research_project_detail_readiness(
        4,
        file_limit=999,
        client=object(),
        now=NOW,
    )

    assert calls == [("list", readiness.MAX_SCAN_FILES)]
    assert result["summary"]["level"] == "empty"
    assert result["local_scan"]["complete"] is True
    assert result["local_scan"]["cache"]["duration_ms"] == 3
    assert result["local_scan"]["cache"]["warning"] == "本地缓存读写异常，本次结果仍可使用。"
    assert "C:/private" not in json.dumps(result, ensure_ascii=False)
