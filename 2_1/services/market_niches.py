"""Explicit market niches and evidence snapshots derived from existing data."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient


NICHE_STATUSES = {"draft", "active", "archived"}
NICHE_STATUS_LABELS = {"draft": "草稿", "active": "观察中", "archived": "已归档"}
NICHE_KEYWORD_ROLES = {"seed", "core", "long_tail", "reference"}
NICHE_PRODUCT_ROLES = {"candidate", "benchmark", "competitor", "reference"}
NICHE_PROJECT_ROLES = {"candidate", "primary", "reference"}
TERMINAL_RESEARCH_PROJECT_STATUSES = {"approved", "rejected"}
EVIDENCE_LEVEL_LABELS = {
    "not_generated": "尚未生成",
    "insufficient": "证据不足",
    "partial": "部分可用",
    "usable": "较完整",
}
RANK_SOURCE_ADJACENT_MAX_HOURS = 24.0
RANK_SOURCE_ALIGNMENT_LABELS = {
    "missing": "无可用批次",
    "single": "单一批次",
    "synchronous": "严格同期",
    "adjacent": "相邻批次",
    "mixed_period": "混合时点",
}
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class MarketNicheError(ValueError):
    pass


def normalize_niche_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_keyword(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def ensure_research_projects_mutable(rows: Iterable[dict[str, Any]]) -> None:
    frozen = [
        row
        for row in rows
        if str(row.get("status") or "") in TERMINAL_RESEARCH_PROJECT_STATUSES
    ]
    if not frozen:
        return
    project_ids = "、".join(f"#{int(row['id'])}" for row in frozen)
    raise MarketNicheError(
        f"研究项目 {project_ids} 已批准或已拒绝，证据关系已冻结；请先退回可编辑阶段"
    )


def create_market_niche(
    name: str,
    *,
    marketplace: str = "US",
    definition: str | None = None,
    category_scope: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    name_value = _required_text(name, "利基名称", maximum=255)
    normalized_name = normalize_niche_name(name_value)
    marketplace_value = _marketplace(marketplace)
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO market_niches (
                      marketplace, name, normalized_name, status, definition, category_scope
                    ) VALUES (%s, %s, %s, 'draft', %s, %s)
                    """,
                    (
                        marketplace_value,
                        name_value,
                        normalized_name,
                        _optional_text(definition, maximum=20_000),
                        _optional_text(category_scope, maximum=512),
                    ),
                )
                niche_id = int(cursor.lastrowid)
    except db._pymysql.err.IntegrityError as exc:
        raise MarketNicheError(f"{marketplace_value} 站点已存在同名利基") from exc
    return get_market_niche(niche_id, client=db)


