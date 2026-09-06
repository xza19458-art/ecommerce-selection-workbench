"""Read-only competitive and keyword graph derived from frozen niche snapshots."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from itertools import combinations
import json
import math
import re
from typing import Any, Iterable

from database.mysql_client import MySQLClient


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
SOURCE_BATCH_SIZE = 1000
GRAPH_BASIS = (
    "仅使用所选利基快照冻结的关键词与排名记录；未出现表示未在已采集范围观察到，"
    "不代表 Amazon 全量排名或投放情况。"
)


class CompetitiveGraphError(ValueError):
    pass


def get_competitive_graph(
    niche_id: int,
    *,
    snapshot_id: int | None = None,
    limit: int = 100,
    min_shared: int = 1,
    focus_asin: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    niche_id_value = _positive_int(niche_id, "利基 ID")
    snapshot_id_value = _optional_positive_int(snapshot_id, "快照 ID")
    limit_value = _bounded_int(limit, default=100, minimum=10, maximum=200)
    min_shared_value = _bounded_int(min_shared, default=1, minimum=1, maximum=100_000)
    focus_asin_value = _normalize_asin(focus_asin)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, marketplace, name, status, definition, category_scope,
                       created_at, updated_at
                FROM market_niches WHERE id = %s LIMIT 1
                """,
                (niche_id_value,),
            )
            niche = cursor.fetchone()
            if not niche:
                raise CompetitiveGraphError(f"未找到利基 #{niche_id_value}")

            cursor.execute(
                """
                SELECT id, niche_id, snapshot_at, source_latest_at, evidence_hash,
                       keyword_count, keyword_with_rank_count, keyword_with_serp_count,
                       rank_coverage, serp_coverage, serp_data_coverage,
                       observed_product_count, product_snapshot_coverage,
                       repeated_product_count, cross_keyword_overlap, raw_json
                FROM niche_snapshots
                WHERE niche_id = %s
                ORDER BY snapshot_at DESC, id DESC
                LIMIT 30
                """,
                (niche_id_value,),
            )
            snapshots = list(cursor.fetchall())
            if not snapshots:
                raise CompetitiveGraphError("该利基尚未生成证据快照，请先在市场与利基页面生成快照")
            snapshot = (
                next((row for row in snapshots if int(row["id"]) == snapshot_id_value), None)
                if snapshot_id_value is not None
                else snapshots[0]
            )
            if snapshot is None:
                raise CompetitiveGraphError(f"快照 #{snapshot_id_value} 不属于该利基或已不存在")

            raw = _parse_json(snapshot.get("raw_json"))
            identity = raw.get("source_identity") if isinstance(raw.get("source_identity"), dict) else {}
            expected_keyword_ids = _unique_positive_ints(identity.get("keyword_ids"))
            expected_rank_ids = _unique_positive_ints(identity.get("rank_snapshot_ids"))
            expected_serp_ids = _unique_positive_ints(identity.get("serp_snapshot_ids"))
            product_manifest = _parse_product_manifest(identity.get("product_snapshots"))
            expected_product_snapshot_ids = sorted({row["snapshot_id"] for row in product_manifest})

            keyword_rows = _fetch_by_ids(
                cursor,
                "SELECT id AS keyword_id, keyword FROM keywords",
                "id",
                expected_keyword_ids,
            )
            rank_rows = _fetch_by_ids(
                cursor,
                """
                SELECT id, keyword_id, product_id, snapshot_at, page_no,
                       organic_rank, is_sponsored
                FROM keyword_rank_snapshots
                """,
                "id",
                expected_rank_ids,
            )
            loaded_serp_rows = _fetch_by_ids(
                cursor,
                "SELECT id FROM keyword_serp_snapshots",
                "id",
                expected_serp_ids,
            )
            product_snapshot_rows = _fetch_by_ids(
                cursor,
                """
                SELECT id AS snapshot_id, product_id, snapshot_at, price, rating,
                       review_count, monthly_bought
                FROM product_snapshots
                """,
                "id",
                expected_product_snapshot_ids,
            )
            observed_product_ids = sorted(
                {int(row["product_id"]) for row in rank_rows if row.get("product_id") is not None}
            )
            product_rows = _fetch_by_ids(
                cursor,
                """
                SELECT id AS product_id, marketplace, asin, title, title_zh,
                       product_url, image_url
                FROM products
                """,
                "id",
                observed_product_ids,
            )
            focus_product = None
            if focus_asin_value:
                focus_product = next(
                    (row for row in product_rows if str(row.get("asin") or "").upper() == focus_asin_value),
                    None,
                )
                if focus_product is None:
                    cursor.execute(
                        """
                        SELECT id AS product_id, marketplace, asin, title, title_zh,
                               product_url, image_url
                        FROM products
                        WHERE marketplace = %s AND asin = %s LIMIT 1
                        """,
                        (niche["marketplace"], focus_asin_value),
                    )
                    focus_product = cursor.fetchone()

    integrity = _source_integrity(
        identity_present=bool(identity),
        expected_keyword_ids=expected_keyword_ids,
        keyword_rows=keyword_rows,
        expected_rank_ids=expected_rank_ids,
        rank_rows=rank_rows,
        expected_serp_ids=expected_serp_ids,
        serp_rows=loaded_serp_rows,
        expected_product_snapshot_ids=expected_product_snapshot_ids,
        product_snapshot_rows=product_snapshot_rows,
        observed_product_ids=observed_product_ids,
        product_rows=product_rows,
    )
    frozen_brands = {row["product_id"]: row["brand"] for row in product_manifest}
    snapshots_by_product = {
        int(row["product_id"]): row
        for row in product_snapshot_rows
        if row.get("product_id") is not None
    }
    enriched_products: list[dict[str, Any]] = []
    for row in product_rows:
        product_id = int(row["product_id"])
        enriched = dict(row)
        enriched["brand"] = frozen_brands.get(product_id)
        enriched.update(snapshots_by_product.get(product_id, {}))
        enriched_products.append(enriched)

    return build_competitive_graph_data(
        niche=_normalize_row(niche),
        snapshot=_normalize_snapshot(snapshot),
        available_snapshots=[_snapshot_option(row) for row in snapshots],
        keyword_rows=keyword_rows,
        rank_rows=rank_rows,
        product_rows=enriched_products,
        source_integrity=integrity,
        source_warnings=list(raw.get("warnings") or []),
        limit=limit_value,
        min_shared=min_shared_value,
        focus_asin=focus_asin_value,
        focus_product=focus_product,
    )


