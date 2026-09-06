from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.research_report_versions as versions  # noqa: E402
import api.routers.research_reports as report_router  # noqa: E402
from api.app import app  # noqa: E402


FINGERPRINT_A = "A" * 64
FINGERPRINT_B = "B" * 64
api_client = TestClient(app)


def _report(*, fingerprint: str = FINGERPRINT_A) -> dict:
    return {
        "report_type": "research_project_decision_report",
        "schema_version": "1.1",
        "method_version": "research-decision-report-v1.2",
        "generated_at": "2026-07-26T10:00:00+08:00",
        "evaluated_on": "2026-07-26",
        "evidence_as_of": "2026-07-24 21:33:24",
        "evidence_fingerprint": FINGERPRINT_A,
        "report_fingerprint": fingerprint,
        "project": {
            "id": 4,
            "name": "减压玩具验证",
            "objective": "验证真实市场机会",
            "strategy": "差异化",
            "decision_summary": None,
        },
        "readiness": {
            "level": "reviewable",
            "label": "可进入人工复核",
            "passed_count": 8,
            "total_count": 10,
            "blocking_count": 2,
            "summary": "仍缺趋势与财务输入。",
            "gates": [
                {
                    "key": "trend_evidence",
                    "label": "趋势证据",
                    "passed": False,
                    "severity": "blocker",
                    "critical_for_final_decision": True,
                    "detail": "只有两个时间点。",
                    "action": "继续积累。",
                }
            ],
        },
        "decision_axes": [
            {
                "key": "demand",
                "label": "需求证据",
                "status": "supporting",
                "confidence": "medium",
                "summary": "需求代理较强。",
                "caveats": [],
                "facts": [
                    {
                        "key": "observed_products",
                        "label": "去重观察商品",
                        "value": 291,
                        "display": "291 个 ASIN",
                        "source_type": "niche_snapshot",
                        "source_id": 10,
                        "source_at": "2026-07-24 21:33:24",
                    }
                ],
            }
        ],
        "data_gaps": [
            {
                "key": "trend_evidence",
                "title": "趋势证据",
                "severity": "blocker",
                "reason": "时间不足",
                "next_action": "继续积累",
                "route": "#/tracking",
                "requires_user_action": True,
            }
        ],
        "assets": {
            "products": [
                {
                    "product_id": 1,
                    "asin": "B0H2DMY1QZ",
                    "title": "Candidate",
                    "role": "candidate",
                    "price": 12.99,
                    "timepoint_count": 2,
                }
            ],
            "keywords": [
                {
                    "keyword_id": 2,
                    "keyword": "squishy",
                    "role": "core",
                    "timepoint_count": 2,
                }
            ],
            "niches": [
                {
                    "niche_id": 4,
                    "name": "减压玩具",
                    "role": "primary",
                    "snapshot_id": 10,
                }
            ],
            "notes": [
                {
                    "id": 5,
                    "note_type": "opportunity",
                    "content": "存在差异化方向",
                    "updated_at": "2026-07-24 21:00:00",
                }
            ],
        },
        "source_manifest": {
            "project": {"id": 4, "objective": "验证真实市场机会"},
            "products": [{"product_id": 1, "asin": "B0H2DMY1QZ", "latest_product_snapshot_id": 11}],
            "keywords": [{"keyword_id": 2, "keyword": "squishy", "latest_snapshot_at": "2026-07-21"}],
            "niches": [{"niche_id": 4, "name": "减压玩具", "snapshot_id": 10}],
            "notes": [{"id": 5, "note_type": "opportunity", "content": "存在差异化方向"}],
        },
    }


