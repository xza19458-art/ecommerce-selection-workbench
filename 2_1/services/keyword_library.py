"""Keyword asset library backed by the existing keywords table."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any

from database.mysql_client import MySQLClient


VALID_SNAPSHOT_FILTERS = {"all", "with", "without"}
VALID_TRACKING_FILTERS = {"all", "any", "none", "active", "paused", "completed", "error"}
VALID_SOURCE_FILTERS = {"all", "workshop", "non_workshop"}
VIRTUAL_CATEGORY_PREPOSITIONS = {"for", "with", "of", "to", "in", "on"}
GENERIC_SINGLE_CATEGORY_TOKENS = {
    "toy",
    "gift",
    "set",
    "pack",
    "kid",
    "adult",
    "men",
    "man",
    "women",
    "woman",
}


def fetch_keyword_assets_page(
    limit: int = 100,
    *,
    offset: int = 0,
    marketplace: str = "US",
    keyword: str | None = None,
    snapshot_filter: str = "all",
    tracking_filter: str = "all",
    source_filter: str = "all",
    sort_by: str = "latest_snapshot_at",
    sort_dir: str = "desc",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Return all keyword assets, including keywords without rank snapshots."""

    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    where_sql, params = _build_asset_filters(
        marketplace=marketplace,
        keyword=keyword,
        snapshot_filter=snapshot_filter,
        tracking_filter=tracking_filter,
        source_filter=source_filter,
    )
    select_sql = _keyword_asset_select(where_sql)
    order_sql, normalized_sort, normalized_dir = _keyword_asset_order_by(sort_by, sort_dir)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            summary = _fetch_summary(cursor, marketplace)
            cursor.execute(f"SELECT COUNT(*) AS total FROM ({select_sql}) AS q", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                {select_sql}
                {order_sql}
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_normalize_asset_row(row) for row in cursor.fetchall()]

    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "sort_by": normalized_sort,
        "sort_dir": normalized_dir,
        "summary": summary,
    }


def fetch_keyword_asset_tree(
    *,
    marketplace: str = "US",
    keyword: str | None = None,
    snapshot_filter: str = "all",
    tracking_filter: str = "all",
    source_filter: str = "all",
    max_keywords: int = 500,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Return filtered keyword assets grouped into an inferred parent-child tree."""

    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    limit_value = _normalize_limit(max_keywords)
    where_sql, params = _build_asset_filters(
        marketplace=marketplace,
        keyword=keyword,
        snapshot_filter=snapshot_filter,
        tracking_filter=tracking_filter,
        source_filter=source_filter,
    )
    select_sql = _keyword_asset_select(where_sql)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            summary = _fetch_summary(cursor, marketplace)
            cursor.execute(f"SELECT COUNT(*) AS total FROM ({select_sql}) AS q", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                {select_sql}
                ORDER BY
                  CHAR_LENGTH(k.keyword) ASC,
                  product_count DESC,
                  k.keyword ASC
                LIMIT %s
                """,
                params + [limit_value],
            )
            rows = [_normalize_asset_row(row) for row in cursor.fetchall()]

    roots = _build_keyword_tree(rows)
    return {
        "roots": roots,
        "total": total,
        "node_count": len(rows),
        "root_count": len(roots),
        "max_depth": _tree_max_depth(roots),
        "max_keywords": limit_value,
        "truncated": total > len(rows),
        "summary": summary,
    }


def fetch_keyword_asset_detail(
    keyword_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    keyword_id = _positive_int(keyword_id)
    where_sql, params = _build_asset_filters(keyword_id=keyword_id)
    select_sql = _keyword_asset_select(where_sql)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"{select_sql} LIMIT 1", params)
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"关键词不存在: {keyword_id}")
            asset = _normalize_asset_row(row)
            has_product_size = db.has_columns(cursor, "products", ("product_size",))
            products = _fetch_keyword_products(cursor, keyword_id, limit=20, has_product_size=has_product_size)
    return {"asset": asset, "products": products}