def build_competitive_graph_data(
    *,
    niche: dict[str, Any],
    snapshot: dict[str, Any],
    keyword_rows: Iterable[dict[str, Any]],
    rank_rows: Iterable[dict[str, Any]],
    product_rows: Iterable[dict[str, Any]],
    source_integrity: dict[str, Any] | None = None,
    source_warnings: Iterable[str] = (),
    available_snapshots: Iterable[dict[str, Any]] = (),
    limit: int = 100,
    min_shared: int = 1,
    focus_asin: str | None = None,
    focus_product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    keyword_list = sorted(
        (_normalize_row(row) for row in keyword_rows),
        key=lambda row: (str(row.get("keyword") or "").lower(), int(row.get("keyword_id") or 0)),
    )
    keyword_by_id = {
        int(row["keyword_id"]): row
        for row in keyword_list
        if row.get("keyword_id") is not None
    }
    deduped_edges = _deduplicate_rank_edges(rank_rows, set(keyword_by_id))
    product_list = [_normalize_row(row) for row in product_rows]
    product_by_id = {
        int(row["product_id"]): row
        for row in product_list
        if row.get("product_id") is not None
    }

    keyword_products: dict[int, set[int]] = defaultdict(set)
    keyword_organic_products: dict[int, set[int]] = defaultdict(set)
    product_edges: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for edge in deduped_edges:
        keyword_id = int(edge["keyword_id"])
        product_id = int(edge["product_id"])
        keyword_products[keyword_id].add(product_id)
        if _is_organic(edge):
            keyword_organic_products[keyword_id].add(product_id)
        product_edges[product_id][keyword_id] = edge

    keyword_nodes: list[dict[str, Any]] = []
    for keyword_id, row in keyword_by_id.items():
        all_products = keyword_products.get(keyword_id, set())
        organic_products = keyword_organic_products.get(keyword_id, set())
        sponsored_count = sum(
            1 for product_id in all_products if bool(product_edges[product_id][keyword_id].get("is_sponsored"))
        )
        keyword_nodes.append(
            {
                **row,
                "observed_product_count": len(all_products),
                "organic_product_count": len(organic_products),
                "sponsored_product_count": sponsored_count,
            }
        )
    keyword_nodes.sort(
        key=lambda row: (-int(row["observed_product_count"]), str(row.get("keyword") or "").lower())
    )

    keyword_relations: list[dict[str, Any]] = []
    for left, right in combinations(keyword_nodes, 2):
        left_id = int(left["keyword_id"])
        right_id = int(right["keyword_id"])
        left_products = keyword_products.get(left_id, set())
        right_products = keyword_products.get(right_id, set())
        shared_products = left_products & right_products
        if len(shared_products) < min_shared:
            continue
        union_count = len(left_products | right_products)
        confidence = relationship_confidence(
            len(shared_products), len(left_products), len(right_products), union_count
        )
        keyword_relations.append(
            {
                "left_keyword_id": left_id,
                "left_keyword": left.get("keyword"),
                "right_keyword_id": right_id,
                "right_keyword": right.get("keyword"),
                "left_product_count": len(left_products),
                "right_product_count": len(right_products),
                "shared_product_count": len(shared_products),
                "shared_organic_product_count": len(
                    keyword_organic_products.get(left_id, set())
                    & keyword_organic_products.get(right_id, set())
                ),
                "union_product_count": union_count,
                "jaccard": _ratio(len(shared_products), union_count),
                "left_containment": _ratio(len(shared_products), len(left_products)),
                "right_containment": _ratio(len(shared_products), len(right_products)),
                "confidence": confidence["code"],
                "confidence_label": confidence["label"],
                "confidence_reason": confidence["reason"],
            }
        )
    keyword_relations.sort(
        key=lambda row: (
            -int(row["shared_product_count"]),
            -float(row["jaccard"] or 0),
            str(row["left_keyword"] or "").lower(),
            str(row["right_keyword"] or "").lower(),
        )
    )

    keyword_count = len(keyword_by_id)
    competitors = [
        _build_competitor_row(
            product_id,
            product_by_id.get(product_id, {}),
            edges,
            keyword_nodes,
            keyword_count,
        )
        for product_id, edges in product_edges.items()
    ]
    competitors.sort(key=_competitor_sort_key)
    returned_competitors = competitors[:limit]

    fallback_focus_product = focus_product
    if focus_asin and fallback_focus_product is None:
        fallback_focus_product = {"asin": focus_asin, "marketplace": niche.get("marketplace")}
    focus = _build_focus_analysis(
        focus_asin=focus_asin,
        focus_product=fallback_focus_product,
        product_by_id=product_by_id,
        product_edges=product_edges,
        keyword_nodes=keyword_nodes,
        competitors=competitors,
    )
    integrity = source_integrity or {
        "complete": True,
        "warnings": [],
        "expected": {},
        "loaded": {},
        "missing": {},
    }
    warnings = list(dict.fromkeys([*source_warnings, *integrity.get("warnings", [])]))
    if not deduped_edges:
        warnings.append("所选快照未冻结可用排名记录，当前无法形成关键词-ASIN关系")
    if len(keyword_nodes) < 2:
        warnings.append("可用成员关键词少于 2 个，无法比较关键词共现关系")
    if len(competitors) > limit:
        warnings.append(f"竞品矩阵仅展示覆盖最高的前 {limit} 个，共观察到 {len(competitors)} 个 ASIN")
    rank_source_times = sorted(
        {
            str(edge.get("snapshot_at"))
            for edge in deduped_edges
            if edge.get("snapshot_at") not in (None, "")
        }
    )
    if len(rank_source_times) > 1:
        warnings.append("成员关键词的最新采集批次时间不完全一致，关系不是严格同一时刻截面")

    return {
        "niche": niche,
        "snapshot": snapshot,
        "available_snapshots": list(available_snapshots),
        "basis": GRAPH_BASIS,
        "source_integrity": integrity,
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "keyword_count": keyword_count,
            "observed_product_count": len(competitors),
            "returned_product_count": len(returned_competitors),
            "keyword_relation_count": len(keyword_relations),
            "rank_edge_count": len(deduped_edges),
            "rank_source_time_count": len(rank_source_times),
            "rank_source_first_at": rank_source_times[0] if rank_source_times else None,
            "rank_source_latest_at": rank_source_times[-1] if rank_source_times else None,
            "cross_keyword_product_count": sum(
                1 for edges in product_edges.values() if len(edges) >= 2
            ),
            "min_shared": min_shared,
            "product_limit": limit,
        },
        "keyword_nodes": keyword_nodes,
        "keyword_relations": keyword_relations,
        "competitors": returned_competitors,
        "focus": focus,
    }