def _version(version_no: int, report: dict, **patch) -> dict:
    value = {
        "id": version_no,
        "project_id": 4,
        "version_no": version_no,
        "freeze_kind": "manual",
        "source_project_status": "collecting",
        "decision_status": None,
        "decision_summary": None,
        "evaluated_on": report["evaluated_on"],
        "schema_version": report["schema_version"],
        "method_version": report["method_version"],
        "evidence_as_of": report["evidence_as_of"],
        "evidence_fingerprint": report["evidence_fingerprint"],
        "report_fingerprint": report["report_fingerprint"],
        "version_note": None,
        "frozen_at": f"2026-07-26 10:0{version_no}:00",
    }
    value.update(patch)
    return {"version": value, "report": report}


class _FakeCursor:
    def __init__(self, client: "_FakeClient") -> None:
        self.client = client
        self.one = None
        self.rows = []
        self.lastrowid = 0
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, sql, params=None) -> None:
        normalized = " ".join(str(sql).split())
        values = tuple(params or ())
        self.one = None
        self.rows = []
        self.rowcount = 0
        if "idempotency_key = %s" in normalized:
            project_id, key = values
            self.one = next(
                (
                    deepcopy(row)
                    for row in self.client.versions
                    if row["project_id"] == project_id and row["idempotency_key"] == key
                ),
                None,
            )
            return
        if "FROM research_projects" in normalized and "FOR UPDATE" in normalized:
            self.one = deepcopy(self.client.project) if values[0] == self.client.project["id"] else None
            return
        if "FROM research_projects" in normalized:
            self.one = deepcopy(self.client.project) if values[0] == self.client.project["id"] else None
            return
        if "COALESCE(MAX(version_no), 0) + 1" in normalized:
            current = [
                row["version_no"]
                for row in self.client.versions
                if row["project_id"] == values[0]
            ]
            self.one = {"next_version": max(current, default=0) + 1}
            return
        if normalized.startswith("INSERT INTO research_project_report_versions"):
            if self.client.fail_insert:
                raise RuntimeError("insert failed")
            keys = (
                "project_id",
                "version_no",
                "freeze_kind",
                "source_project_status",
                "decision_status",
                "decision_summary",
                "evaluated_on",
                "schema_version",
                "method_version",
                "evidence_as_of",
                "evidence_fingerprint",
                "report_fingerprint",
                "report_json",
                "report_json_sha256",
                "report_markdown",
                "report_markdown_sha256",
                "version_note",
                "idempotency_key",
                "frozen_at",
            )
            row = dict(zip(keys, values, strict=True))
            row["id"] = len(self.client.versions) + 1
            self.client.versions.append(row)
            self.lastrowid = row["id"]
            self.rowcount = 1
            return
        if normalized.startswith("UPDATE research_projects"):
            if self.client.fail_update:
                raise RuntimeError("update failed")
            (
                status,
                summary,
                decided_at,
                status_changed_at,
                version_id,
                project_id,
            ) = values
            assert project_id == self.client.project["id"]
            self.client.project.update(
                {
                    "status": status,
                    "decision_summary": summary,
                    "decided_at": decided_at,
                    "status_changed_at": status_changed_at,
                    "current_decision_report_version_id": version_id,
                }
            )
            self.rowcount = 1
            return
        if "FROM research_project_report_versions" in normalized and "WHERE project_id = %s AND id = %s" in normalized:
            project_id, version_id = values
            self.one = next(
                (
                    deepcopy(row)
                    for row in self.client.versions
                    if row["project_id"] == project_id and row["id"] == version_id
                ),
                None,
            )
            return
        if "FROM research_project_report_versions" in normalized and "WHERE project_id = %s AND version_no = %s" in normalized:
            project_id, version_no = values
            self.one = next(
                (
                    deepcopy(row)
                    for row in self.client.versions
                    if row["project_id"] == project_id and row["version_no"] == version_no
                ),
                None,
            )
            return
        if "SELECT version_no FROM research_project_report_versions" in normalized:
            project_id = values[0]
            candidates = [
                row
                for row in self.client.versions
                if row["project_id"] == project_id
            ]
            latest = max(candidates, key=lambda row: row["version_no"], default=None)
            self.one = {"version_no": latest["version_no"]} if latest else None
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, client: "_FakeClient") -> None:
        self.client = client

    def cursor(self):
        return _FakeCursor(self.client)