def fetch_market_niches_page(
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
    where = ["mn.marketplace = %s"]
    params: list[Any] = [marketplace_value]
    if status:
        where.append("mn.status = %s")
        params.append(_choice(status, NICHE_STATUSES, "利基状态"))
    keyword_value = " ".join(str(keyword or "").strip().split())
    if keyword_value:
        where.append("(mn.name LIKE %s OR mn.definition LIKE %s OR mn.category_scope LIKE %s)")
        pattern = f"%{keyword_value}%"
        params.extend([pattern, pattern, pattern])
    where_sql = " AND ".join(where)
    order_sql, normalized_sort, normalized_dir = _niche_order(sort_by, sort_dir)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total FROM market_niches mn WHERE {where_sql}", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                "SELECT status, COUNT(*) AS count FROM market_niches WHERE marketplace = %s GROUP BY status",
                (marketplace_value,),
            )
            status_counts = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
            cursor.execute(
                f"""
                {_niche_summary_select()}
                WHERE {where_sql}
                {order_sql}
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_normalize_niche_row(row) for row in cursor.fetchall()]
    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "sort_by": normalized_sort,
        "sort_dir": normalized_dir,
        "status_counts": status_counts,
    }


def get_market_niche(niche_id: int, *, client: MySQLClient | None = None) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"{_niche_summary_select()} WHERE mn.id = %s LIMIT 1", (niche_id_value,))
            row = cursor.fetchone()
            if not row:
                raise MarketNicheError(f"未找到利基 #{niche_id_value}")
            return {
                "niche": _normalize_niche_row(row),
                "keywords": _fetch_niche_keywords(cursor, niche_id_value),
                "products": _fetch_niche_products(cursor, niche_id_value),
                "projects": _fetch_niche_projects(cursor, niche_id_value),
                "snapshots": _fetch_niche_snapshots(cursor, niche_id_value),
            }


def update_market_niche(
    niche_id: int,
    *,
    name: str | None = None,
    status: str | None = None,
    definition: str | None = None,
    category_scope: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    assignments: list[str] = []
    params: list[Any] = []
    if name is not None:
        name_value = _required_text(name, "利基名称", maximum=255)
        assignments.extend(["name = %s", "normalized_name = %s"])
        params.extend([name_value, normalize_niche_name(name_value)])
    if status is not None:
        assignments.append("status = %s")
        params.append(_choice(status, NICHE_STATUSES, "利基状态"))
    if definition is not None:
        assignments.append("definition = %s")
        params.append(_optional_text(definition, maximum=20_000))
    if category_scope is not None:
        assignments.append("category_scope = %s")
        params.append(_optional_text(category_scope, maximum=512))
    if not assignments:
        return get_market_niche(niche_id_value, client=db)
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    params.append(niche_id_value)
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                _require_niche(cursor, niche_id_value)
                cursor.execute(
                    f"UPDATE market_niches SET {', '.join(assignments)} WHERE id = %s",
                    params,
                )
    except db._pymysql.err.IntegrityError as exc:
        raise MarketNicheError("同站点已存在同名利基") from exc
    return get_market_niche(niche_id_value, client=db)


def add_niche_keywords(
    niche_id: int,
    keywords: Iterable[str],
    *,
    role: str = "core",
    notes: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    normalized = _normalize_keywords(keywords)
    if not normalized:
        raise MarketNicheError("请至少输入一个关键词")
    role_value = _choice(role, NICHE_KEYWORD_ROLES, "关键词角色")
    notes_value = _optional_text(notes, maximum=10_000)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            niche = _require_niche(cursor, niche_id_value)
            displays = list(normalized.values())
            placeholders = ", ".join(["%s"] * len(displays))
            cursor.execute(
                f"SELECT id, keyword FROM keywords WHERE marketplace = %s AND keyword IN ({placeholders})",
                [niche["marketplace"], *displays],
            )
            found = {normalize_keyword(row["keyword"]): row for row in cursor.fetchall()}
            for key, row in found.items():
                cursor.execute(
                    """
                    INSERT INTO niche_keywords (niche_id, keyword_id, role, notes)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      role = VALUES(role),
                      notes = COALESCE(VALUES(notes), notes),
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (niche_id_value, row["id"], role_value, notes_value),
                )
            missing = [display for key, display in normalized.items() if key not in found]
    return {"missing": missing, "niche": get_market_niche(niche_id_value, client=db)}


