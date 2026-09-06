from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.research_workspace import (
    PROJECT_TRANSITIONS,
    ResearchProjectError,
    _normalize_asins,
    _normalize_keywords,
    _normalize_project_row,
    add_research_project_products,
    ensure_project_mutable,
    normalize_project_name,
    set_research_project_status,
    validate_project_transition,
)


class _FrozenProjectCursor:
    def __init__(self) -> None:
        self.last_sql = ""
        self.executed_sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, sql, params=None) -> None:
        self.last_sql = " ".join(str(sql).split())
        self.executed_sql.append(self.last_sql)

    def fetchone(self):
        if "FROM research_projects" in self.last_sql:
            return {"id": 9, "marketplace": "US", "status": "approved"}
        return None


class _FrozenProjectConnection:
    def __init__(self, cursor: _FrozenProjectCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self):
        return self._cursor


class _FrozenProjectClient:
    def __init__(self) -> None:
        self.cursor = _FrozenProjectCursor()

    def connect(self):
        return _FrozenProjectConnection(self.cursor)


def test_project_name_normalization_is_stable() -> None:
    assert normalize_project_name("  Squishy   Toys  ") == "squishy toys"
    assert normalize_project_name("SQUISHY toys") == "squishy toys"


def test_asset_input_normalization_deduplicates() -> None:
    assert _normalize_asins([" b0fbxzyrg9, B0FBXZYRG9 "]) == ["B0FBXZYRG9"]
    assert _normalize_keywords(["Cow  Squishy\n cow squishy", "Mini Squishy"]) == {
        "cow squishy": "Cow Squishy",
        "mini squishy": "Mini Squishy",
    }


def test_invalid_asin_is_rejected() -> None:
    try:
        _normalize_asins(["not-an-asin"])
    except ResearchProjectError as exc:
        assert "ASIN 格式" in str(exc)
    else:
        raise AssertionError("invalid ASIN should be rejected")


def test_status_transition_is_explicit() -> None:
    assert "validating" in validate_project_transition("collecting", "validating")
    try:
        validate_project_transition("idea", "approved")
    except ResearchProjectError as exc:
        assert "不能从" in str(exc)
    else:
        raise AssertionError("illegal status jump should be rejected")
    assert set(PROJECT_TRANSITIONS) == {
        "idea", "collecting", "validating", "candidate", "manual_review", "approved", "rejected"
    }


def test_terminal_decision_requires_explanation_before_database_access() -> None:
    try:
        set_research_project_status(1, "approved", decision_summary="太短", client=object())
    except ResearchProjectError as exc:
        assert "不少于 10 个字符" in str(exc)
    else:
        raise AssertionError("short terminal decision should be rejected")


def test_terminal_project_evidence_is_frozen_until_reopened() -> None:
    assert ensure_project_mutable({"status": "manual_review"})["status"] == "manual_review"
    assert "manual_review" in validate_project_transition("approved", "manual_review")
    assert "idea" in validate_project_transition("rejected", "idea")
    for status in ("approved", "rejected"):
        try:
            ensure_project_mutable({"status": status})
        except ResearchProjectError as exc:
            assert "已冻结" in str(exc)
            assert "先将项目退回" in str(exc)
        else:
            raise AssertionError(f"{status} project should be frozen")


def test_direct_product_association_rejects_terminal_project_before_write() -> None:
    client = _FrozenProjectClient()
    try:
        add_research_project_products(9, ["B0FBXZYRG9"], client=client)
    except ResearchProjectError as exc:
        assert "已冻结" in str(exc)
    else:
        raise AssertionError("terminal project should reject direct product association")
    assert not any(
        sql.startswith(("INSERT ", "UPDATE ", "DELETE "))
        for sql in client.cursor.executed_sql
    )


def test_migration_contains_relation_deduplication() -> None:
    sql = (ROOT / "database" / "migrations" / "20260713_research_workspace_v1.sql").read_text(encoding="utf-8")
    assert "uk_research_project_product (project_id, product_id)" in sql
    assert "uk_research_project_keyword (project_id, keyword_id)" in sql
    assert "uk_research_project_note_hash (project_id, note_type, content_hash)" in sql


def test_project_evidence_coverage_uses_individual_product_fields() -> None:
    row = _normalize_project_row(
        {
            "product_count": 1,
            "detail_collected_count": 1,
            "product_evidence_points": 2,
            "product_evidence_total": 3,
            "keyword_count": 1,
            "keyword_with_snapshots": 1,
        }
    )
    assert row["evidence_coverage"] == 75.0


if __name__ == "__main__":
    tests = [
        test_project_name_normalization_is_stable,
        test_asset_input_normalization_deduplicates,
        test_invalid_asin_is_rejected,
        test_status_transition_is_explicit,
        test_terminal_decision_requires_explanation_before_database_access,
        test_terminal_project_evidence_is_frozen_until_reopened,
        test_direct_product_association_rejects_terminal_project_before_write,
        test_migration_contains_relation_deduplication,
        test_project_evidence_coverage_uses_individual_product_fields,
    ]
    for test in tests:
        test()
    print(f"research workspace tests passed: {len(tests)}/{len(tests)}")