class _FakeClient:
    def __init__(self) -> None:
        self.project = {
            "id": 4,
            "marketplace": "US",
            "status": "manual_review",
            "current_decision_report_version_id": None,
        }
        self.versions = []
        self.fail_insert = False
        self.fail_update = False

    @contextmanager
    def connect(self):
        project_before = deepcopy(self.project)
        versions_before = deepcopy(self.versions)
        try:
            yield _FakeConnection(self)
        except Exception:
            self.project = project_before
            self.versions = versions_before
            raise


def test_canonical_json_is_order_independent() -> None:
    first = {"b": 2, "a": {"d": 4, "c": 3}}
    second = {"a": {"c": 3, "d": 4}, "b": 2}
    assert versions.canonical_report_json(first) == versions.canonical_report_json(second)
    assert len(versions.content_sha256(versions.canonical_report_json(first))) == 64


def test_diff_uses_stable_facts_and_separates_method_changes() -> None:
    before = _report()
    after = deepcopy(before)
    after["decision_axes"][0]["facts"][0]["value"] = 320
    after["decision_axes"][0]["facts"][0]["display"] = "320 个 ASIN"
    after["report_fingerprint"] = FINGERPRINT_B
    result = versions.build_research_report_diff(_version(1, before), _version(2, after))
    fact_change = next(
        row
        for row in result["changes"]
        if row["item_key"] == "observed_products" and row["field"] == "value"
    )
    assert result["comparison_scope"] == "evidence_changed"
    assert result["business_interpretation_allowed"] is True
    assert fact_change["delta"] == 29

    changed_method = deepcopy(after)
    changed_method["method_version"] = "research-decision-report-v2.0"
    changed_method["report_fingerprint"] = "C" * 64
    method_result = versions.build_research_report_diff(
        _version(2, after),
        _version(3, changed_method),
    )
    assert method_result["comparison_scope"] == "method_changed"
    assert method_result["business_interpretation_allowed"] is False
    assert "不能直接解释" in method_result["warning"]


def test_current_report_compares_with_latest_frozen_baseline(monkeypatch) -> None:
    before = _report()
    current = deepcopy(before)
    current["evidence_fingerprint"] = FINGERPRINT_B
    current["report_fingerprint"] = FINGERPRINT_B
    current["readiness"]["passed_count"] = 9
    current["readiness"]["blocking_count"] = 1
    current["readiness"]["summary"] = "仅剩财务输入。"

    db = _FakeClient()
    monkeypatch.setattr(
        versions,
        "build_research_decision_report_from_cursor",
        lambda *_args, **_kwargs: deepcopy(before),
    )
    monkeypatch.setattr(versions, "render_research_decision_markdown", lambda _report: "# report")
    versions.freeze_research_report_version(
        4,
        confirmed=True,
        evaluated_on="2026-07-26",
        expected_report_fingerprint=FINGERPRINT_A,
        idempotency_key="baseline-freeze-0001",
        client=db,
        frozen_at=datetime.fromisoformat("2026-07-26T10:00:00+08:00"),
    )
    monkeypatch.setattr(
        versions,
        "build_research_decision_report_from_cursor",
        lambda *_args, **_kwargs: deepcopy(current),
    )

    result = versions.compare_current_research_report_to_baseline(4, client=db)

    assert result["comparison_type"] == versions.LIVE_DIFF_SCHEMA_VERSION
    assert result["has_baseline"] is True
    assert result["baseline_kind"] == "latest_frozen"
    assert result["baseline"]["version_no"] == 1
    assert result["current"]["freeze_kind"] == "dynamic"
    assert result["current_report"]["report_fingerprint"] == FINGERPRINT_B
    assert result["diff"]["comparison_scope"] == "evidence_changed"
    assert result["diff"]["evidence_fingerprint_changed"] is True


