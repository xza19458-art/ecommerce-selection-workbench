"""Dry-run planning for low-frequency product snapshot collection.

This module is intentionally read-only: it only inspects MySQL metadata and
builds a collection plan. It does not open Amazon, save HTML, or write tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote_plus

from database.mysql_client import MySQLClient


AMAZON_SEARCH_BASE = {
    "US": "https://www.amazon.com/s",
    "UK": "https://www.amazon.co.uk/s",
    "DE": "https://www.amazon.de/s",
    "JP": "https://www.amazon.co.jp/s",
    "CA": "https://www.amazon.ca/s",
}


@dataclass(frozen=True)
class SnapshotCollectionPage:
    page_no: int
    url: str
    suggested_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "页码": self.page_no,
            "采集URL": self.url,
            "建议保存文件": self.suggested_file,
        }


@dataclass(frozen=True)
class SnapshotCollectionTask:
    priority: int
    keyword_id: int
    marketplace: str
    keyword: str
    tracked_products: int
    avg_snapshots_per_product: float
    min_snapshots_per_product: int
    max_score: float | None
    last_collected_at: datetime | None
    hours_since_last: float | None
    recommended_pages: int
    reason: str
    save_dir: str
    pages: tuple[SnapshotCollectionPage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "优先级": self.priority,
            "关键词ID": self.keyword_id,
            "站点": self.marketplace,
            "关键词": self.keyword,
            "关联商品数": self.tracked_products,
            "平均快照数": round(self.avg_snapshots_per_product, 2),
            "最少快照数": self.min_snapshots_per_product,
            "最高综合分": round(self.max_score, 2) if self.max_score is not None else None,
            "上次采集时间": self.last_collected_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.last_collected_at
            else None,
            "距上次采集小时": round(self.hours_since_last, 1) if self.hours_since_last is not None else None,
            "建议页数": self.recommended_pages,
            "建议保存目录": self.save_dir,
            "推荐原因": self.reason,
        }


@dataclass(frozen=True)
class SnapshotCollectionPlan:
    generated_at: datetime
    min_interval_hours: int
    target_snapshots: int
    total_candidates: int
    tasks: tuple[SnapshotCollectionTask, ...]

    def to_rows(self, *, include_pages: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in self.tasks:
            base = task.to_dict()
            if include_pages:
                for page in task.pages:
                    row = dict(base)
                    row.update(page.to_dict())
                    rows.append(row)
            else:
                rows.append(base)
        return rows

    def summary(self) -> dict[str, Any]:
        return {
            "生成时间": self.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "候选关键词数": self.total_candidates,
            "入选任务数": len(self.tasks),
            "最短采集间隔小时": self.min_interval_hours,
            "目标快照数": self.target_snapshots,
            "说明": "dry-run 计划，不联网、不写库；联网重采需另行人工触发并遵守被拦即停边界。",
        }


def build_snapshot_collection_plan(
    *,
    max_keywords: int = 3,
    min_interval_hours: int = 72,
    default_pages: int = 1,
    max_pages_per_keyword: int = 2,
    target_snapshots: int = 3,
    marketplace: str | None = None,
    keyword: str | None = None,
    keyword_exact: bool = False,
    seed_keyword: bool = False,
    save_root: str | Path = "html/snapshots",
    now: datetime | None = None,
    client: MySQLClient | None = None,
) -> SnapshotCollectionPlan:
    """Build a read-only dry-run plan for snapshot collection.

    The planner ranks existing keyword pools by snapshot scarcity, elapsed time
    since last keyword collection, and existing product score. It deliberately
    does not perform network access or database writes.

    ``seed_keyword=True`` 用于关键词追踪的冷启动：当传入的 ``keyword`` 还没有商品池
    （候选为空）时，直接按 (keyword, marketplace, pages) 合成一个直采任务，让全新词
    也能采到首个快照。已有商品池的词不受影响（候选非空即走原排序逻辑，不重复播种）。
    """

    generated_at = (now or datetime.now()).replace(second=0, microsecond=0)
    db = client or MySQLClient()
    candidates = _fetch_keyword_candidates(
        db,
        marketplace=marketplace,
        keyword=keyword,
        keyword_exact=keyword_exact,
    )
    eligible = [
        _candidate_to_task(
            row,
            priority=index + 1,
            generated_at=generated_at,
            min_interval_hours=min_interval_hours,
            default_pages=default_pages,
            max_pages_per_keyword=max_pages_per_keyword,
            target_snapshots=target_snapshots,
            save_root=save_root,
        )
        for index, row in enumerate(
            sorted(
                (
                    row
                    for row in candidates
                    if _to_int(row.get("tracked_products")) > 0
                    and _is_due(row.get("last_collected_at"), generated_at, min_interval_hours)
                ),
                key=lambda row: _candidate_sort_key(row, generated_at, target_snapshots),
            )[: _normalize_limit(max_keywords, 1, 50)]
        )
    ]
    if seed_keyword and keyword and (keyword.strip()) and not eligible:
        # 冷启动播种：该词尚无商品池，按 (keyword, marketplace) 直接造一个首采任务。
        eligible.append(
            _candidate_to_task(
                {
                    "keyword_id": 0,
                    "marketplace": (marketplace or "US"),
                    "keyword": keyword.strip(),
                    "tracked_products": 0,
                    "avg_snapshots_per_product": 0,
                    "min_snapshots_per_product": 0,
                    "max_score": None,
                    "last_collected_at": None,
                },
                priority=1,
                generated_at=generated_at,
                min_interval_hours=min_interval_hours,
                default_pages=default_pages,
                max_pages_per_keyword=max_pages_per_keyword,
                target_snapshots=target_snapshots,
                save_root=save_root,
            )
        )
    return SnapshotCollectionPlan(
        generated_at=generated_at,
        min_interval_hours=min_interval_hours,
        target_snapshots=target_snapshots,
        total_candidates=len(candidates),
        tasks=tuple(eligible),
    )


def _fetch_keyword_candidates(
    db: MySQLClient,
    *,
    marketplace: str | None,
    keyword: str | None,
    keyword_exact: bool,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if marketplace:
        clauses.append("k.marketplace = %s")
        params.append(marketplace.strip().upper())
    if keyword:
        if keyword_exact:
            clauses.append("k.keyword = %s")
            params.append(keyword.strip())
        else:
            clauses.append("k.keyword LIKE %s")
            params.append(f"%{keyword.strip()}%")
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                WITH keyword_products AS (
                  SELECT keyword_id, product_id
                  FROM keyword_rank_snapshots
                  UNION
                  SELECT keyword_id, product_id
                  FROM product_scores
                  WHERE keyword_id IS NOT NULL
                ),
                product_snapshot_counts AS (
                  SELECT
                    product_id,
                    COUNT(*) AS snapshot_count,
                    MAX(snapshot_at) AS latest_product_snapshot_at
                  FROM product_snapshots
                  GROUP BY product_id
                ),
                product_stats AS (
                  SELECT
                    kp.keyword_id,
                    COUNT(DISTINCT kp.product_id) AS tracked_products,
                    AVG(COALESCE(psc.snapshot_count, 0)) AS avg_snapshots_per_product,
                    MIN(COALESCE(psc.snapshot_count, 0)) AS min_snapshots_per_product,
                    MAX(psc.latest_product_snapshot_at) AS latest_product_snapshot_at
                  FROM keyword_products kp
                  LEFT JOIN product_snapshot_counts psc
                    ON psc.product_id = kp.product_id
                  GROUP BY kp.keyword_id
                ),
                rank_stats AS (
                  SELECT
                    keyword_id,
                    MAX(snapshot_at) AS last_keyword_snapshot_at
                  FROM keyword_rank_snapshots
                  GROUP BY keyword_id
                ),
                score_stats AS (
                  SELECT
                    keyword_id,
                    MAX(total_score) AS max_score
                  FROM product_scores
                  WHERE keyword_id IS NOT NULL
                  GROUP BY keyword_id
                )
                SELECT
                  k.id AS keyword_id,
                  k.marketplace,
                  k.keyword,
                  COALESCE(ps.tracked_products, 0) AS tracked_products,
                  COALESCE(ps.avg_snapshots_per_product, 0) AS avg_snapshots_per_product,
                  COALESCE(ps.min_snapshots_per_product, 0) AS min_snapshots_per_product,
                  ss.max_score,
                  COALESCE(rs.last_keyword_snapshot_at, ps.latest_product_snapshot_at) AS last_collected_at
                FROM keywords k
                LEFT JOIN product_stats ps ON ps.keyword_id = k.id
                LEFT JOIN rank_stats rs ON rs.keyword_id = k.id
                LEFT JOIN score_stats ss ON ss.keyword_id = k.id
                {where_sql}
                ORDER BY COALESCE(ss.max_score, 0) DESC, COALESCE(ps.tracked_products, 0) DESC, k.id ASC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def _candidate_to_task(
    row: dict[str, Any],
    *,
    priority: int,
    generated_at: datetime,
    min_interval_hours: int,
    default_pages: int,
    max_pages_per_keyword: int,
    target_snapshots: int,
    save_root: str | Path,
) -> SnapshotCollectionTask:
    marketplace = str(row.get("marketplace") or "US").upper()
    keyword = str(row.get("keyword") or "").strip()
    tracked_products = _to_int(row.get("tracked_products"))
    avg_snapshots = _to_float(row.get("avg_snapshots_per_product"))
    min_snapshots = _to_int(row.get("min_snapshots_per_product"))
    max_score = _to_optional_float(row.get("max_score"))
    last_collected_at = row.get("last_collected_at")
    if last_collected_at is not None and not isinstance(last_collected_at, datetime):
        last_collected_at = _parse_datetime(last_collected_at)
    hours_since_last = _hours_since(last_collected_at, generated_at)
    recommended_pages = _recommend_pages(
        tracked_products=tracked_products,
        avg_snapshots=avg_snapshots,
        max_score=max_score,
        default_pages=default_pages,
        max_pages_per_keyword=max_pages_per_keyword,
    )
    save_dir = _build_save_dir(save_root, generated_at, keyword)
    pages = tuple(
        SnapshotCollectionPage(
            page_no=page_no,
            url=_build_search_url(marketplace, keyword, page_no),
            suggested_file=str(Path(save_dir) / _build_file_name(keyword, page_no, generated_at)),
        )
        for page_no in range(1, recommended_pages + 1)
    )
    return SnapshotCollectionTask(
        priority=priority,
        keyword_id=_to_int(row.get("keyword_id")),
        marketplace=marketplace,
        keyword=keyword,
        tracked_products=tracked_products,
        avg_snapshots_per_product=avg_snapshots,
        min_snapshots_per_product=min_snapshots,
        max_score=max_score,
        last_collected_at=last_collected_at,
        hours_since_last=hours_since_last,
        recommended_pages=recommended_pages,
        reason=_build_reason(
            last_collected_at=last_collected_at,
            hours_since_last=hours_since_last,
            min_interval_hours=min_interval_hours,
            avg_snapshots=avg_snapshots,
            target_snapshots=target_snapshots,
            max_score=max_score,
        ),
        save_dir=save_dir,
        pages=pages,
    )


def _candidate_sort_key(row: dict[str, Any], generated_at: datetime, target_snapshots: int) -> tuple[float, float, float]:
    avg_snapshots = _to_float(row.get("avg_snapshots_per_product"))
    max_score = _to_optional_float(row.get("max_score")) or 0.0
    last_collected_at = row.get("last_collected_at")
    if last_collected_at is not None and not isinstance(last_collected_at, datetime):
        last_collected_at = _parse_datetime(last_collected_at)
    hours = _hours_since(last_collected_at, generated_at)
    scarcity = max(0.0, float(target_snapshots) - avg_snapshots)
    return (-scarcity, -(hours or 99999.0), -max_score)


def _is_due(last_collected_at: Any, generated_at: datetime, min_interval_hours: int) -> bool:
    if last_collected_at is None:
        return True
    if not isinstance(last_collected_at, datetime):
        last_collected_at = _parse_datetime(last_collected_at)
    hours = _hours_since(last_collected_at, generated_at)
    return hours is None or hours >= min_interval_hours


def _recommend_pages(
    *,
    tracked_products: int,
    avg_snapshots: float,
    max_score: float | None,
    default_pages: int,
    max_pages_per_keyword: int,
) -> int:
    pages = _normalize_limit(default_pages, 1, max_pages_per_keyword)
    if tracked_products >= 16 and avg_snapshots < 3 and (max_score or 0) >= 70:
        pages += 1
    return _normalize_limit(pages, 1, max_pages_per_keyword)


def _build_reason(
    *,
    last_collected_at: datetime | None,
    hours_since_last: float | None,
    min_interval_hours: int,
    avg_snapshots: float,
    target_snapshots: int,
    max_score: float | None,
) -> str:
    reasons: list[str] = []
    if last_collected_at is None:
        reasons.append("该关键词暂无历史采集时间，适合建立首个周期快照")
    elif hours_since_last is not None:
        reasons.append(f"距上次采集约 {hours_since_last:.1f} 小时，已达到 {min_interval_hours} 小时间隔")
    if avg_snapshots < target_snapshots:
        reasons.append(f"单品平均快照约 {avg_snapshots:.2f} 条，低于趋势可用门槛 {target_snapshots} 条")
    if max_score is not None and max_score >= 70:
        reasons.append(f"已有高分商品（最高 {max_score:.2f}），优先积累趋势样本")
    if not reasons:
        reasons.append("满足低频重采条件，可补充时间序列样本")
    return "；".join(reasons)


def _build_search_url(marketplace: str, keyword: str, page_no: int) -> str:
    base = AMAZON_SEARCH_BASE.get(marketplace.upper(), AMAZON_SEARCH_BASE["US"])
    return f"{base}?k={quote_plus(keyword)}&page={page_no}"


def _build_save_dir(save_root: str | Path, generated_at: datetime, keyword: str) -> str:
    return str(Path(save_root) / generated_at.strftime("%Y%m%d_%H%M") / _slugify(keyword))


def _build_file_name(keyword: str, page_no: int, generated_at: datetime) -> str:
    return f"{_slugify(keyword)}_p{page_no}_{generated_at.strftime('%Y%m%d_%H%M')}.html"


def _slugify(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value.strip(), flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "keyword"


def _hours_since(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds() / 3600)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _normalize_limit(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(number, maximum))