def relationship_confidence(
    shared_count: int,
    left_count: int,
    right_count: int,
    union_count: int | None = None,
) -> dict[str, str]:
    shared = max(0, int(shared_count or 0))
    left = max(0, int(left_count or 0))
    right = max(0, int(right_count or 0))
    union = max(0, int(union_count if union_count is not None else left + right - shared))
    if shared >= 20 and left >= 50 and right >= 50:
        return {"code": "high", "label": "高样本", "reason": "共同 ASIN 不少于 20，且两侧样本均不少于 50"}
    if shared >= 5 and union >= 20:
        return {"code": "medium", "label": "中样本", "reason": "共同 ASIN 不少于 5，且并集样本不少于 20"}
    return {"code": "low", "label": "低样本", "reason": "共同或两侧样本较少，仅作关系线索"}


def organic_visibility_proxy(ranks: Iterable[Any], keyword_count: int) -> float | None:
    total_keywords = int(keyword_count or 0)
    if total_keywords <= 0:
        return None
    score = 0.0
    for value in ranks:
        rank = _positive_rank(value)
        if rank is not None:
            score += 1.0 / math.log2(rank + 1)
    return round(score / total_keywords, 4)


def _build_competitor_row(
    product_id: int,
    product: dict[str, Any],
    edges: dict[int, dict[str, Any]],
    keyword_nodes: list[dict[str, Any]],
    keyword_count: int,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    organic_ranks: list[int] = []
    sponsored_count = 0
    for keyword in keyword_nodes:
        keyword_id = int(keyword["keyword_id"])
        edge = edges.get(keyword_id)
        if edge is None:
            status = "unobserved"
            rank = None
        elif _is_organic(edge):
            status = "organic"
            rank = _positive_rank(edge.get("organic_rank"))
            if rank is not None:
                organic_ranks.append(rank)
        else:
            status = "sponsored"
            rank = None
            sponsored_count += 1
        observations.append(
            {
                "keyword_id": keyword_id,
                "keyword": keyword.get("keyword"),
                "status": status,
                "status_label": _observation_label(status),
                "organic_rank": rank,
                "page_no": edge.get("page_no") if edge else None,
            }
        )
    observed_count = len(edges)
    result = {
        **product,
        "product_id": product_id,
        "observed_keyword_count": observed_count,
        "organic_keyword_count": len(organic_ranks),
        "sponsored_keyword_count": sponsored_count,
        "keyword_coverage": _ratio(observed_count, keyword_count),
        "organic_coverage": _ratio(len(organic_ranks), keyword_count),
        "best_organic_rank": min(organic_ranks) if organic_ranks else None,
        "average_organic_rank": round(sum(organic_ranks) / len(organic_ranks), 2) if organic_ranks else None,
        "organic_visibility_proxy": organic_visibility_proxy(organic_ranks, keyword_count),
        "keyword_observations": observations,
    }
    return _normalize_row(result)


def _build_focus_analysis(
    *,
    focus_asin: str | None,
    focus_product: dict[str, Any] | None,
    product_by_id: dict[int, dict[str, Any]],
    product_edges: dict[int, dict[int, dict[str, Any]]],
    keyword_nodes: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not focus_asin:
        return None
    matched_product = next(
        (row for row in product_by_id.values() if str(row.get("asin") or "").upper() == focus_asin),
        None,
    )
    current = _normalize_row(matched_product or focus_product or {"asin": focus_asin})
    product_id = int(current["product_id"]) if current.get("product_id") is not None else None
    edges = product_edges.get(product_id, {}) if product_id is not None else {}
    gaps: list[dict[str, Any]] = []
    organic_count = 0
    sponsored_count = 0
    for keyword in keyword_nodes:
        keyword_id = int(keyword["keyword_id"])
        edge = edges.get(keyword_id)
        if edge is None:
            status = "unobserved"
            rank = None
        elif _is_organic(edge):
            status = "organic"
            rank = _positive_rank(edge.get("organic_rank"))
            organic_count += 1
        else:
            status = "sponsored"
            rank = None
            sponsored_count += 1
        gaps.append(
            {
                "keyword_id": keyword_id,
                "keyword": keyword.get("keyword"),
                "status": status,
                "status_label": _observation_label(status),
                "organic_rank": rank,
                "page_no": edge.get("page_no") if edge else None,
            }
        )

    focus_keyword_ids = set(edges)
    similar: list[dict[str, Any]] = []
    if focus_keyword_ids:
        for row in competitors:
            other_id = int(row.get("product_id") or 0)
            if product_id is not None and other_id == product_id:
                continue
            other_keyword_ids = set(product_edges.get(other_id, {}))
            shared_ids = focus_keyword_ids & other_keyword_ids
            if not shared_ids:
                continue
            shared_names = [
                str(keyword.get("keyword") or "")
                for keyword in keyword_nodes
                if int(keyword["keyword_id"]) in shared_ids
            ]
            similar.append(
                {
                    "product_id": other_id,
                    "asin": row.get("asin"),
                    "title": row.get("title"),
                    "title_zh": row.get("title_zh"),
                    "marketplace": row.get("marketplace"),
                    "product_url": row.get("product_url"),
                    "shared_keyword_count": len(shared_ids),
                    "coverage_jaccard": _ratio(len(shared_ids), len(focus_keyword_ids | other_keyword_ids)),
                    "shared_keywords": shared_names,
                }
            )
        similar.sort(
            key=lambda row: (
                -int(row["shared_keyword_count"]),
                -float(row["coverage_jaccard"] or 0),
                str(row.get("asin") or ""),
            )
        )

    return {
        "product": current,
        "found_in_database": product_id is not None,
        "observed_in_snapshot": bool(edges),
        "summary": {
            "keyword_count": len(keyword_nodes),
            "organic_count": organic_count,
            "sponsored_count": sponsored_count,
            "unobserved_count": len(keyword_nodes) - organic_count - sponsored_count,
        },
        "keyword_gaps": gaps,
        "similar_competitors": similar[:10],
        "message": (
            "该 ASIN 已在所选快照的成员关键词中被观察到。"
            if edges
            else "该 ASIN 未在所选快照的已采集成员关键词范围内被观察到。"
        ),
    }


def _deduplicate_rank_edges(
    rows: Iterable[dict[str, Any]],
    allowed_keyword_ids: set[int],
) -> list[dict[str, Any]]:
    selected: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in rows:
        row = _normalize_row(raw)
        try:
            keyword_id = int(row["keyword_id"])
            product_id = int(row["product_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if keyword_id not in allowed_keyword_ids or product_id <= 0:
            continue
        key = (keyword_id, product_id)
        previous = selected.get(key)
        if previous is None or _edge_priority(row) < _edge_priority(previous):
            selected[key] = row
    return sorted(
        selected.values(),
        key=lambda row: (int(row["keyword_id"]), int(row["product_id"]), int(row.get("id") or 0)),
    )


def _edge_priority(row: dict[str, Any]) -> tuple[int, int, int, int]:
    rank = _positive_rank(row.get("organic_rank"))
    return (
        0 if rank is not None and not bool(row.get("is_sponsored")) else 1,
        1 if bool(row.get("is_sponsored")) else 0,
        rank if rank is not None else 10**9,
        int(row.get("id") or 0),
    )


def _competitor_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    monthly = _finite_float(row.get("monthly_bought"))
    reviews = _finite_float(row.get("review_count"))
    return (
        -int(row.get("observed_keyword_count") or 0),
        -int(row.get("organic_keyword_count") or 0),
        -float(row.get("organic_visibility_proxy") or 0),
        -(monthly if monthly is not None else -1),
        -(reviews if reviews is not None else -1),
        str(row.get("asin") or ""),
    )


def _source_integrity(
    *,
    identity_present: bool,
    expected_keyword_ids: list[int],
    keyword_rows: list[dict[str, Any]],
    expected_rank_ids: list[int],
    rank_rows: list[dict[str, Any]],
    expected_serp_ids: list[int],
    serp_rows: list[dict[str, Any]],
    expected_product_snapshot_ids: list[int],
    product_snapshot_rows: list[dict[str, Any]],
    observed_product_ids: list[int],
    product_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    loaded_keyword_ids = {int(row["keyword_id"]) for row in keyword_rows}
    loaded_rank_ids = {int(row["id"]) for row in rank_rows}
    loaded_serp_ids = {int(row["id"]) for row in serp_rows}
    loaded_product_snapshot_ids = {int(row["snapshot_id"]) for row in product_snapshot_rows}
    loaded_product_ids = {int(row["product_id"]) for row in product_rows}
    missing = {
        "keyword_ids": sorted(set(expected_keyword_ids) - loaded_keyword_ids),
        "rank_snapshot_ids": sorted(set(expected_rank_ids) - loaded_rank_ids),
        "serp_snapshot_ids": sorted(set(expected_serp_ids) - loaded_serp_ids),
        "product_snapshot_ids": sorted(set(expected_product_snapshot_ids) - loaded_product_snapshot_ids),
        "product_ids": sorted(set(observed_product_ids) - loaded_product_ids),
    }
    warnings: list[str] = []
    if not identity_present:
        warnings.append("所选利基快照没有冻结来源清单，系统没有改用最新数据")
    labels = {
        "keyword_ids": "关键词",
        "rank_snapshot_ids": "排名快照",
        "serp_snapshot_ids": "SERP 快照",
        "product_snapshot_ids": "商品快照",
        "product_ids": "商品",
    }
    for key, ids in missing.items():
        if ids:
            warnings.append(f"冻结来源中的{labels[key]}有 {len(ids)} 条已缺失，图谱仅使用仍可复核的记录")
    return {
        "complete": identity_present and not any(missing.values()),
        "expected": {
            "keyword_count": len(expected_keyword_ids),
            "rank_snapshot_count": len(expected_rank_ids),
            "serp_snapshot_count": len(expected_serp_ids),
            "product_snapshot_count": len(expected_product_snapshot_ids),
            "product_count": len(observed_product_ids),
        },
        "loaded": {
            "keyword_count": len(loaded_keyword_ids),
            "rank_snapshot_count": len(loaded_rank_ids),
            "serp_snapshot_count": len(loaded_serp_ids),
            "product_snapshot_count": len(loaded_product_snapshot_ids),
            "product_count": len(loaded_product_ids),
        },
        "missing": {key: values[:20] for key, values in missing.items()},
        "warnings": warnings,
    }


def _fetch_by_ids(
    cursor: Any,
    select_sql: str,
    id_column: str,
    values: Iterable[int],
) -> list[dict[str, Any]]:
    ids = sorted({int(value) for value in values if int(value) > 0})
    if not ids:
        return []
    rows: list[dict[str, Any]] = []
    for start in range(0, len(ids), SOURCE_BATCH_SIZE):
        batch = ids[start:start + SOURCE_BATCH_SIZE]
        placeholders = ", ".join(["%s"] * len(batch))
        cursor.execute(f"{select_sql} WHERE {id_column} IN ({placeholders})", batch)
        rows.extend(cursor.fetchall())
    return rows


def _parse_product_manifest(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            product_id = int(item[0])
            snapshot_id = int(item[1])
        except (TypeError, ValueError):
            continue
        if product_id <= 0 or snapshot_id <= 0:
            continue
        result.append(
            {
                "product_id": product_id,
                "snapshot_id": snapshot_id,
                "brand": str(item[2]).strip() if len(item) >= 3 and item[2] else None,
            }
        )
    return result


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row)
    raw = _parse_json(result.pop("raw_json", None))
    result["warnings"] = list(raw.get("warnings") or [])
    return result


def _snapshot_option(row: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_row(row)
    normalized.pop("raw_json", None)
    return normalized


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat(sep=" ")
        elif isinstance(value, date):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _unique_positive_ints(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: set[int] = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.add(number)
    return sorted(result)


def _normalize_asin(value: str | None) -> str | None:
    result = str(value or "").strip().upper()
    if not result:
        return None
    if not ASIN_RE.fullmatch(result):
        raise CompetitiveGraphError("目标 ASIN 必须是 10 位字母或数字")
    return result


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CompetitiveGraphError(f"{label}必须是正整数") from exc
    if result <= 0:
        raise CompetitiveGraphError(f"{label}必须是正整数")
    return result


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, label)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _positive_rank(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _is_organic(edge: dict[str, Any]) -> bool:
    return not bool(edge.get("is_sponsored")) and _positive_rank(edge.get("organic_rank")) is not None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _observation_label(status: str) -> str:
    return {
        "organic": "观察到自然位次",
        "sponsored": "仅观察到广告",
        "unobserved": "未在已采集范围观察到",
    }.get(status, status)
