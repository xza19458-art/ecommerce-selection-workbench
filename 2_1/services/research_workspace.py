"""Research project workspace backed by existing product and keyword assets."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient


PROJECT_STATUSES = (
    "idea",
    "collecting",
    "validating",
    "candidate",
    "manual_review",
    "approved",
    "rejected",
)
PROJECT_STATUS_LABELS = {
    "idea": "方向构想",
    "collecting": "收集证据",
    "validating": "验证中",
    "candidate": "候选方向",
    "manual_review": "人工复核",
    "approved": "已批准",
    "rejected": "已拒绝",
}
PROJECT_TRANSITIONS = {
    "idea": ("collecting", "rejected"),
    "collecting": ("idea", "validating", "rejected"),
    "validating": ("collecting", "candidate", "rejected"),
    "candidate": ("validating", "manual_review", "approved", "rejected"),
    "manual_review": ("candidate", "approved", "rejected"),
    "approved": ("manual_review",),
    "rejected": ("idea",),
}
PRODUCT_ROLES = {"candidate", "benchmark", "competitor", "reference"}
KEYWORD_ROLES = {"seed", "candidate", "core", "long_tail", "reference"}
NOTE_TYPES = {"observation", "opportunity", "risk", "decision"}
TERMINAL_STATUSES = {"approved", "rejected"}
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")


class ResearchProjectError(ValueError):
    """Raised when research project input or state transitions are invalid."""


def normalize_project_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def normalize_keyword(value: str | None) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def validate_project_transition(current: str, target: str) -> tuple[str, ...]:
    current_value = _status(current)
    target_value = _status(target)
    allowed = PROJECT_TRANSITIONS[current_value]
    if target_value != current_value and target_value not in allowed:
        raise ResearchProjectError(
            f"不能从“{PROJECT_STATUS_LABELS[current_value]}”直接变更为“{PROJECT_STATUS_LABELS[target_value]}”"
        )
    return allowed


def ensure_project_mutable(project: dict[str, Any]) -> dict[str, Any]:
    status = str(project.get("status") or "")
    if status in TERMINAL_STATUSES:
        label = PROJECT_STATUS_LABELS.get(status, status)
        target = "人工复核" if status == "approved" else "方向构想"
        raise ResearchProjectError(
            f"项目当前为“{label}”，证据与项目定义已冻结；请先将项目退回“{target}”再修改"
        )
    return project


def create_research_project(
    name: str,
    *,
    marketplace: str = "US",
    objective: str | None = None,
    strategy: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    display_name = _required_text(name, "项目名称", maximum=255)
    normalized_name = normalize_project_name(display_name)
    marketplace_value = _marketplace(marketplace)
    objective_value = _optional_text(objective, maximum=5000)
    strategy_value = _optional_text(strategy, maximum=64)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM research_projects
                WHERE marketplace = %s AND normalized_name = %s
                LIMIT 1
                """,
                (marketplace_value, normalized_name),
            )
            existing = cursor.fetchone()
            if existing:
                raise ResearchProjectError(f"同站点已存在同名研究项目（ID {existing['id']}）")
            cursor.execute(
                """
                INSERT INTO research_projects (
                  marketplace, name, normalized_name, status, objective, strategy
                ) VALUES (%s, %s, %s, 'idea', %s, %s)
                """,
                (marketplace_value, display_name, normalized_name, objective_value, strategy_value),
            )
            project_id = int(cursor.lastrowid)
    return get_research_project(project_id, client=db)