def test_terminal_project_without_bound_decision_version_never_falls_back(monkeypatch) -> None:
    db = _FakeClient()
    db.project["status"] = "approved"
    db.project["current_decision_report_version_id"] = None
    db.versions.append({"id": 9, "project_id": 4, "version_no": 3})
    monkeypatch.setattr(
        versions,
        "build_research_decision_report_from_cursor",
        lambda *_args, **_kwargs: deepcopy(_report()),
    )

    result = versions.compare_current_research_report_to_baseline(4, client=db)

    assert result["has_baseline"] is False
    assert result["baseline_kind"] == "terminal_missing"
    assert result["baseline"] is None
    assert result["diff"] is None


def test_manual_freeze_is_idempotent_and_rejects_stale_evidence(monkeypatch) -> None:
    db = _FakeClient()
    report = _report()
    monkeypatch.setattr(
        versions,
        "build_research_decision_report_from_cursor",
        lambda *_args, **_kwargs: deepcopy(report),
    )
    monkeypatch.setattr(versions, "render_research_decision_markdown", lambda _report: "# report")
    kwargs = {
        "confirmed": True,
        "evaluated_on": "2026-07-26",
        "expected_report_fingerprint": FINGERPRINT_A,
        "idempotency_key": "freeze-key-0001",
        "version_note": "首次人工复核",
        "client": db,
        "frozen_at": datetime.fromisoformat("2026-07-26T10:00:00+08:00"),
    }
    first = versions.freeze_research_report_version(4, **kwargs)
    replay = versions.freeze_research_report_version(4, **kwargs)

    assert first["version"]["version_no"] == 1
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert len(db.versions) == 1

    try:
        versions.freeze_research_report_version(
            4,
            **{**kwargs, "idempotency_key": "freeze-key-0002", "expected_report_fingerprint": FINGERPRINT_B},
        )
    except versions.ResearchReportVersionConflict as exc:
        assert "证据已变化" in str(exc)
        assert exc.details["current_report_fingerprint"] == FINGERPRINT_A
    else:
        raise AssertionError("stale report fingerprint must be rejected")
    assert len(db.versions) == 1


def test_terminal_freeze_and_status_update_are_atomic(monkeypatch) -> None:
    report = _report()
    monkeypatch.setattr(
        versions,
        "build_research_decision_report_from_cursor",
        lambda *_args, **_kwargs: deepcopy(report),
    )
    monkeypatch.setattr(versions, "render_research_decision_markdown", lambda _report: "# report")
    monkeypatch.setattr(
        versions,
        "get_research_project",
        lambda project_id, client=None: {"project": deepcopy(client.project)},
    )
    kwargs = {
        "decision_summary": "证据达到人工复核条件，但供应链与财务仍需线下确认。",
        "confirmed": True,
        "evaluated_on": "2026-07-26",
        "expected_report_fingerprint": FINGERPRINT_A,
        "idempotency_key": "decision-key-0001",
        "version_note": "阶段决策",
        "frozen_at": datetime.fromisoformat("2026-07-26T10:00:00+08:00"),
    }

    db = _FakeClient()
    result = versions.freeze_research_project_decision(4, "approved", client=db, **kwargs)
    assert db.project["status"] == "approved"
    assert db.project["current_decision_report_version_id"] == 1
    assert result["frozen_version"]["decision_status"] == "approved"
    assert len(db.versions) == 1

    failing = _FakeClient()
    failing.fail_update = True
    try:
        versions.freeze_research_project_decision(4, "approved", client=failing, **kwargs)
    except RuntimeError as exc:
        assert "update failed" in str(exc)
    else:
        raise AssertionError("failed terminal update must roll back")
    assert failing.project["status"] == "manual_review"
    assert failing.versions == []