def remove_niche_keyword(
    niche_id: int,
    keyword_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    return _remove_relation(
        niche_id,
        keyword_id,
        table="niche_keywords",
        column="keyword_id",
        client=client,
    )


def add_niche_products(
    niche_id: int,
    asins: Iterable[str],
    *,
    role: str = "benchmark",
    notes: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    asin_values = _normalize_asins(asins)
    if not asin_values:
        raise MarketNicheError("请至少输入一个 ASIN")
    role_value = _choice(role, NICHE_PRODUCT_ROLES, "商品角色")
    notes_value = _optional_text(notes, maximum=10_000)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            niche = _require_niche(cursor, niche_id_value)
            placeholders = ", ".join(["%s"] * len(asin_values))
            cursor.execute(
                f"SELECT id, asin FROM products WHERE marketplace = %s AND asin IN ({placeholders})",
                [niche["marketplace"], *asin_values],
            )
            rows = cursor.fetchall()
            found = {str(row["asin"]).upper(): row for row in rows}
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO niche_products (niche_id, product_id, role, notes)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      role = VALUES(role),
                      notes = COALESCE(VALUES(notes), notes),
                      updated_at = CURRENT_TIMESTAMP
                    """,
                    (niche_id_value, row["id"], role_value, notes_value),
                )
            missing = [asin for asin in asin_values if asin not in found]
    return {"missing": missing, "niche": get_market_niche(niche_id_value, client=db)}


def remove_niche_product(
    niche_id: int,
    product_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    return _remove_relation(
        niche_id,
        product_id,
        table="niche_products",
        column="product_id",
        client=client,
    )


def add_niche_projects(
    niche_id: int,
    project_ids: Iterable[int],
    *,
    role: str = "candidate",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    ids = sorted({_positive_int(item, "研究项目 ID") for item in project_ids})
    if not ids:
        raise MarketNicheError("请至少选择一个研究项目")
    role_value = _choice(role, NICHE_PROJECT_ROLES, "项目角色")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            niche = _require_niche(cursor, niche_id_value)
            placeholders = ", ".join(["%s"] * len(ids))
            cursor.execute(
                f"SELECT id, status FROM research_projects WHERE marketplace = %s AND id IN ({placeholders})",
                [niche["marketplace"], *ids],
            )
            project_rows = cursor.fetchall()
            ensure_research_projects_mutable(project_rows)
            found_ids = {int(row["id"]) for row in project_rows}
            for project_id in found_ids:
                cursor.execute(
                    """
                    INSERT INTO research_project_niches (project_id, niche_id, role)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE role = VALUES(role), updated_at = CURRENT_TIMESTAMP
                    """,
                    (project_id, niche_id_value, role_value),
                )
            missing = [item for item in ids if item not in found_ids]
    return {"missing": missing, "niche": get_market_niche(niche_id_value, client=db)}


def remove_niche_project(
    niche_id: int,
    project_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    return _remove_relation(
        niche_id,
        project_id,
        table="research_project_niches",
        column="project_id",
        client=client,
    )


def generate_niche_snapshot(
    niche_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            _require_niche(cursor, niche_id_value)
            cursor.execute(
                "SELECT keyword_id FROM niche_keywords WHERE niche_id = %s ORDER BY keyword_id",
                (niche_id_value,),
            )
            keyword_ids = [int(row["keyword_id"]) for row in cursor.fetchall()]
            if not keyword_ids:
                raise MarketNicheError("至少关联一个已入库关键词后才能生成市场快照")

            placeholders = ", ".join(["%s"] * len(keyword_ids))
            cursor.execute(
                f"""
                SELECT krs.id, krs.keyword_id, krs.product_id, krs.snapshot_at,
                       krs.organic_rank, krs.is_sponsored
                FROM keyword_rank_snapshots krs
                JOIN (
                  SELECT keyword_id, MAX(snapshot_at) AS snapshot_at
                  FROM keyword_rank_snapshots
                  WHERE keyword_id IN ({placeholders})
                  GROUP BY keyword_id
                ) latest
                  ON latest.keyword_id = krs.keyword_id
                 AND latest.snapshot_at = krs.snapshot_at
                ORDER BY krs.keyword_id, krs.product_id
                """,
                keyword_ids,
            )
            rank_rows = cursor.fetchall()
            cursor.execute(
                f"""
                SELECT kss.*
                FROM keyword_serp_snapshots kss
                JOIN (
                  SELECT keyword_id, MAX(snapshot_at) AS snapshot_at
                  FROM keyword_serp_snapshots
                  WHERE keyword_id IN ({placeholders})
                  GROUP BY keyword_id
                ) latest
                  ON latest.keyword_id = kss.keyword_id
                 AND latest.snapshot_at = kss.snapshot_at
                ORDER BY kss.keyword_id
                """,
                keyword_ids,
            )
            serp_rows = cursor.fetchall()

            product_ids = sorted({int(row["product_id"]) for row in rank_rows})
            product_rows: list[dict[str, Any]] = []
            if product_ids:
                product_placeholders = ", ".join(["%s"] * len(product_ids))
                cursor.execute(
                    f"""
                    SELECT p.id AS product_id, p.brand, snap.id AS snapshot_id,
                           snap.snapshot_at, snap.price, snap.rating,
                           snap.review_count, snap.monthly_bought
                    FROM products p
                    LEFT JOIN product_snapshots snap
                      ON snap.id = (
                        SELECT ps.id FROM product_snapshots ps
                        WHERE ps.product_id = p.id
                        ORDER BY ps.snapshot_at DESC, ps.id DESC LIMIT 1
                      )
                    WHERE p.id IN ({product_placeholders})
                    ORDER BY p.id
                    """,
                    product_ids,
                )
                product_rows = cursor.fetchall()
            cursor.execute(
                "SELECT product_id FROM niche_products WHERE niche_id = %s ORDER BY product_id",
                (niche_id_value,),
            )
            manual_product_ids = [int(row["product_id"]) for row in cursor.fetchall()]

            metrics = _aggregate_niche_evidence(
                keyword_ids=keyword_ids,
                rank_rows=rank_rows,
                serp_rows=serp_rows,
                product_rows=product_rows,
                manual_product_ids=manual_product_ids,
            )
            snapshot_at = datetime.now().replace(microsecond=0)
            columns = [
                "niche_id", "snapshot_at", "source_latest_at", "evidence_hash",
                "keyword_count", "keyword_with_rank_count", "keyword_with_serp_count",
                "rank_coverage", "serp_coverage", "serp_data_coverage", "page_count",
                "observed_product_count", "product_with_snapshot_count", "product_snapshot_coverage",
                "repeated_product_count", "cross_keyword_overlap", "manual_product_count",
                "price_p25", "price_median", "price_p75",
                "review_p25", "review_median", "review_p75", "rating_median",
                "monthly_bought_median", "monthly_bought_total", "monthly_bought_coverage",
                "demand_cr3", "demand_cr10", "ad_density", "brand_count", "brand_coverage",
                "brand_product_cr3", "raw_json",
            ]
            values = [
                niche_id_value,
                snapshot_at,
                metrics["source_latest_at"],
                metrics["evidence_hash"],
                *[metrics[column] for column in columns[4:-1]],
                json.dumps(metrics["raw_json"], ensure_ascii=False),
            ]
            cursor.execute(
                f"""
                INSERT INTO niche_snapshots ({', '.join(columns)})
                VALUES ({', '.join(['%s'] * len(columns))})
                ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                """,
                values,
            )
            created = cursor.rowcount == 1
            snapshot_id = int(cursor.lastrowid)
    detail = get_market_niche(niche_id_value, client=db)
    snapshot = next((row for row in detail["snapshots"] if int(row["id"]) == snapshot_id), None)
    return {"created": created, "snapshot": snapshot, "niche": detail}


def _aggregate_niche_evidence(
    *,
    keyword_ids: Iterable[int],
    rank_rows: Iterable[dict[str, Any]],
    serp_rows: Iterable[dict[str, Any]],
    product_rows: Iterable[dict[str, Any]],
    manual_product_ids: Iterable[int],
) -> dict[str, Any]:
    keyword_id_values = sorted({int(item) for item in keyword_ids})
    ranks = [dict(row) for row in rank_rows]
    serps = [dict(row) for row in serp_rows]
    products = [dict(row) for row in product_rows]
    manual_ids = sorted({int(item) for item in manual_product_ids})
    keyword_count = len(keyword_id_values)
    ranked_keyword_ids = {int(row["keyword_id"]) for row in ranks}
    serp_keyword_ids = {int(row["keyword_id"]) for row in serps}
    product_keywords: dict[int, set[int]] = defaultdict(set)
    for row in ranks:
        product_keywords[int(row["product_id"])].add(int(row["keyword_id"]))
    observed_product_ids = sorted(product_keywords)
    observed_count = len(observed_product_ids)
    repeated_count = sum(1 for values in product_keywords.values() if len(values) >= 2)
    overlap = (
        round(repeated_count / observed_count, 4)
        if observed_count and len(ranked_keyword_ids) >= 2
        else None
    )

    product_by_id = {int(row["product_id"]): row for row in products}
    sampled = [product_by_id[item] for item in observed_product_ids if item in product_by_id]
    with_snapshot = [row for row in sampled if row.get("snapshot_id") is not None]
    prices = _numbers(row.get("price") for row in with_snapshot)
    reviews = _numbers(row.get("review_count") for row in with_snapshot)
    ratings = _numbers(row.get("rating") for row in with_snapshot)
    monthly = _numbers(row.get("monthly_bought") for row in with_snapshot)
    known_brands = [
        " ".join(str(row.get("brand") or "").strip().lower().split())
        for row in sampled
        if str(row.get("brand") or "").strip()
    ]
    brand_counts = Counter(known_brands)

    total_cards = sum(int(row.get("total_card_count") or 0) for row in serps)
    sponsored_cards = sum(int(row.get("sponsored_count") or 0) for row in serps)
    coverage_weight = sum(max(1, int(row.get("unique_asin_count") or 0)) for row in serps if row.get("data_coverage") is not None)
    coverage_total = sum(
        float(row["data_coverage"]) * max(1, int(row.get("unique_asin_count") or 0))
        for row in serps
        if row.get("data_coverage") is not None
    )
    source_times = [
        parsed
        for row in [*ranks, *serps, *with_snapshot]
        if (parsed := _as_datetime(row.get("snapshot_at"))) is not None
    ]
    source_latest_at = max(source_times) if source_times else None
    rank_batch_times = sorted(
        [keyword_id, max(times).isoformat(sep=" ")]
        for keyword_id in keyword_id_values
        if (times := [
            parsed
            for row in ranks
            if int(row["keyword_id"]) == keyword_id
            and (parsed := _as_datetime(row.get("snapshot_at"))) is not None
        ])
    )
    rank_source_timing = _rank_source_timing(rank_batch_times)

    rank_coverage = round(len(ranked_keyword_ids) / keyword_count, 4) if keyword_count else None
    serp_coverage = round(len(serp_keyword_ids) / keyword_count, 4) if keyword_count else None
    product_snapshot_coverage = round(len(with_snapshot) / observed_count, 4) if observed_count else None
    monthly_coverage = round(len(monthly) / observed_count, 4) if observed_count else None
    brand_coverage = round(len(known_brands) / observed_count, 4) if observed_count else None
    brand_product_cr3 = (
        round(sum(count for _brand, count in brand_counts.most_common(3)) / len(known_brands), 4)
        if known_brands
        else None
    )

    warnings = _evidence_warnings(
        keyword_count=keyword_count,
        ranked_keyword_count=len(ranked_keyword_ids),
        serp_coverage=serp_coverage,
        observed_product_count=observed_count,
        product_snapshot_coverage=product_snapshot_coverage,
        monthly_bought_coverage=monthly_coverage,
        brand_coverage=brand_coverage,
    )
    if rank_source_timing["alignment"] == "mixed_period":
        warnings.append(
            "成员关键词最新排名批次跨越 "
            f"{rank_source_timing['span_days']:.1f} 天，超过 24 小时；"
            "跨词重合与市场分布仅代表混合时点观察，不应解释为严格同期市场截面"
        )
    source_identity = {
        "rank_basis": "latest_keyword_batch_v2",
        "keyword_ids": keyword_id_values,
        "rank_snapshot_ids": sorted(int(row["id"]) for row in ranks if row.get("id") is not None),
        "rank_batch_times": rank_batch_times,
        "serp_snapshot_ids": sorted(int(row["id"]) for row in serps if row.get("id") is not None),
        "product_snapshots": sorted(
            [int(row["product_id"]), int(row["snapshot_id"]), str(row.get("brand") or "")]
            for row in with_snapshot
        ),
        "manual_product_ids": manual_ids,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(source_identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "source_latest_at": source_latest_at,
        "rank_source_first_at": rank_source_timing["first_at"],
        "rank_source_latest_at": rank_source_timing["latest_at"],
        "rank_source_span_hours": rank_source_timing["span_hours"],
        "rank_source_span_days": rank_source_timing["span_days"],
        "rank_source_alignment": rank_source_timing["alignment"],
        "evidence_hash": evidence_hash,
        "keyword_count": keyword_count,
        "keyword_with_rank_count": len(ranked_keyword_ids),
        "keyword_with_serp_count": len(serp_keyword_ids),
        "rank_coverage": rank_coverage,
        "serp_coverage": serp_coverage,
        "serp_data_coverage": round(coverage_total / coverage_weight, 4) if coverage_weight else None,
        "page_count": sum(int(row.get("page_count") or 0) for row in serps),
        "observed_product_count": observed_count,
        "product_with_snapshot_count": len(with_snapshot),
        "product_snapshot_coverage": product_snapshot_coverage,
        "repeated_product_count": repeated_count,
        "cross_keyword_overlap": overlap,
        "manual_product_count": len(manual_ids),
        "price_p25": _percentile(prices, 0.25),
        "price_median": _percentile(prices, 0.50),
        "price_p75": _percentile(prices, 0.75),
        "review_p25": _percentile(reviews, 0.25),
        "review_median": _percentile(reviews, 0.50),
        "review_p75": _percentile(reviews, 0.75),
        "rating_median": _percentile(ratings, 0.50),
        "monthly_bought_median": _percentile(monthly, 0.50),
        "monthly_bought_total": round(sum(monthly), 2) if monthly else None,
        "monthly_bought_coverage": monthly_coverage,
        "demand_cr3": _concentration(monthly, 3),
        "demand_cr10": _concentration(monthly, 10),
        "ad_density": round(sponsored_cards / total_cards, 4) if total_cards else None,
        "brand_count": len(brand_counts),
        "brand_coverage": brand_coverage,
        "brand_product_cr3": brand_product_cr3,
        "raw_json": {
            "basis": "latest complete rank batch per member keyword; unique ASIN latest product snapshots",
            "aggregation_version": "latest_keyword_batch_v2",
            "manual_products_excluded_from_market_sample": True,
            "monthly_bought_is_lower_bound_proxy": True,
            "brand_product_cr3_is_not_sales_share": True,
            "source_identity": source_identity,
            "rank_source_time_range": rank_source_timing,
            "warnings": warnings,
        },
    }


def evidence_level(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "not_generated"
    keyword_count = int(snapshot.get("keyword_count") or 0)
    ranked_count = int(snapshot.get("keyword_with_rank_count") or 0)
    observed_count = int(snapshot.get("observed_product_count") or 0)
    if not keyword_count or not ranked_count or observed_count < 20:
        return "insufficient"
    if (
        keyword_count < 2
        or float(snapshot.get("rank_coverage") or 0) < 0.8
        or float(snapshot.get("serp_coverage") or 0) < 0.5
        or float(snapshot.get("serp_data_coverage") or 0) < 0.6
        or float(snapshot.get("product_snapshot_coverage") or 0) < 0.7
        or float(snapshot.get("monthly_bought_coverage") or 0) < 0.4
    ):
        return "partial"
    return "usable"


def _evidence_warnings(
    *,
    keyword_count: int,
    ranked_keyword_count: int,
    serp_coverage: float | None,
    observed_product_count: int,
    product_snapshot_coverage: float | None,
    monthly_bought_coverage: float | None,
    brand_coverage: float | None,
) -> list[str]:
    warnings: list[str] = []
    if keyword_count < 2:
        warnings.append("成员关键词少于 2 个，暂不能验证跨关键词市场一致性")
    if ranked_keyword_count == 0:
        warnings.append("成员关键词没有排名快照，尚无可用市场样本")
    if float(serp_coverage or 0) < 0.5:
        warnings.append("SERP 聚合覆盖不足一半，广告密度和字段覆盖代表性有限")
    if observed_product_count < 20:
        warnings.append("去重观察 ASIN 少于 20 个，分布指标样本不足")
    if float(product_snapshot_coverage or 0) < 0.7:
        warnings.append("商品快照覆盖不足 70%，价格和评论分布可能偏差")
    if float(monthly_bought_coverage or 0) < 0.4:
        warnings.append("近月购买量覆盖不足 40%，需求代理与集中度只能谨慎参考")
    if float(brand_coverage or 0) < 0.5:
        warnings.append("品牌覆盖不足 50%，品牌集中度代表性有限")
    return warnings


def _niche_summary_select() -> str:
    return """
        SELECT
          mn.id, mn.marketplace, mn.name, mn.normalized_name, mn.status,
          mn.definition, mn.category_scope, mn.created_at, mn.updated_at,
          COALESCE(keyword_stats.keyword_count, 0) AS keyword_count,
          COALESCE(product_stats.manual_product_count, 0) AS manual_product_count,
          COALESCE(project_stats.project_count, 0) AS project_count,
          latest.id AS latest_snapshot_id,
          latest.snapshot_at AS latest_snapshot_at,
          latest.source_latest_at,
          latest.keyword_with_rank_count,
          latest.keyword_with_serp_count,
          latest.rank_coverage,
          latest.serp_coverage,
          latest.serp_data_coverage,
          latest.observed_product_count,
          latest.product_snapshot_coverage,
          latest.cross_keyword_overlap,
          latest.monthly_bought_total,
          latest.monthly_bought_coverage,
          latest.ad_density,
          latest.brand_product_cr3,
          latest.brand_coverage,
          latest.raw_json
        FROM market_niches mn
        LEFT JOIN (
          SELECT niche_id, COUNT(*) AS keyword_count FROM niche_keywords GROUP BY niche_id
        ) keyword_stats ON keyword_stats.niche_id = mn.id
        LEFT JOIN (
          SELECT niche_id, COUNT(*) AS manual_product_count FROM niche_products GROUP BY niche_id
        ) product_stats ON product_stats.niche_id = mn.id
        LEFT JOIN (
          SELECT niche_id, COUNT(*) AS project_count FROM research_project_niches GROUP BY niche_id
        ) project_stats ON project_stats.niche_id = mn.id
        LEFT JOIN niche_snapshots latest
          ON latest.id = (
            SELECT ns.id FROM niche_snapshots ns
            WHERE ns.niche_id = mn.id
            ORDER BY ns.snapshot_at DESC, ns.id DESC LIMIT 1
          )
    """


def _fetch_niche_keywords(cursor: Any, niche_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT nk.keyword_id, k.keyword, nk.role, nk.notes, nk.added_at, nk.updated_at,
               COALESCE(rank_stats.rank_snapshot_count, 0) AS rank_snapshot_count,
               COALESCE(rank_stats.observed_product_count, 0) AS observed_product_count,
               rank_stats.latest_rank_at,
               COALESCE(serp_stats.serp_snapshot_count, 0) AS serp_snapshot_count,
               serp_stats.latest_serp_at
        FROM niche_keywords nk
        JOIN keywords k ON k.id = nk.keyword_id
        LEFT JOIN (
          SELECT keyword_id, COUNT(*) AS rank_snapshot_count,
                 COUNT(DISTINCT product_id) AS observed_product_count,
                 MAX(snapshot_at) AS latest_rank_at
          FROM keyword_rank_snapshots GROUP BY keyword_id
        ) rank_stats ON rank_stats.keyword_id = nk.keyword_id
        LEFT JOIN (
          SELECT keyword_id, COUNT(*) AS serp_snapshot_count, MAX(snapshot_at) AS latest_serp_at
          FROM keyword_serp_snapshots GROUP BY keyword_id
        ) serp_stats ON serp_stats.keyword_id = nk.keyword_id
        WHERE nk.niche_id = %s
        ORDER BY FIELD(nk.role, 'seed', 'core', 'long_tail', 'reference'), k.keyword
        """,
        (niche_id,),
    )
    return [_normalize_row(row) for row in cursor.fetchall()]


def _fetch_niche_products(cursor: Any, niche_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT np.product_id, p.asin, p.title, p.brand, p.category_path,
               np.role, np.notes, np.added_at, np.updated_at,
               snap.snapshot_at AS latest_snapshot_at, snap.price, snap.rating,
               snap.review_count, snap.monthly_bought
        FROM niche_products np
        JOIN products p ON p.id = np.product_id
        LEFT JOIN product_snapshots snap
          ON snap.id = (
            SELECT ps.id FROM product_snapshots ps WHERE ps.product_id = p.id
            ORDER BY ps.snapshot_at DESC, ps.id DESC LIMIT 1
          )
        WHERE np.niche_id = %s
        ORDER BY FIELD(np.role, 'candidate', 'benchmark', 'competitor', 'reference'), np.added_at
        """,
        (niche_id,),
    )
    return [_normalize_row(row) for row in cursor.fetchall()]


def _fetch_niche_projects(cursor: Any, niche_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT rpn.project_id, rp.name, rp.status, rp.objective, rpn.role,
               rpn.added_at, rpn.updated_at
        FROM research_project_niches rpn
        JOIN research_projects rp ON rp.id = rpn.project_id
        WHERE rpn.niche_id = %s
        ORDER BY FIELD(rpn.role, 'primary', 'candidate', 'reference'), rp.updated_at DESC
        """,
        (niche_id,),
    )
    return [_normalize_row(row) for row in cursor.fetchall()]


def _fetch_niche_snapshots(cursor: Any, niche_id: int, limit: int = 30) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT * FROM niche_snapshots
        WHERE niche_id = %s
        ORDER BY snapshot_at DESC, id DESC
        LIMIT %s
        """,
        (niche_id, _bounded_int(limit, default=30, minimum=1, maximum=200)),
    )
    return [_normalize_snapshot_row(row) for row in cursor.fetchall()]


def _remove_relation(
    niche_id: int,
    target_id: int,
    *,
    table: str,
    column: str,
    client: MySQLClient | None,
) -> dict[str, Any]:
    allowed = {
        ("niche_keywords", "keyword_id"),
        ("niche_products", "product_id"),
        ("research_project_niches", "project_id"),
    }
    if (table, column) not in allowed:
        raise MarketNicheError("不支持的利基关系")
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    target_id_value = _positive_int(target_id, "关系对象 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            _require_niche(cursor, niche_id_value)
            if table == "research_project_niches":
                cursor.execute(
                    "SELECT id, status FROM research_projects WHERE id = %s LIMIT 1",
                    (target_id_value,),
                )
                project = cursor.fetchone()
                if project:
                    ensure_research_projects_mutable([project])
            cursor.execute(
                f"DELETE FROM {table} WHERE niche_id = %s AND {column} = %s",
                (niche_id_value, target_id_value),
            )
            deleted = cursor.rowcount > 0
    return {"deleted": deleted, "niche": get_market_niche(niche_id_value, client=db)}


def _require_niche(cursor: Any, niche_id: int) -> dict[str, Any]:
    cursor.execute("SELECT * FROM market_niches WHERE id = %s LIMIT 1", (niche_id,))
    row = cursor.fetchone()
    if not row:
        raise MarketNicheError(f"未找到利基 #{niche_id}")
    return row


def _niche_order(sort_by: str, sort_dir: str) -> tuple[str, str, str]:
    choices = {
        "updated_at": "mn.updated_at",
        "name": "mn.name",
        "keyword_count": "keyword_count",
        "observed_product_count": "latest.observed_product_count",
        "serp_coverage": "latest.serp_coverage",
        "latest_snapshot_at": "latest.snapshot_at",
    }
    normalized_sort = str(sort_by or "updated_at").strip()
    if normalized_sort not in choices:
        normalized_sort = "updated_at"
    normalized_dir = "asc" if str(sort_dir or "").lower() == "asc" else "desc"
    direction = "ASC" if normalized_dir == "asc" else "DESC"
    expression = choices[normalized_sort]
    return f"ORDER BY {expression} IS NULL, {expression} {direction}, mn.id DESC", normalized_sort, normalized_dir


def _normalize_niche_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row)
    status = str(result.get("status") or "draft")
    result["status_label"] = NICHE_STATUS_LABELS.get(status, status)
    level = evidence_level(result if result.get("latest_snapshot_id") else None)
    result["evidence_level"] = level
    result["evidence_level_label"] = EVIDENCE_LEVEL_LABELS[level]
    raw = result.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    result["warnings"] = raw.get("warnings", []) if isinstance(raw, dict) else []
    _apply_rank_source_timing(result, niche_snapshot_source_timing(raw))
    result.pop("raw_json", None)
    return result


def _normalize_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row)
    raw = result.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    result["raw"] = raw if isinstance(raw, dict) else {}
    result["warnings"] = result["raw"].get("warnings", [])
    _apply_rank_source_timing(result, niche_snapshot_source_timing(result["raw"]))
    result.pop("raw_json", None)
    level = evidence_level(result)
    result["evidence_level"] = level
    result["evidence_level_label"] = EVIDENCE_LEVEL_LABELS[level]
    return result


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat(sep=" ")
        elif isinstance(value, date):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _normalize_keywords(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        for item in re.split(r"[\r\n,，;；]+", str(value or "")):
            display = " ".join(item.strip().split())
            key = normalize_keyword(display)
            if key:
                result.setdefault(key, display)
    return result


def _normalize_asins(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in re.split(r"[\s,，;；]+", str(value or "")):
            asin = item.strip().upper()
            if not asin:
                continue
            if not ASIN_RE.fullmatch(asin):
                raise MarketNicheError(f"ASIN 格式不正确：{item.strip()}")
            if asin not in seen:
                seen.add(asin)
                result.append(asin)
    return result


def _numbers(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def _concentration(values: list[float], top_n: int) -> float | None:
    positive = sorted((value for value in values if value > 0), reverse=True)
    total = sum(positive)
    if total <= 0:
        return None
    return round(sum(positive[:top_n]) / total, 4)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return None


def niche_snapshot_source_timing(raw: Any) -> dict[str, Any]:
    """Return canonical rank-batch timing, including for snapshots created before this metadata."""
    if not isinstance(raw, dict):
        return _rank_source_timing([])
    explicit = raw.get("rank_source_time_range")
    if isinstance(explicit, dict):
        first_at = _as_datetime(explicit.get("first_at"))
        latest_at = _as_datetime(explicit.get("latest_at"))
        if first_at is not None and latest_at is not None:
            member_count = _bounded_int(
                explicit.get("member_batch_count"),
                default=2,
                minimum=1,
                maximum=1_000_000,
            )
            return _rank_source_timing(
                [[index, first_at if index == 0 else latest_at] for index in range(member_count)]
            )
    identity = raw.get("source_identity")
    rank_batch_times = identity.get("rank_batch_times", []) if isinstance(identity, dict) else []
    return _rank_source_timing(rank_batch_times)


def _rank_source_timing(rank_batch_times: Iterable[Any]) -> dict[str, Any]:
    parsed_times: list[datetime] = []
    for item in rank_batch_times:
        value = item.get("snapshot_at") if isinstance(item, dict) else (
            item[1] if isinstance(item, (list, tuple)) and len(item) >= 2 else None
        )
        parsed = _as_datetime(value)
        if parsed is not None:
            parsed_times.append(parsed)
    unique_times = sorted(set(parsed_times))
    if not unique_times:
        alignment = "missing"
        first_at = latest_at = None
        span_hours = span_days = None
    else:
        first_at = unique_times[0]
        latest_at = unique_times[-1]
        span_hours = round((latest_at - first_at).total_seconds() / 3600, 2)
        span_days = round(span_hours / 24, 4)
        if len(parsed_times) == 1:
            alignment = "single"
        elif len(unique_times) == 1:
            alignment = "synchronous"
        elif span_hours <= RANK_SOURCE_ADJACENT_MAX_HOURS:
            alignment = "adjacent"
        else:
            alignment = "mixed_period"
    return {
        "first_at": first_at.isoformat(sep=" ") if first_at else None,
        "latest_at": latest_at.isoformat(sep=" ") if latest_at else None,
        "span_hours": span_hours,
        "span_days": span_days,
        "member_batch_count": len(parsed_times),
        "alignment": alignment,
    }


def _apply_rank_source_timing(result: dict[str, Any], timing: dict[str, Any]) -> None:
    result["rank_source_first_at"] = timing["first_at"]
    result["rank_source_latest_at"] = timing["latest_at"]
    result["rank_source_span_hours"] = timing["span_hours"]
    result["rank_source_span_days"] = timing["span_days"]
    result["rank_source_alignment"] = timing["alignment"]
    result["rank_source_alignment_label"] = RANK_SOURCE_ALIGNMENT_LABELS[timing["alignment"]]


def _marketplace(value: str | None) -> str:
    result = str(value or "US").strip().upper()
    if not result or len(result) > 16 or not re.fullmatch(r"[A-Z0-9_-]+", result):
        raise MarketNicheError("站点格式不正确")
    return result


def _choice(value: str | None, choices: set[str], label: str) -> str:
    result = str(value or "").strip().lower()
    if result not in choices:
        raise MarketNicheError(f"{label}不支持：{value}")
    return result


def _required_text(value: str | None, label: str, *, maximum: int) -> str:
    result = " ".join(str(value or "").strip().split())
    if not result:
        raise MarketNicheError(f"请填写{label}")
    if len(result) > maximum:
        raise MarketNicheError(f"{label}不能超过 {maximum} 个字符")
    return result


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    if len(result) > maximum:
        raise MarketNicheError(f"内容不能超过 {maximum} 个字符")
    return result


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketNicheError(f"{label}必须是正整数") from exc
    if result <= 0:
        raise MarketNicheError(f"{label}必须是正整数")
    return result


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))