def create_tracking_for_keywords(
    keyword_ids: list[int],
    *,
    marketplace: str = "US",
    target_snapshots: int = 3,
    pages_per_keyword: int | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    ids = _normalize_ids(keyword_ids)
    if not ids:
        raise ValueError("请选择要创建追踪的关键词")
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    target_snapshots = _normalize_positive_int(target_snapshots, default=3, maximum=365)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            rows = _fetch_keywords_by_ids(cursor, ids, marketplace=marketplace)
    if not rows:
        raise ValueError("未找到可创建追踪的关键词")

    from services.keyword_tracking import create_tracking_task, list_tracking_tasks

    tasks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        keyword = str(row["keyword"])
        existing = [
            task for task in list_tracking_tasks(
                status="active",
                marketplace=marketplace,
                keyword=keyword,
                limit=20,
                client=client,
            )
            if task.marketplace == marketplace and task.keyword == keyword
        ]
        if existing:
            task = existing[0]
            tasks.append(task.to_dict())
            warnings.append(f"关键词「{keyword}」已有 active 追踪任务 #{task.id}，未重复创建。")
            continue
        task = create_tracking_task(
            marketplace=marketplace,
            keyword=keyword,
            target_snapshots=target_snapshots,
            pages_per_keyword=pages_per_keyword,
            client=client,
        )
        tasks.append(task.to_dict())

    return {"created_or_existing": len(tasks), "tasks": tasks, "warnings": warnings, "ids": ids}


def _build_keyword_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    token_meta: dict[int, tuple[str, tuple[str, ...], Counter[str]]] = {}
    for row in rows:
        keyword_id = _to_int(row.get("keyword_id"))
        keyword = str(row.get("keyword") or "").strip()
        tokens = tuple(_keyword_tokens(keyword))
        node = {
            **row,
            "keyword_id": keyword_id,
            "parent_keyword_id": None,
            "parent_keyword": None,
            "children": [],
            "child_count": 0,
            "descendant_count": 0,
            "depth": 1,
            "path": [keyword] if keyword else [],
            "token_count": len(tokens),
        }
        nodes.append(node)
        token_meta[keyword_id] = (keyword, tokens, Counter(tokens))

    by_id = {node["keyword_id"]: node for node in nodes}
    for node in nodes:
        node_id = node["keyword_id"]
        keyword, tokens, counts = token_meta[node_id]
        best_parent_id: int | None = None
        best_score: tuple[int, int, int, str] | None = None
        for candidate in nodes:
            parent_id = candidate["keyword_id"]
            if parent_id == node_id:
                continue
            parent_keyword, parent_tokens, parent_counts = token_meta[parent_id]
            if not _is_keyword_parent(parent_counts, counts, parent_tokens, tokens):
                continue
            phrase_bonus = 1 if _contains_token_phrase(parent_tokens, tokens) else 0
            score = (len(parent_tokens), phrase_bonus, len(parent_keyword), parent_keyword.lower())
            if best_score is None or score > best_score:
                best_parent_id = parent_id
                best_score = score
        if best_parent_id is not None:
            parent = by_id[best_parent_id]
            node["parent_keyword_id"] = parent["keyword_id"]
            node["parent_keyword"] = parent["keyword"]
            parent["children"].append(node)

    roots = _attach_virtual_category_roots([node for node in nodes if node["parent_keyword_id"] is None])
    for root in roots:
        _refresh_tree_node(root, depth=1, path=[])
    _sort_tree_nodes(roots)
    return roots


def _attach_virtual_category_roots(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_map: dict[int, list[tuple[str, str]]] = {}
    frequency: Counter[str] = Counter()
    labels: dict[str, Counter[str]] = {}
    for root in roots:
        root_id = _to_int(root.get("keyword_id"))
        candidates = _virtual_category_candidates(str(root.get("keyword") or ""))
        candidate_map[root_id] = candidates
        for key, label in {key: label for key, label in candidates}.items():
            frequency[key] += 1
            labels.setdefault(key, Counter())[label] += 1

    grouped: dict[str, list[dict[str, Any]]] = {}
    remaining: list[dict[str, Any]] = []
    for root in roots:
        root_key = _normalized_keyword_key(str(root.get("keyword") or ""))
        selected_key = None
        for key, _label in candidate_map.get(_to_int(root.get("keyword_id")), []):
            if key == root_key or frequency[key] < 2:
                continue
            selected_key = key
            break
        if selected_key:
            grouped.setdefault(selected_key, []).append(root)
        else:
            remaining.append(root)

    virtual_id = -1
    virtual_roots: list[dict[str, Any]] = []
    for key, children in grouped.items():
        label = labels.get(key, Counter()).most_common(1)[0][0] if labels.get(key) else key.title()
        virtual_roots.append(_make_virtual_category_node(virtual_id, key, label, children))
        virtual_id -= 1
    return virtual_roots + remaining


def _virtual_category_candidates(keyword: str) -> list[tuple[str, str]]:
    words = re.findall(r"[A-Za-z0-9]+", keyword)
    tokens = [_normalize_keyword_token(word.lower()) for word in words if word]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()

    for index, token in enumerate(tokens):
        if token in VIRTUAL_CATEGORY_PREPOSITIONS and index >= 2:
            _append_virtual_candidate(result, seen, tokens[:index], words[:index])

    if len(tokens) > 1:
        _append_virtual_candidate(result, seen, tokens[-1:], words[-1:])
        for width in range(2, min(4, len(tokens))):
            _append_virtual_candidate(result, seen, tokens[-width:], words[-width:])
    return result


def _append_virtual_candidate(
    result: list[tuple[str, str]],
    seen: set[str],
    tokens: list[str],
    words: list[str],
) -> None:
    if not tokens:
        return
    if len(tokens) == 1 and tokens[0] in GENERIC_SINGLE_CATEGORY_TOKENS:
        return
    key = " ".join(tokens)
    if not key or key in seen:
        return
    label = _format_virtual_category_label(" ".join(words).strip())
    result.append((key, label or key.title()))
    seen.add(key)


def _format_virtual_category_label(label: str) -> str:
    if label and label == label.lower():
        return label.title()
    return label


def _normalized_keyword_key(keyword: str) -> str:
    return " ".join(_keyword_tokens(keyword))


def _make_virtual_category_node(
    virtual_id: int,
    key: str,
    label: str,
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    for child in children:
        child["parent_keyword_id"] = virtual_id
        child["parent_keyword"] = label
    return {
        "keyword_id": virtual_id,
        "marketplace": children[0].get("marketplace") if children else None,
        "keyword": label,
        "created_at": None,
        "product_count": sum(_to_int(child.get("product_count")) for child in children),
        "snapshot_time_count": sum(_to_int(child.get("snapshot_time_count")) for child in children),
        "rank_snapshot_count": sum(_to_int(child.get("rank_snapshot_count")) for child in children),
        "latest_snapshot_at": _max_present(child.get("latest_snapshot_at") for child in children),
        "avg_organic_rank": _avg_present(child.get("avg_organic_rank") for child in children),
        "top10_count": sum(_to_int(child.get("top10_count")) for child in children),
        "sponsored_count": sum(_to_int(child.get("sponsored_count")) for child in children),
        "avg_total_score": _avg_present(child.get("avg_total_score") for child in children),
        "avg_demand_score": _avg_present(child.get("avg_demand_score") for child in children),
        "avg_competition_score": _avg_present(child.get("avg_competition_score") for child in children),
        "avg_rating_score": _avg_present(child.get("avg_rating_score") for child in children),
        "avg_price_score": _avg_present(child.get("avg_price_score") for child in children),
        "avg_rank_score": _avg_present(child.get("avg_rank_score") for child in children),
        "latest_score_date": _max_present(child.get("latest_score_date") for child in children),
        "idea_id": None,
        "idea_count": sum(_to_int(child.get("idea_count")) for child in children),
        "idea_statuses": [],
        "source_types": [],
        "idea_score": _avg_present(child.get("idea_score") for child in children),
        "confidence_score": _avg_present(child.get("confidence_score") for child in children),
        "recommendation_level": None,
        "last_run_id": None,
        "idea_updated_at": _max_present(child.get("idea_updated_at") for child in children),
        "tracking_task_count": sum(_to_int(child.get("tracking_task_count")) for child in children),
        "active_tracking_task_id": None,
        "latest_tracking_task_id": None,
        "active_count": sum(_to_int(child.get("active_count")) for child in children),
        "paused_count": sum(_to_int(child.get("paused_count")) for child in children),
        "completed_count": sum(_to_int(child.get("completed_count")) for child in children),
        "error_count": sum(_to_int(child.get("error_count")) for child in children),
        "tracking_last_collected_at": _max_present(child.get("tracking_last_collected_at") for child in children),
        "tracking_last_checked_at": _max_present(child.get("tracking_last_checked_at") for child in children),
        "tracking_achieved_snapshots": 0,
        "tracking_target_snapshots": 0,
        "tracking_status": _tracking_status({
            "active_count": sum(_to_int(child.get("active_count")) for child in children),
            "error_count": sum(_to_int(child.get("error_count")) for child in children),
            "paused_count": sum(_to_int(child.get("paused_count")) for child in children),
            "completed_count": sum(_to_int(child.get("completed_count")) for child in children),
            "tracking_task_count": sum(_to_int(child.get("tracking_task_count")) for child in children),
        }),
        "tracking_task_id": None,
        "has_snapshots": any(child.get("has_snapshots") for child in children),
        "has_tracking": any(child.get("has_tracking") for child in children),
        "has_workshop_idea": any(child.get("has_workshop_idea") for child in children),
        "virtual": True,
        "virtual_key": key,
        "parent_keyword_id": None,
        "parent_keyword": None,
        "children": children,
        "child_count": len(children),
        "descendant_count": 0,
        "depth": 1,
        "path": [label],
        "token_count": len(key.split()),
    }


def _avg_present(values: Any) -> float | None:
    numbers = [_to_float(value) for value in values if value is not None and value != ""]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def _max_present(values: Any) -> Any:
    present = [value for value in values if value is not None and value != ""]
    return max(present) if present else None


def _keyword_tokens(keyword: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", keyword.lower())
    return [_normalize_keyword_token(token) for token in tokens if token]


def _normalize_keyword_token(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _is_keyword_parent(
    parent_counts: Counter[str],
    child_counts: Counter[str],
    parent_tokens: tuple[str, ...],
    child_tokens: tuple[str, ...],
) -> bool:
    if not parent_tokens or len(parent_tokens) >= len(child_tokens):
        return False
    for token, count in parent_counts.items():
        if child_counts[token] < count:
            return False
    return True


def _contains_token_phrase(parent_tokens: tuple[str, ...], child_tokens: tuple[str, ...]) -> bool:
    if not parent_tokens or len(parent_tokens) > len(child_tokens):
        return False
    width = len(parent_tokens)
    return any(child_tokens[index:index + width] == parent_tokens for index in range(len(child_tokens) - width + 1))


def _refresh_tree_node(node: dict[str, Any], *, depth: int, path: list[str]) -> int:
    keyword = str(node.get("keyword") or "")
    current_path = path + ([keyword] if keyword else [])
    node["depth"] = depth
    node["path"] = current_path
    total = 0
    for child in node["children"]:
        total += 1 + _refresh_tree_node(child, depth=depth + 1, path=current_path)
    node["child_count"] = len(node["children"])
    node["descendant_count"] = total
    _sort_tree_nodes(node["children"])
    return total


def _sort_tree_nodes(nodes: list[dict[str, Any]]) -> None:
    nodes.sort(
        key=lambda node: (
            -_to_int(node.get("descendant_count")),
            -_to_int(node.get("child_count")),
            _to_int(node.get("token_count")),
            str(node.get("keyword") or "").lower(),
        )
    )


def _tree_max_depth(nodes: list[dict[str, Any]]) -> int:
    max_depth = 0
    stack = list(nodes)
    while stack:
        node = stack.pop()
        max_depth = max(max_depth, _to_int(node.get("depth")))
        stack.extend(node.get("children") or [])
    return max_depth


def _keyword_asset_select(where_sql: str) -> str:
    return f"""
        SELECT
          k.id AS keyword_id,
          k.marketplace,
          k.keyword,
          k.created_at,
          COALESCE(rank_stats.product_count, 0) AS product_count,
          COALESCE(rank_stats.snapshot_time_count, 0) AS snapshot_time_count,
          COALESCE(rank_stats.rank_snapshot_count, 0) AS rank_snapshot_count,
          rank_stats.latest_snapshot_at,
          rank_stats.avg_organic_rank,
          COALESCE(rank_stats.top10_count, 0) AS top10_count,
          COALESCE(rank_stats.sponsored_count, 0) AS sponsored_count,
          score_stats.avg_total_score,
          score_stats.avg_demand_score,
          score_stats.avg_competition_score,
          score_stats.avg_rating_score,
          score_stats.avg_price_score,
          score_stats.avg_rank_score,
          score_stats.latest_score_date,
          idea_stats.idea_id,
          COALESCE(idea_stats.idea_count, 0) AS idea_count,
          idea_stats.idea_statuses,
          idea_stats.source_types,
          idea_stats.idea_score,
          idea_stats.confidence_score,
          idea_stats.recommendation_level,
          idea_stats.last_run_id,
          idea_stats.idea_updated_at,
          COALESCE(track_stats.tracking_task_count, 0) AS tracking_task_count,
          track_stats.active_tracking_task_id,
          track_stats.latest_tracking_task_id,
          COALESCE(track_stats.active_count, 0) AS active_count,
          COALESCE(track_stats.paused_count, 0) AS paused_count,
          COALESCE(track_stats.completed_count, 0) AS completed_count,
          COALESCE(track_stats.error_count, 0) AS error_count,
          track_stats.tracking_last_collected_at,
          track_stats.tracking_last_checked_at,
          track_stats.tracking_achieved_snapshots,
          track_stats.tracking_target_snapshots
        FROM keywords k
        LEFT JOIN (
          SELECT
            keyword_id,
            COUNT(DISTINCT product_id) AS product_count,
            COUNT(DISTINCT snapshot_at) AS snapshot_time_count,
            COUNT(*) AS rank_snapshot_count,
            MAX(snapshot_at) AS latest_snapshot_at,
            AVG(organic_rank) AS avg_organic_rank,
            SUM(CASE WHEN organic_rank IS NOT NULL AND organic_rank <= 10 THEN 1 ELSE 0 END) AS top10_count,
            SUM(CASE WHEN is_sponsored = 1 THEN 1 ELSE 0 END) AS sponsored_count
          FROM keyword_rank_snapshots
          GROUP BY keyword_id
        ) rank_stats ON rank_stats.keyword_id = k.id
        LEFT JOIN (
          SELECT
            ps.keyword_id,
            AVG(ps.total_score) AS avg_total_score,
            AVG(ps.demand_score) AS avg_demand_score,
            AVG(ps.competition_score) AS avg_competition_score,
            AVG(ps.rating_score) AS avg_rating_score,
            AVG(ps.price_score) AS avg_price_score,
            AVG(ps.rank_score) AS avg_rank_score,
            MAX(ps.score_date) AS latest_score_date
          FROM product_scores ps
          JOIN (
            SELECT product_id, keyword_id, MAX(score_date) AS score_date
            FROM product_scores
            WHERE keyword_id IS NOT NULL
            GROUP BY product_id, keyword_id
          ) latest
            ON latest.product_id = ps.product_id
           AND latest.keyword_id = ps.keyword_id
           AND latest.score_date = ps.score_date
          GROUP BY ps.keyword_id
        ) score_stats ON score_stats.keyword_id = k.id
        LEFT JOIN (
          SELECT
            promoted_keyword_id AS keyword_id,
            MAX(id) AS idea_id,
            COUNT(*) AS idea_count,
            GROUP_CONCAT(DISTINCT status ORDER BY status SEPARATOR ',') AS idea_statuses,
            GROUP_CONCAT(DISTINCT source_types ORDER BY source_types SEPARATOR ',') AS source_types,
            MAX(idea_score) AS idea_score,
            MAX(confidence_score) AS confidence_score,
            MAX(recommendation_level) AS recommendation_level,
            MAX(last_run_id) AS last_run_id,
            MAX(updated_at) AS idea_updated_at
          FROM keyword_ideas
          WHERE promoted_keyword_id IS NOT NULL
          GROUP BY promoted_keyword_id
        ) idea_stats ON idea_stats.keyword_id = k.id
        LEFT JOIN (
          SELECT
            marketplace,
            keyword,
            COUNT(*) AS tracking_task_count,
            MAX(CASE WHEN status = 'active' THEN id ELSE NULL END) AS active_tracking_task_id,
            MAX(id) AS latest_tracking_task_id,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN status = 'paused' THEN 1 ELSE 0 END) AS paused_count,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count,
            MAX(last_collected_at) AS tracking_last_collected_at,
            MAX(last_checked_at) AS tracking_last_checked_at,
            MAX(achieved_snapshots) AS tracking_achieved_snapshots,
            MAX(target_snapshots) AS tracking_target_snapshots
          FROM keyword_tracking_tasks
          GROUP BY marketplace, keyword
        ) track_stats
          ON track_stats.marketplace = k.marketplace
         AND track_stats.keyword = k.keyword
        {where_sql}
    """


def _build_asset_filters(
    *,
    marketplace: str | None = None,
    keyword: str | None = None,
    snapshot_filter: str = "all",
    tracking_filter: str = "all",
    source_filter: str = "all",
    keyword_id: int | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if keyword_id is not None:
        clauses.append("k.id = %s")
        params.append(_positive_int(keyword_id))
    if marketplace:
        clauses.append("k.marketplace = %s")
        params.append(_normalize_marketplace(marketplace))
    if keyword:
        clauses.append("LOWER(k.keyword) LIKE %s")
        params.append(f"%{keyword.strip().lower()}%")

    snapshot_filter = _normalize_choice(snapshot_filter, VALID_SNAPSHOT_FILTERS, "all")
    if snapshot_filter == "with":
        clauses.append("COALESCE(rank_stats.snapshot_time_count, 0) > 0")
    elif snapshot_filter == "without":
        clauses.append("COALESCE(rank_stats.snapshot_time_count, 0) = 0")

    tracking_filter = _normalize_choice(tracking_filter, VALID_TRACKING_FILTERS, "all")
    if tracking_filter == "any":
        clauses.append("COALESCE(track_stats.tracking_task_count, 0) > 0")
    elif tracking_filter == "none":
        clauses.append("COALESCE(track_stats.tracking_task_count, 0) = 0")
    elif tracking_filter in {"active", "paused", "completed", "error"}:
        clauses.append(f"COALESCE(track_stats.{tracking_filter}_count, 0) > 0")

    source_filter = _normalize_choice(source_filter, VALID_SOURCE_FILTERS, "all")
    if source_filter == "workshop":
        clauses.append("COALESCE(idea_stats.idea_count, 0) > 0")
    elif source_filter == "non_workshop":
        clauses.append("COALESCE(idea_stats.idea_count, 0) = 0")

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params


def _keyword_asset_order_by(sort_by: str, sort_dir: str) -> tuple[str, str, str]:
    sorts = {
        "keyword": "k.keyword",
        "product_count": "product_count",
        "snapshot_time_count": "snapshot_time_count",
        "latest_snapshot_at": "latest_snapshot_at",
        "avg_total_score": "avg_total_score",
        "avg_organic_rank": "avg_organic_rank",
        "source_types": "source_types",
        "tracking_status": "active_count",
        "created_at": "k.created_at",
        "keyword_id": "k.id",
    }
    normalized_sort = str(sort_by or "latest_snapshot_at").strip()
    if normalized_sort not in sorts:
        normalized_sort = "latest_snapshot_at"
    normalized_dir = "asc" if str(sort_dir or "").lower() == "asc" else "desc"
    direction = "ASC" if normalized_dir == "asc" else "DESC"
    expr = sorts[normalized_sort]
    return (
        f"ORDER BY {expr} IS NULL, {expr} {direction}, latest_snapshot_at DESC, product_count DESC, keyword_id DESC",
        normalized_sort,
        normalized_dir,
    )


def _fetch_summary(cursor: Any, marketplace: str) -> dict[str, Any]:
    where_sql, params = _build_asset_filters(marketplace=marketplace)
    select_sql = _keyword_asset_select(where_sql)
    cursor.execute(
        f"""
        SELECT
          COUNT(*) AS total_keywords,
          SUM(CASE WHEN snapshot_time_count > 0 THEN 1 ELSE 0 END) AS with_snapshots,
          SUM(CASE WHEN active_count > 0 THEN 1 ELSE 0 END) AS active_tracking,
          SUM(CASE WHEN idea_count > 0 THEN 1 ELSE 0 END) AS workshop_keywords,
          MAX(latest_snapshot_at) AS latest_snapshot_at
        FROM ({select_sql}) AS q
        """,
        params,
    )
    row = cursor.fetchone() or {}
    return {
        "total_keywords": _to_int(row.get("total_keywords")),
        "with_snapshots": _to_int(row.get("with_snapshots")),
        "active_tracking": _to_int(row.get("active_tracking")),
        "workshop_keywords": _to_int(row.get("workshop_keywords")),
        "latest_snapshot_at": _format_date(row.get("latest_snapshot_at")),
    }


def _fetch_keyword_products(
    cursor: Any,
    keyword_id: int,
    *,
    limit: int,
    has_product_size: bool = False,
) -> list[dict[str, Any]]:
    product_size_select = "p.product_size" if has_product_size else "NULL AS product_size"
    cursor.execute(
        f"""
        SELECT
          p.asin,
          p.title,
          p.title_zh,
          {product_size_select},
          snap.price,
          snap.rating,
          snap.review_count,
          snap.monthly_bought,
          krs.organic_rank,
          krs.is_sponsored,
          ps.total_score,
          ps.demand_score,
          ps.competition_score,
          krs.snapshot_at
        FROM keyword_rank_snapshots krs
        JOIN (
          SELECT product_id, MAX(snapshot_at) AS snapshot_at
          FROM keyword_rank_snapshots
          WHERE keyword_id = %s
          GROUP BY product_id
        ) latest_rank
          ON latest_rank.product_id = krs.product_id
         AND latest_rank.snapshot_at = krs.snapshot_at
        JOIN products p ON p.id = krs.product_id
        LEFT JOIN (
          SELECT snap.*
          FROM product_snapshots snap
          JOIN (
            SELECT product_id, MAX(snapshot_at) AS snapshot_at
            FROM product_snapshots
            GROUP BY product_id
          ) latest_snap
            ON latest_snap.product_id = snap.product_id
           AND latest_snap.snapshot_at = snap.snapshot_at
        ) snap ON snap.product_id = p.id
        LEFT JOIN (
          SELECT ps1.*
          FROM product_scores ps1
          JOIN (
            SELECT product_id, keyword_id, MAX(score_date) AS score_date
            FROM product_scores
            WHERE keyword_id = %s
            GROUP BY product_id, keyword_id
          ) latest_score
            ON latest_score.product_id = ps1.product_id
           AND latest_score.keyword_id = ps1.keyword_id
           AND latest_score.score_date = ps1.score_date
        ) ps
          ON ps.product_id = p.id
         AND ps.keyword_id = krs.keyword_id
        WHERE krs.keyword_id = %s
        ORDER BY ps.total_score DESC, snap.monthly_bought DESC, krs.organic_rank ASC
        LIMIT %s
        """,
        (keyword_id, keyword_id, keyword_id, _normalize_limit(limit)),
    )
    rows = []
    for row in cursor.fetchall():
        normalized = dict(row)
        for key in ("price", "rating", "total_score", "demand_score", "competition_score"):
            normalized[key] = _to_float(normalized.get(key))
        for key in ("review_count", "monthly_bought", "organic_rank", "is_sponsored"):
            normalized[key] = _to_int(normalized.get(key))
        normalized["snapshot_at"] = _format_date(normalized.get("snapshot_at"))
        rows.append(normalized)
    return rows


def _fetch_keywords_by_ids(cursor: Any, ids: list[int], *, marketplace: str) -> list[dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"""
        SELECT id, marketplace, keyword
        FROM keywords
        WHERE marketplace = %s
          AND id IN ({placeholders})
        ORDER BY FIELD(id, {placeholders})
        """,
        [marketplace] + ids + ids,
    )
    return cursor.fetchall()


def _normalize_asset_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in (
        "keyword_id",
        "product_count",
        "snapshot_time_count",
        "rank_snapshot_count",
        "top10_count",
        "sponsored_count",
        "idea_count",
        "tracking_task_count",
        "active_count",
        "paused_count",
        "completed_count",
        "error_count",
        "tracking_achieved_snapshots",
        "tracking_target_snapshots",
    ):
        normalized[key] = _to_int(normalized.get(key))
    for key in (
        "idea_id",
        "last_run_id",
        "active_tracking_task_id",
        "latest_tracking_task_id",
    ):
        normalized[key] = _to_optional_int(normalized.get(key))
    for key in (
        "avg_organic_rank",
        "avg_total_score",
        "avg_demand_score",
        "avg_competition_score",
        "avg_rating_score",
        "avg_price_score",
        "avg_rank_score",
        "idea_score",
        "confidence_score",
    ):
        normalized[key] = _to_float(normalized.get(key))
    for key in (
        "created_at",
        "latest_snapshot_at",
        "latest_score_date",
        "idea_updated_at",
        "tracking_last_collected_at",
        "tracking_last_checked_at",
    ):
        normalized[key] = _format_date(normalized.get(key))
    normalized["source_types"] = _split_csv(normalized.get("source_types"))
    normalized["idea_statuses"] = _split_csv(normalized.get("idea_statuses"))
    normalized["tracking_status"] = _tracking_status(normalized)
    normalized["tracking_task_id"] = (
        normalized.get("active_tracking_task_id")
        or normalized.get("latest_tracking_task_id")
        or None
    )
    normalized["has_snapshots"] = bool(normalized.get("snapshot_time_count"))
    normalized["has_tracking"] = bool(normalized.get("tracking_task_count"))
    normalized["has_workshop_idea"] = bool(normalized.get("idea_count"))
    return normalized


def _tracking_status(row: dict[str, Any]) -> str:
    if _to_int(row.get("active_count")) > 0:
        return "active"
    if _to_int(row.get("error_count")) > 0:
        return "error"
    if _to_int(row.get("paused_count")) > 0:
        return "paused"
    if _to_int(row.get("completed_count")) > 0:
        return "completed"
    if _to_int(row.get("tracking_task_count")) > 0:
        return "other"
    return "none"


def _split_csv(value: Any) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in str(value).split(","):
        text = item.strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _format_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _to_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    return int(value)


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _normalize_choice(value: str | None, valid: set[str], default: str) -> str:
    text = str(value or default).strip()
    return text if text in valid else default


def _normalize_limit(value: int) -> int:
    return _normalize_positive_int(value, default=100, maximum=500)


def _normalize_offset(value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(0, number)


def _normalize_marketplace(value: str) -> str:
    text = (value or "US").strip().upper()
    return (text or "US")[:16]


def _normalize_positive_int(value: int | None, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def _positive_int(value: int) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError("关键词 ID 必须为正整数")
    return number


def _normalize_ids(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number <= 0 or number in seen:
            continue
        result.append(number)
        seen.add(number)
        if len(result) >= 100:
            break
    return result