def test_migration_is_append_only_and_down_retains_history_table() -> None:
    forward = (
        ROOT / "database" / "migrations" / "20260726_research_report_versions_v1.sql"
    ).read_text(encoding="utf-8")
    down = (
        ROOT / "database" / "migrations" / "20260726_research_report_versions_v1.down.sql"
    ).read_text(encoding="utf-8")
    assert "uk_research_report_project_version (project_id, version_no)" in forward
    assert "uk_research_report_project_idempotency (project_id, idempotency_key)" in forward
    assert "current_decision_report_version_id" in forward
    assert "DROP COLUMN current_decision_report_version_id" in down
    assert "DROP TABLE" not in down.upper()


def test_report_version_routes_keep_envelopes_and_frozen_headers(monkeypatch) -> None:
    report = _report()
    detail = _version(1, report)

    class FakeRepository:
        def freeze_version(self, project_id, **kwargs):
            assert project_id == 4
            assert kwargs["confirmed"] is True
            assert kwargs["expected_report_fingerprint"] == FINGERPRINT_A
            return {"version": detail["version"], "idempotent_replay": False}

        def list_versions(self, project_id, *, limit=50, offset=0):
            assert (project_id, limit, offset) == (4, 25, 0)
            return {"rows": [detail["version"]], "total": 1, "limit": 25, "offset": 0}

        def get_version(self, project_id, version_no):
            assert (project_id, version_no) == (4, 1)
            return detail

        def compare_versions(self, project_id, from_version, to_version):
            assert (project_id, from_version, to_version) == (4, 1, 2)
            return {"diff_type": versions.DIFF_SCHEMA_VERSION, "changes": []}

        def compare_current_to_baseline(self, project_id, *, from_version=None, as_of=None):
            assert (project_id, from_version, as_of) == (4, 1, "2026-08-07")
            return {
                "comparison_type": versions.LIVE_DIFF_SCHEMA_VERSION,
                "has_baseline": True,
                "baseline": detail["version"],
                "current_report": report,
                "diff": {"diff_type": versions.DIFF_SCHEMA_VERSION, "changes": []},
            }

        def export_version(self, project_id, version_no, export_format):
            assert (project_id, version_no, export_format) == (4, 1, "markdown")
            return b"# frozen", "frozen.md", "text/markdown; charset=utf-8", detail

    monkeypatch.setattr(report_router, "_repository", FakeRepository())
    frozen = api_client.post(
        "/api/research-projects/4/report-versions",
        json={
            "confirmed": True,
            "evaluated_on": "2026-07-26",
            "expected_report_fingerprint": FINGERPRINT_A,
            "idempotency_key": "freeze-route-0001",
            "version_note": "阶段复核",
        },
    )
    listed = api_client.get(
        "/api/research-projects/4/report-versions",
        params={"limit": 25, "offset": 0},
    )
    read = api_client.get("/api/research-projects/4/report-versions/1")
    compared = api_client.get(
        "/api/research-projects/4/report-version-diff",
        params={"from_version": 1, "to_version": 2},
    )
    live_compared = api_client.get(
        "/api/research-projects/4/report-baseline-diff",
        params={"from_version": 1, "as_of": "2026-08-07"},
    )
    exported = api_client.get(
        "/api/research-projects/4/report-versions/1/export",
        params={"format": "markdown"},
    )

    assert frozen.status_code == 200
    assert frozen.json()["data"]["version"]["version_no"] == 1
    assert listed.json()["data"]["total"] == 1
    assert read.json()["data"]["report"]["report_fingerprint"] == FINGERPRINT_A
    assert compared.json()["data"]["diff_type"] == versions.DIFF_SCHEMA_VERSION
    assert live_compared.status_code == 200
    assert live_compared.json()["data"]["comparison_type"] == versions.LIVE_DIFF_SCHEMA_VERSION
    assert exported.text == "# frozen"
    assert exported.headers["x-report-version"] == "1"
    assert exported.headers["x-report-fingerprint"] == FINGERPRINT_A