def fetch_research_projects_page(
    limit: int = 50,
    *,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    marketplace_value = _marketplace(marketplace)
    limit_value = _bounded_int(limit, default=50, minimum=1, maximum=200)
    offset_value = _bounded_int(offset, default=0, minimum=0, maximum=10_000_000)
    where = ["rp.marketplace = %s"]
    params: list[Any] = [marketplace_value]
    if status:
        where.append("rp.status = %s")
        params.append(_status(status))
    keyword_value = " ".join(str(keyword or "").strip().split())
    if keyword_value:
        where.append("(rp.name LIKE %s OR rp.objective LIKE %s OR rp.strategy LIKE %s)")
        pattern = f"%{keyword_value}%"
        params.extend([pattern, pattern, pattern])
    order_sql, normalized_sort, normalized_dir = _project_order(sort_by, sort_dir)
    where_sql = " AND ".join(where)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM research_projects rp WHERE {where_sql}",
                params,
            )
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                {_project_summary_select()}
                WHERE {where_sql}
                {order_sql}
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_normalize_project_row(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM research_projects
                WHERE marketplace = %s
                GROUP BY status
                """,
                (marketplace_value,),
            )
            status_counts = {key: 0 for key in PROJECT_STATUSES}
            for row in cursor.fetchall():
                status_counts[str(row["status"])] = int(row["total"] or 0)

    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "marketplace": marketplace_value,
        "sort_by": normalized_sort,
        "sort_dir": normalized_dir,
        "status_counts": status_counts,
        "status_labels": PROJECT_STATUS_LABELS,
    }


def get_research_project(project_id: int, *, client: MySQLClient | None = None) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            return load_research_project_bundle(cursor, project_id_value)


def load_research_project_bundle(cursor: Any, project_id: int) -> dict[str, Any]:
    """Load one project and all of its existing relations in the caller's transaction."""
    project_id_value = _positive_int(project_id, "研究项目 ID")
    cursor.execute(
        f"{_project_summary_select()} WHERE rp.id = %s LIMIT 1",
        (project_id_value,),
    )
    project_row = cursor.fetchone()
    if not project_row:
        raise ResearchProjectError(f"研究项目不存在：{project_id_value}")
    project = _normalize_project_row(project_row)
    products = _fetch_project_products(cursor, project_id_value)
    keywords = _fetch_project_keywords(cursor, project_id_value)
    niches = _fetch_project_niches(cursor, project_id_value)
    notes = _fetch_project_notes(cursor, project_id_value)
    project["next_statuses"] = list(PROJECT_TRANSITIONS[project["status"]])
    project["next_status_labels"] = {
        value: PROJECT_STATUS_LABELS[value] for value in project["next_statuses"]
    }
    return {
        "project": project,
        "products": products,
        "keywords": keywords,
        "niches": niches,
        "notes": notes,
        "status_labels": PROJECT_STATUS_LABELS,
        "product_roles": sorted(PRODUCT_ROLES),
        "keyword_roles": sorted(KEYWORD_ROLES),
        "note_types": sorted(NOTE_TYPES),
    }


def update_research_project(
    project_id: int,
    *,
    name: str | None = None,
    objective: str | None = None,
    strategy: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    updates: list[str] = []
    params: list[Any] = []
    if name is not None:
        display_name = _required_text(name, "项目名称", maximum=255)
        updates.extend(["name = %s", "normalized_name = %s"])
        params.extend([display_name, normalize_project_name(display_name)])
    if objective is not None:
        updates.append("objective = %s")
        params.append(_optional_text(objective, maximum=5000))
    if strategy is not None:
        updates.append("strategy = %s")
        params.append(_optional_text(strategy, maximum=64))
    if not updates:
        raise ResearchProjectError("没有需要更新的项目字段")

    with db.connect() as conn:
        with conn.cursor() as cursor:
            ensure_project_mutable(_require_project(cursor, project_id_value))
            try:
                cursor.execute(
                    f"UPDATE research_projects SET {', '.join(updates)} WHERE id = %s",
                    params + [project_id_value],
                )
            except db._pymysql.err.IntegrityError as exc:
                raise ResearchProjectError("同站点已存在同名研究项目") from exc
    return get_research_project(project_id_value, client=db)


def set_research_project_status(
    project_id: int,
    status: str,
    *,
    decision_summary: str | None = None,
    confirmed: bool = False,
    evaluated_on: date | str | None = None,
    expected_report_fingerprint: str | None = None,
    idempotency_key: str | None = None,
    version_note: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    target = _status(status)
    summary = _optional_text(decision_summary, maximum=10_000)
    if target in TERMINAL_STATUSES and len(summary or "") < 10:
        raise ResearchProjectError("批准或拒绝项目前，请填写不少于 10 个字符的中文结论")
    if target in TERMINAL_STATUSES:
        from services.research_report_versions import freeze_research_project_decision

        return freeze_research_project_decision(
            project_id_value,
            target,
            decision_summary=summary,
            confirmed=confirmed,
            evaluated_on=evaluated_on,
            expected_report_fingerprint=expected_report_fingerprint or "",
            idempotency_key=idempotency_key or "",
            version_note=version_note,
            client=db,
        )

    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = _require_project(cursor, project_id_value)
            current = str(project["status"])
            validate_project_transition(current, target)
            if current in TERMINAL_STATUSES and target == current:
                ensure_project_mutable(project)
            decided_sql = "NOW()" if target in TERMINAL_STATUSES else "NULL"
            summary_sql = "decision_summary"
            params: list[Any] = [target]
            if decision_summary is not None:
                summary_sql = "%s"
                params.append(summary)
            cursor.execute(
                f"""
                UPDATE research_projects
                SET status = %s,
                    decision_summary = {summary_sql},
                    decided_at = {decided_sql},
                    status_changed_at = NOW(),
                    current_decision_report_version_id = NULL
                WHERE id = %s
                """,
                params + [project_id_value],
            )
    return get_research_project(project_id_value, client=db)


def add_research_project_products(
    project_id: int,
    asins: Iterable[str],
    *,
    role: str = "candidate",
    notes: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    role_value = _choice(role, PRODUCT_ROLES, "商品角色")
    notes_value = _optional_text(notes, maximum=5000)
    asin_values = _normalize_asins(asins)
    if not asin_values:
        raise ResearchProjectError("请至少填写一个 ASIN")

    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = ensure_project_mutable(_require_project(cursor, project_id_value))
            placeholders = ", ".join(["%s"] * len(asin_values))
            cursor.execute(
                f"""
                SELECT id, asin
                FROM products
                WHERE marketplace = %s AND asin IN ({placeholders})
                """,
                [project["marketplace"], *asin_values],
            )
            found_rows = cursor.fetchall()
            found = {str(row["asin"]).upper(): int(row["id"]) for row in found_rows}
            for asin in asin_values:
                product_id = found.get(asin)
                if not product_id:
                    continue
                cursor.execute(
                    """
                    INSERT INTO research_project_products (project_id, product_id, role, notes)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      role = VALUES(role),
                      notes = COALESCE(NULLIF(VALUES(notes), ''), notes)
                    """,
                    (project_id_value, product_id, role_value, notes_value),
                )
    missing = [asin for asin in asin_values if asin not in found]
    return {
        "linked": len(found),
        "missing": missing,
        "project": get_research_project(project_id_value, client=db),
    }


def remove_research_project_product(
    project_id: int,
    product_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    return _remove_relation(
        "research_project_products",
        project_id,
        product_id,
        asset_column="product_id",
        client=client,
    )


def add_research_project_keywords(
    project_id: int,
    keywords: Iterable[str],
    *,
    role: str = "candidate",
    notes: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    role_value = _choice(role, KEYWORD_ROLES, "关键词角色")
    notes_value = _optional_text(notes, maximum=5000)
    keyword_values = _normalize_keywords(keywords)
    if not keyword_values:
        raise ResearchProjectError("请至少填写一个已入库关键词")

    with db.connect() as conn:
        with conn.cursor() as cursor:
            project = ensure_project_mutable(_require_project(cursor, project_id_value))
            placeholders = ", ".join(["%s"] * len(keyword_values))
            cursor.execute(
                f"""
                SELECT id, keyword
                FROM keywords
                WHERE marketplace = %s
                  AND LOWER(TRIM(keyword)) IN ({placeholders})
                """,
                [project["marketplace"], *keyword_values.keys()],
            )
            found_rows = cursor.fetchall()
            found = {normalize_keyword(row["keyword"]): row for row in found_rows}
            for normalized, row in found.items():
                cursor.execute(
                    """
                    INSERT INTO research_project_keywords (project_id, keyword_id, role, notes)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      role = VALUES(role),
                      notes = COALESCE(NULLIF(VALUES(notes), ''), notes)
                    """,
                    (project_id_value, int(row["id"]), role_value, notes_value),
                )
    missing = [display for normalized, display in keyword_values.items() if normalized not in found]
    return {
        "linked": len(found),
        "missing": missing,
        "project": get_research_project(project_id_value, client=db),
    }


def remove_research_project_keyword(
    project_id: int,
    keyword_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    return _remove_relation(
        "research_project_keywords",
        project_id,
        keyword_id,
        asset_column="keyword_id",
        client=client,
    )


def add_research_project_note(
    project_id: int,
    note_type: str,
    content: str,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    note_type_value = _choice(note_type, NOTE_TYPES, "笔记类型")
    content_value = _required_content(content, "笔记内容", maximum=20_000)
    hash_source = " ".join(content_value.split()).casefold()
    content_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()

    with db.connect() as conn:
        with conn.cursor() as cursor:
            ensure_project_mutable(_require_project(cursor, project_id_value))
            cursor.execute(
                """
                INSERT INTO research_project_notes (project_id, note_type, content, content_hash)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                """,
                (project_id_value, note_type_value, content_value, content_hash),
            )
            note_id = int(cursor.lastrowid)
    return {"note_id": note_id, "project": get_research_project(project_id_value, client=db)}


def delete_research_project_note(
    project_id: int,
    note_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    note_id_value = _positive_int(note_id, "笔记 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            ensure_project_mutable(_require_project(cursor, project_id_value))
            cursor.execute(
                "DELETE FROM research_project_notes WHERE project_id = %s AND id = %s",
                (project_id_value, note_id_value),
            )
            deleted = cursor.rowcount > 0
    return {"deleted": deleted, "project": get_research_project(project_id_value, client=db)}


def _project_summary_select() -> str:
    return """
        SELECT
          rp.id,
          rp.marketplace,
          rp.name,
          rp.status,
          rp.objective,
          rp.strategy,
          rp.decision_summary,
          rp.decided_at,
          rp.current_decision_report_version_id,
          current_report.version_no AS current_decision_report_version_no,
          current_report.decision_status AS current_decision_report_status,
          current_report.frozen_at AS current_decision_report_frozen_at,
          rp.status_changed_at,
          rp.created_at,
          rp.updated_at,
          COALESCE(product_stats.product_count, 0) AS product_count,
          COALESCE(product_stats.detail_collected_count, 0) AS detail_collected_count,
          COALESCE(product_stats.product_evidence_points, 0) AS product_evidence_points,
          COALESCE(product_stats.product_evidence_total, 0) AS product_evidence_total,
          COALESCE(keyword_stats.keyword_count, 0) AS keyword_count,
          COALESCE(keyword_stats.keyword_with_snapshots, 0) AS keyword_with_snapshots,
          COALESCE(note_stats.note_count, 0) AS note_count,
          COALESCE(note_stats.opportunity_count, 0) AS opportunity_count,
          COALESCE(note_stats.risk_count, 0) AS risk_count
        FROM research_projects rp
        LEFT JOIN (
          SELECT
            rpp.project_id,
            COUNT(*) AS product_count,
            SUM(CASE WHEN p.detail_collected_at IS NOT NULL THEN 1 ELSE 0 END) AS detail_collected_count,
            SUM(CASE WHEN NULLIF(TRIM(p.category_path), '') IS NOT NULL THEN 1 ELSE 0 END)
              + SUM(CASE WHEN p.date_first_available IS NOT NULL THEN 1 ELSE 0 END)
              + SUM(CASE WHEN COALESCE(bsr_stats.bsr_count, 0) > 0 THEN 1 ELSE 0 END)
              AS product_evidence_points,
            COUNT(*) * 3 AS product_evidence_total
          FROM research_project_products rpp
          JOIN products p ON p.id = rpp.product_id
          LEFT JOIN (
            SELECT product_id, COUNT(*) AS bsr_count
            FROM product_bsr_snapshots
            GROUP BY product_id
          ) bsr_stats ON bsr_stats.product_id = p.id
          GROUP BY rpp.project_id
        ) product_stats ON product_stats.project_id = rp.id
        LEFT JOIN (
          SELECT
            rpk.project_id,
            COUNT(*) AS keyword_count,
            SUM(CASE WHEN rank_stats.snapshot_count > 0 THEN 1 ELSE 0 END) AS keyword_with_snapshots
          FROM research_project_keywords rpk
          LEFT JOIN (
            SELECT keyword_id, COUNT(*) AS snapshot_count
            FROM keyword_rank_snapshots
            GROUP BY keyword_id
          ) rank_stats ON rank_stats.keyword_id = rpk.keyword_id
          GROUP BY rpk.project_id
        ) keyword_stats ON keyword_stats.project_id = rp.id
        LEFT JOIN (
          SELECT
            project_id,
            COUNT(*) AS note_count,
            SUM(CASE WHEN note_type = 'opportunity' THEN 1 ELSE 0 END) AS opportunity_count,
            SUM(CASE WHEN note_type = 'risk' THEN 1 ELSE 0 END) AS risk_count
          FROM research_project_notes
          GROUP BY project_id
        ) note_stats ON note_stats.project_id = rp.id
        LEFT JOIN research_project_report_versions current_report
          ON current_report.id = rp.current_decision_report_version_id
    """


def _fetch_project_products(cursor: Any, project_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          rpp.id AS relation_id,
          rpp.role,
          rpp.notes,
          rpp.added_at,
          p.id AS product_id,
          p.marketplace,
          p.asin,
          p.title,
          p.title_zh,
          p.brand,
          p.category_path,
          p.product_size,
          p.date_first_available,
          p.detail_collected_at,
          p.product_url,
          p.image_url,
          latest_snapshot.snapshot_at,
          latest_snapshot.price,
          latest_snapshot.rating,
          latest_snapshot.review_count,
          latest_snapshot.monthly_bought,
          latest_rank.organic_rank,
          latest_score.total_score,
          latest_score.reason AS score_reason,
          score_keyword.keyword AS score_keyword,
          (SELECT COUNT(*) FROM product_snapshots ps_count WHERE ps_count.product_id = p.id) AS snapshot_count,
          (SELECT COUNT(*) FROM product_bsr_snapshots bsr WHERE bsr.product_id = p.id) AS bsr_count
        FROM research_project_products rpp
        JOIN products p ON p.id = rpp.product_id
        LEFT JOIN product_snapshots latest_snapshot
          ON latest_snapshot.id = (
            SELECT ps.id
            FROM product_snapshots ps
            WHERE ps.product_id = p.id
            ORDER BY ps.snapshot_at DESC, ps.id DESC
            LIMIT 1
          )
        LEFT JOIN product_scores latest_score
          ON latest_score.id = (
            SELECT score.id
            FROM product_scores score
            WHERE score.product_id = p.id
            ORDER BY score.score_date DESC, score.total_score DESC, score.id DESC
            LIMIT 1
          )
        LEFT JOIN keywords score_keyword ON score_keyword.id = latest_score.keyword_id
        LEFT JOIN keyword_rank_snapshots latest_rank
          ON latest_rank.id = (
            SELECT rank_row.id
            FROM keyword_rank_snapshots rank_row
            WHERE rank_row.product_id = p.id
              AND rank_row.keyword_id = latest_score.keyword_id
            ORDER BY rank_row.snapshot_at DESC, rank_row.id DESC
            LIMIT 1
          )
        WHERE rpp.project_id = %s
        ORDER BY rpp.added_at DESC, p.asin ASC
        """,
        (project_id,),
    )
    result = []
    for row in cursor.fetchall():
        normalized = _normalize_row(row)
        collected = sum(
            1
            for value in (
                normalized.get("category_path"),
                normalized.get("date_first_available"),
                normalized.get("bsr_count") if normalized.get("bsr_count") else None,
            )
            if value not in (None, "", 0)
        )
        normalized["evidence_collected"] = collected
        normalized["evidence_total"] = 3
        normalized["evidence_status"] = (
            "not_collected"
            if not normalized.get("detail_collected_at")
            else "collected" if collected == 3 else "partial"
        )
        result.append(normalized)
    return result


def _fetch_project_keywords(cursor: Any, project_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
          rpk.id AS relation_id,
          rpk.role,
          rpk.notes,
          rpk.added_at,
          k.id AS keyword_id,
          k.marketplace,
          k.keyword,
          COALESCE(rank_stats.product_count, 0) AS product_count,
          COALESCE(rank_stats.snapshot_count, 0) AS rank_snapshot_count,
          rank_stats.latest_snapshot_at,
          rank_stats.avg_organic_rank,
          score_stats.avg_total_score,
          idea.idea_score,
          idea.confidence_score,
          idea.recommendation_level,
          (
            SELECT task.status
            FROM keyword_tracking_tasks task
            WHERE task.marketplace = k.marketplace AND task.keyword = k.keyword
            ORDER BY (task.status = 'active') DESC, task.updated_at DESC, task.id DESC
            LIMIT 1
          ) AS tracking_status
        FROM research_project_keywords rpk
        JOIN keywords k ON k.id = rpk.keyword_id
        LEFT JOIN (
          SELECT
            keyword_id,
            COUNT(DISTINCT product_id) AS product_count,
            COUNT(*) AS snapshot_count,
            MAX(snapshot_at) AS latest_snapshot_at,
            AVG(organic_rank) AS avg_organic_rank
          FROM keyword_rank_snapshots
          GROUP BY keyword_id
        ) rank_stats ON rank_stats.keyword_id = k.id
        LEFT JOIN (
          SELECT keyword_id, AVG(total_score) AS avg_total_score
          FROM product_scores
          WHERE keyword_id IS NOT NULL
          GROUP BY keyword_id
        ) score_stats ON score_stats.keyword_id = k.id
        LEFT JOIN keyword_ideas idea ON idea.promoted_keyword_id = k.id
        WHERE rpk.project_id = %s
        ORDER BY rpk.added_at DESC, k.keyword ASC
        """,
        (project_id,),
    )
    return [_normalize_row(row) for row in cursor.fetchall()]


def _fetch_project_notes(cursor: Any, project_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, note_type, content, created_at, updated_at
        FROM research_project_notes
        WHERE project_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (project_id,),
    )
    return [_normalize_row(row) for row in cursor.fetchall()]


def _fetch_project_niches(cursor: Any, project_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT rpn.niche_id, mn.name, mn.status, mn.definition, rpn.role,
               latest.snapshot_at AS latest_snapshot_at,
               latest.observed_product_count,
               latest.keyword_count,
               latest.keyword_with_rank_count,
               latest.rank_coverage,
               latest.serp_coverage,
               latest.serp_data_coverage,
               latest.product_snapshot_coverage,
               latest.monthly_bought_coverage,
               latest.cross_keyword_overlap
        FROM research_project_niches rpn
        JOIN market_niches mn ON mn.id = rpn.niche_id
        LEFT JOIN niche_snapshots latest
          ON latest.id = (
            SELECT ns.id FROM niche_snapshots ns
            WHERE ns.niche_id = mn.id
            ORDER BY ns.snapshot_at DESC, ns.id DESC LIMIT 1
          )
        WHERE rpn.project_id = %s
        ORDER BY FIELD(rpn.role, 'primary', 'candidate', 'reference'), mn.updated_at DESC
        """,
        (project_id,),
    )
    from services.market_niches import evidence_level

    rows = []
    for row in cursor.fetchall():
        normalized = _normalize_row(row)
        normalized["evidence_level"] = evidence_level(normalized)
        rows.append(normalized)
    return rows


def _remove_relation(
    table: str,
    project_id: int,
    asset_id: int,
    *,
    asset_column: str,
    client: MySQLClient | None,
) -> dict[str, Any]:
    allowed = {
        ("research_project_products", "product_id"),
        ("research_project_keywords", "keyword_id"),
    }
    if (table, asset_column) not in allowed:
        raise ResearchProjectError("不支持的项目关系")
    db = client or MySQLClient()
    project_id_value = _positive_int(project_id, "研究项目 ID")
    asset_id_value = _positive_int(asset_id, "资产 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            ensure_project_mutable(_require_project(cursor, project_id_value))
            cursor.execute(
                f"DELETE FROM {table} WHERE project_id = %s AND {asset_column} = %s",
                (project_id_value, asset_id_value),
            )
            deleted = cursor.rowcount > 0
    return {"deleted": deleted, "project": get_research_project(project_id_value, client=db)}


def _require_project(cursor: Any, project_id: int) -> dict[str, Any]:
    cursor.execute(
        "SELECT id, marketplace, status FROM research_projects WHERE id = %s LIMIT 1",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ResearchProjectError(f"研究项目不存在：{project_id}")
    return row


def _project_order(sort_by: str, sort_dir: str) -> tuple[str, str, str]:
    choices = {
        "updated_at": "rp.updated_at",
        "created_at": "rp.created_at",
        "name": "rp.name",
        "status": "rp.status",
        "product_count": "product_count",
        "keyword_count": "keyword_count",
    }
    normalized_sort = str(sort_by or "updated_at").strip()
    if normalized_sort not in choices:
        normalized_sort = "updated_at"
    normalized_dir = "asc" if str(sort_dir or "").lower() == "asc" else "desc"
    direction = "ASC" if normalized_dir == "asc" else "DESC"
    expression = choices[normalized_sort]
    return f"ORDER BY {expression} {direction}, rp.id DESC", normalized_sort, normalized_dir


def _normalize_project_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row)
    status = str(result.get("status") or "idea")
    result["status_label"] = PROJECT_STATUS_LABELS.get(status, status)
    product_count = int(result.get("product_count") or 0)
    product_evidence_points = int(result.get("product_evidence_points") or 0)
    product_evidence_total = int(result.get("product_evidence_total") or (product_count * 3))
    keyword_count = int(result.get("keyword_count") or 0)
    snapshot_count = int(result.get("keyword_with_snapshots") or 0)
    total_evidence = product_evidence_total + keyword_count
    covered_evidence = product_evidence_points + snapshot_count
    result["evidence_coverage"] = (
        round(covered_evidence / total_evidence * 100, 1) if total_evidence else 0.0
    )
    result["terminal_report_unbound"] = (
        status in TERMINAL_STATUSES
        and not result.get("current_decision_report_version_id")
    )
    return result


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (datetime, date)):
            result[key] = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        else:
            result[key] = value
    return result


def _normalize_asins(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        for part in re.split(r"[\s,，;；]+", str(raw or "")):
            asin = part.strip().upper()
            if not asin or asin in seen:
                continue
            if not ASIN_PATTERN.fullmatch(asin):
                raise ResearchProjectError(f"ASIN 格式不正确：{asin}")
            result.append(asin)
            seen.add(asin)
            if len(result) > 50:
                raise ResearchProjectError("单次最多关联 50 个 ASIN")
    return result


def _normalize_keywords(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values or []:
        for part in re.split(r"[\r\n,，;；]+", str(raw or "")):
            display = " ".join(part.strip().split())
            normalized = normalize_keyword(display)
            if not normalized:
                continue
            if len(display) > 255:
                raise ResearchProjectError(f"关键词过长：{display[:40]}…")
            result.setdefault(normalized, display)
            if len(result) > 100:
                raise ResearchProjectError("单次最多关联 100 个关键词")
    return result


def _marketplace(value: str | None) -> str:
    marketplace = str(value or "US").strip().upper()
    if not marketplace or len(marketplace) > 16 or not re.fullmatch(r"[A-Z0-9_-]+", marketplace):
        raise ResearchProjectError("站点格式不正确")
    return marketplace


def _status(value: str | None) -> str:
    return _choice(value, set(PROJECT_STATUSES), "项目状态")


def _choice(value: str | None, choices: set[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise ResearchProjectError(f"{label}不支持：{value}")
    return normalized


def _required_text(value: str | None, label: str, *, maximum: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ResearchProjectError(f"{label}不能为空")
    if len(text) > maximum:
        raise ResearchProjectError(f"{label}不能超过 {maximum} 个字符")
    return text


def _required_content(value: str | None, label: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ResearchProjectError(f"{label}不能为空")
    if len(text) > maximum:
        raise ResearchProjectError(f"{label}不能超过 {maximum} 个字符")
    return text


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ResearchProjectError(f"内容不能超过 {maximum} 个字符")
    return text


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchProjectError(f"{label}必须是正整数") from exc
    if result < 1:
        raise ResearchProjectError(f"{label}必须是正整数")
    return result


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))
