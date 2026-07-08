"""Keyword workshop: seed keyword expansion, evidence storage, and promotion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import random
import re
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database.mysql_client import MySQLClient


SOURCE_SUGGEST = "amazon_suggest"
SOURCE_TITLE = "title_ngram"
SOURCE_EXISTING = "existing_keyword"
VALID_SOURCES = {SOURCE_SUGGEST, SOURCE_TITLE, SOURCE_EXISTING}

STATUS_CANDIDATE = "candidate"
STATUS_PROMOTED = "promoted"
STATUS_TRACKING = "tracking"
STATUS_IGNORED = "ignored"
VALID_IDEA_STATUSES = {STATUS_CANDIDATE, STATUS_PROMOTED, STATUS_TRACKING, STATUS_IGNORED}

RUNNING = "running"
COMPLETED = "completed"
ERROR = "error"

US_MARKETPLACE_ID = "ATVPDKIKX0DER"
AMAZON_SUGGEST_URL = "https://completion.amazon.com/api/2017/suggestions"
DEFAULT_SUGGEST_TIMEOUT_SECONDS = 8

WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
TITLE_SEGMENT_RE = re.compile(r"[,，;；|/\\()\[\]{}:：.!?！？\"“”]+|\s+[–—-]\s+")
QUANTITY_TOKEN_RE = re.compile(r"^\d+(?:pc|pcs|pack|packs|count|ct|piece|pieces|pk|x)?$")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "your",
}
WEAK_EDGE_TOKENS = {
    "bulk",
    "bag",
    "bags",
    "count",
    "goody",
    "pack",
    "packs",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "random",
    "rising",
    "set",
    "sets",
}
TITLE_CONTEXT_TAIL_TOKENS = {
    "anxiety",
    "fidget",
    "sensory",
    "squeeze",
    "stress",
}
TITLE_BROAD_INTENT_START_TOKENS = {
    "ball",
    "balls",
    "fidget",
    "sensory",
    "squeeze",
    "toy",
    "toys",
}
TITLE_BROAD_CONTEXT_TOKENS = {
    "birthday",
    "classroom",
    "favor",
    "favors",
    "gift",
    "gifts",
    "party",
}
TITLE_GENERIC_SEED_TOKENS = {
    "item",
    "items",
    "product",
    "products",
    "toy",
    "toys",
}
SQUISHY_TOKENS = {"squishies", "squishy"}


@dataclass(frozen=True)
class WorkshopRunResult:
    run_id: int
    marketplace: str
    seed_keywords: list[str]
    sources: dict[str, bool]
    total_found: int
    total_saved: int
    warnings: list[str]
    ideas: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "marketplace": self.marketplace,
            "seed_keywords": self.seed_keywords,
            "sources": self.sources,
            "total_found": self.total_found,
            "total_saved": self.total_saved,
            "warnings": self.warnings,
            "ideas": self.ideas,
        }


def ensure_keyword_workshop_schema(*, client: MySQLClient | None = None) -> None:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)


def normalize_keyword(value: str | None) -> str:
    text = str(value or "").lower()
    tokens = WORD_RE.findall(text)
    return " ".join(tokens)


def normalize_seed_keywords(seed_keywords: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if seed_keywords is None:
        return []
    if isinstance(seed_keywords, str):
        raw = re.split(r"[\n,，;；]+", seed_keywords)
    else:
        raw = list(seed_keywords)
    seen: set[str] = set()
    seeds: list[str] = []
    for item in raw:
        normalized = normalize_keyword(item)
        if normalized and normalized not in seen and is_valid_keyword(normalized):
            seen.add(normalized)
            seeds.append(normalized)
    return seeds[:20]


def is_valid_keyword(keyword: str | None) -> bool:
    raw = str(keyword or "").lower()
    if "http://" in raw or "https://" in raw or "www." in raw:
        return False
    normalized = normalize_keyword(keyword)
    if len(normalized) < 3 or len(normalized) > 90:
        return False
    tokens = normalized.split()
    if not tokens or len(tokens) > 7:
        return False
    if all(token.isdigit() for token in tokens):
        return False
    if any(len(token) > 32 for token in tokens):
        return False
    if len(tokens) == 1 and tokens[0] in STOPWORDS:
        return False
    if _has_repeated_meaningful_token(tokens):
        return False
    return True


def build_expansion_queries(seed_keyword: str, *, expand_alpha_num: bool = True) -> list[str]:
    base = normalize_keyword(seed_keyword)
    if not base:
        return []
    queries = [base]
    if expand_alpha_num:
        queries.extend(f"{base} {suffix}" for suffix in "abcdefghijklmnopqrstuvwxyz0123456789")
    return _unique_keep_order(queries)


def parse_suggestions(payload: Any) -> list[str]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        payload = json.loads(payload)

    raw_items: list[Any] = []
    if isinstance(payload, dict):
        for key in ("suggestions", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_items.extend(value)
    elif isinstance(payload, list):
        raw_items.extend(payload)

    suggestions: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            suggestions.append(item)
        elif isinstance(item, dict):
            for key in ("value", "suggestion", "keyword", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    suggestions.append(value)
                    break
    return _unique_keep_order([normalize_keyword(item) for item in suggestions if is_valid_keyword(item)])


def make_session_id() -> str:
    return f"{random.randint(100, 999)}-{random.randint(1000000, 9999999)}-{random.randint(1000000, 9999999)}"


def fetch_amazon_suggestions(
    prefix: str,
    *,
    marketplace: str = "US",
    timeout_seconds: int = DEFAULT_SUGGEST_TIMEOUT_SECONDS,
) -> list[str]:
    marketplace = _normalize_marketplace(marketplace)
    mid = US_MARKETPLACE_ID if marketplace == "US" else US_MARKETPLACE_ID
    params = {
        "session-id": make_session_id(),
        "request-id": make_session_id(),
        "page-type": "Gateway",
        "lop": "en_US",
        "site-variant": "desktop",
        "client-info": "amazon-search-ui",
        "mid": mid,
        "alias": "aps",
        "b2b": "0",
        "fresh": "0",
        "ks": "65",
        "prefix": prefix,
        "event": "onKeyPress",
        "limit": "11",
        "fb": "1",
        "suggestion-type": "KEYWORD",
    }
    url = f"{AMAZON_SUGGEST_URL}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return parse_suggestions(response.read())


def extract_title_ngrams(
    titles: list[str],
    seed_keywords: list[str],
    *,
    min_n: int = 2,
    max_n: int = 4,
    max_candidates: int = 120,
) -> list[dict[str, Any]]:
    seed_tokens = {token for seed in seed_keywords for token in normalize_keyword(seed).split()}
    required_seed_tokens = seed_tokens - TITLE_GENERIC_SEED_TOKENS
    if not seed_tokens:
        return []
    buckets: dict[str, dict[str, Any]] = {}
    for title in titles:
        for tokens in _title_token_segments(title):
            for size in range(min_n, max_n + 1):
                if len(tokens) < size:
                    continue
                for idx in range(0, len(tokens) - size + 1):
                    phrase_tokens = tokens[idx : idx + size]
                    if not _is_title_ngram_candidate(phrase_tokens, seed_tokens, required_seed_tokens):
                        continue
                    phrase = " ".join(phrase_tokens)
                    bucket = buckets.setdefault(
                        phrase,
                        {"keyword": phrase, "count": 0, "examples": [], "matched_seeds": set()},
                    )
                    bucket["count"] += 1
                    if len(bucket["examples"]) < 5:
                        bucket["examples"].append(str(title)[:160])
                    bucket["matched_seeds"].update(seed for seed in seed_keywords if set(seed.split()) & set(phrase_tokens))

    rows = sorted(buckets.values(), key=lambda item: (-int(item["count"]), item["keyword"]))
    normalized: list[dict[str, Any]] = []
    for row in rows[:max_candidates]:
        normalized.append(
            {
                "keyword": row["keyword"],
                "count": int(row["count"]),
                "examples": row["examples"],
                "matched_seeds": sorted(row["matched_seeds"]),
            }
        )
    return normalized


def _title_token_segments(title: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for segment in TITLE_SEGMENT_RE.split(str(title or "").lower()):
        tokens = WORD_RE.findall(segment)
        if tokens:
            segments.append(tokens)
    return segments


def _is_title_ngram_candidate(
    phrase_tokens: list[str],
    seed_tokens: set[str],
    required_seed_tokens: set[str] | None = None,
) -> bool:
    token_set = set(phrase_tokens)
    if not (token_set & seed_tokens):
        return False
    if required_seed_tokens and not (token_set & required_seed_tokens):
        return False
    if _has_quantity_token(phrase_tokens):
        return False
    if phrase_tokens[0] in STOPWORDS or phrase_tokens[-1] in STOPWORDS:
        return False
    if phrase_tokens[0] in WEAK_EDGE_TOKENS or phrase_tokens[-1] in WEAK_EDGE_TOKENS:
        return False
    if phrase_tokens[0] == "relief":
        return False
    if phrase_tokens[-1] in TITLE_CONTEXT_TAIL_TOKENS:
        return False
    if _looks_like_broad_title_intent(phrase_tokens):
        return False
    if _has_squishy_stress_order_issue(phrase_tokens):
        return False
    if _has_fidget_toys_trailing_modifier_issue(phrase_tokens):
        return False
    if _has_repeated_meaningful_token(phrase_tokens):
        return False
    return is_valid_keyword(" ".join(phrase_tokens))


def _has_quantity_token(tokens: list[str]) -> bool:
    return any(QUANTITY_TOKEN_RE.match(token) for token in tokens)


def _looks_like_broad_title_intent(tokens: list[str]) -> bool:
    if tokens[0] in {"ball", "balls"} and "for" in tokens:
        return True
    if tokens[0] in {"ball", "balls"} and any(
        token in {"fidget", "sensory", "squeeze", "toy", "toys"} for token in tokens[1:]
    ):
        return True
    if tokens[0] in {"ball", "balls"} and any(token in TITLE_BROAD_CONTEXT_TOKENS for token in tokens[1:]):
        return True
    for idx in range(0, len(tokens) - 1):
        if tokens[idx] == "for" and tokens[idx + 1] in TITLE_CONTEXT_TAIL_TOKENS:
            return tokens[0] in TITLE_BROAD_INTENT_START_TOKENS or len(tokens) <= 3
    return False


def _has_squishy_stress_order_issue(tokens: list[str]) -> bool:
    for idx in range(0, len(tokens) - 1):
        if tokens[idx] in SQUISHY_TOKENS and tokens[idx + 1] == "stress" and idx > 0:
            return True
        if tokens[idx] == "cube" and tokens[idx + 1] in SQUISHY_TOKENS and "stress" in tokens:
            return True
    return False


def _has_fidget_toys_trailing_modifier_issue(tokens: list[str]) -> bool:
    if len(tokens) < 3 or tokens[0:2] != ["fidget", "toys"]:
        return False
    return tokens[-1] in SQUISHY_TOKENS or tokens[-1] in {"mochi"}


def _has_repeated_meaningful_token(tokens: list[str]) -> bool:
    meaningful = [
        token
        for token in tokens
        if len(token) > 2 and not token.isdigit() and token not in STOPWORDS and token not in WEAK_EDGE_TOKENS
    ]
    return len(meaningful) != len(set(meaningful))


def score_keyword_idea(keyword: str, source_types: set[str] | list[str], evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_keyword(keyword)
    tokens = normalized.split()
    sources = set(source_types)
    source_evidence = evidence.get("sources") or {}

    suggest = source_evidence.get(SOURCE_SUGGEST) or {}
    title = source_evidence.get(SOURCE_TITLE) or {}
    existing = source_evidence.get(SOURCE_EXISTING) or {}

    best_rank = _optional_int(suggest.get("best_rank"))
    suggest_demand = 0.0 if best_rank is None else max(45.0, 96.0 - min(best_rank, 12) * 4.0)
    title_count = _optional_int(title.get("count")) or 0
    title_demand = min(88.0, 35.0 + title_count * 8.0) if title_count else 0.0
    product_count = _optional_int(existing.get("product_count")) or 0
    existing_demand = min(90.0, 35.0 + product_count * 4.0) if product_count else 0.0
    demand_score = max(suggest_demand, title_demand, existing_demand)

    token_count = len(tokens)
    if token_count == 1:
        long_tail_score = 38.0
    elif token_count == 2:
        long_tail_score = 72.0
    elif token_count in (3, 4):
        long_tail_score = 90.0
    elif token_count <= 6:
        long_tail_score = 72.0
    else:
        long_tail_score = 45.0

    competition_score = long_tail_score
    if product_count:
        if product_count <= 3:
            competition_score = min(100.0, competition_score + 8.0)
        elif product_count >= 80:
            competition_score = max(35.0, competition_score - 15.0)

    avg_total_score = _optional_float(existing.get("avg_total_score"))
    existing_opportunity = avg_total_score or (55.0 if product_count else 0.0)

    idea_score = demand_score * 0.40 + competition_score * 0.25 + long_tail_score * 0.20 + existing_opportunity * 0.15
    idea_score = round(max(0.0, min(100.0, idea_score)), 2)

    confidence = 10.0
    if SOURCE_SUGGEST in sources:
        confidence += 26.0
    if SOURCE_TITLE in sources:
        confidence += min(24.0, 10.0 + title_count * 2.0)
    if SOURCE_EXISTING in sources:
        confidence += 24.0
    confidence += min(16.0, (_optional_int(evidence.get("occurrence_count")) or 1) * 2.0)
    confidence = round(max(0.0, min(100.0, confidence)), 2)

    return {
        "idea_score": idea_score,
        "confidence_score": confidence,
        "recommendation_level": _recommendation_level(idea_score, confidence),
        "reason": _build_scoring_reason(
            token_count=token_count,
            best_rank=best_rank,
            title_count=title_count,
            product_count=product_count,
            avg_total_score=avg_total_score,
            idea_score=idea_score,
            confidence_score=confidence,
        ),
    }


def run_keyword_workshop(
    *,
    seed_keywords: list[str] | tuple[str, ...] | str,
    marketplace: str = "US",
    use_suggest: bool = True,
    use_titles: bool = True,
    expand_suggest: bool = True,
    max_suggest_queries_per_seed: int = 16,
    max_title_rows: int = 300,
    suggest_delay_seconds: float = 0.15,
    client: MySQLClient | None = None,
    suggest_fetcher: Callable[..., list[str]] | None = None,
) -> WorkshopRunResult:
    seeds = normalize_seed_keywords(seed_keywords)
    if not seeds:
        raise ValueError("请至少填写一个有效种子词")
    marketplace = _normalize_marketplace(marketplace)
    sources = {SOURCE_SUGGEST: bool(use_suggest), SOURCE_TITLE: bool(use_titles)}
    db = client or MySQLClient()
    fetcher = suggest_fetcher or fetch_amazon_suggestions
    run_id = _create_run(db, marketplace=marketplace, seeds=seeds, sources=sources, expand_suggest=expand_suggest)

    candidates: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    total_found = 0
    total_saved = 0
    try:
        if use_suggest:
            found, suggest_warnings = _collect_suggest_candidates(
                candidates,
                seeds=seeds,
                marketplace=marketplace,
                expand_suggest=expand_suggest,
                max_queries_per_seed=max_suggest_queries_per_seed,
                delay_seconds=suggest_delay_seconds,
                fetcher=fetcher,
            )
            total_found += found
            warnings.extend(suggest_warnings)
        if use_titles:
            found = _collect_title_candidates(
                candidates,
                db,
                seeds=seeds,
                marketplace=marketplace,
                limit=max_title_rows,
            )
            total_found += found

        with db.connect() as conn:
            with conn.cursor() as cursor:
                db.ensure_keyword_workshop_tables(cursor)
                _attach_existing_keyword_signals(cursor, marketplace, candidates)
                total_saved = _save_candidates(cursor, run_id=run_id, marketplace=marketplace, candidates=candidates)
                _finish_run(
                    cursor,
                    run_id=run_id,
                    status=COMPLETED,
                    total_found=total_found,
                    total_saved=total_saved,
                    warnings=warnings,
                )
        ideas = fetch_keyword_ideas_page(
            limit=50,
            offset=0,
            marketplace=marketplace,
            run_id=run_id,
            client=db,
        )["rows"]
        return WorkshopRunResult(
            run_id=run_id,
            marketplace=marketplace,
            seed_keywords=seeds,
            sources=sources,
            total_found=total_found,
            total_saved=total_saved,
            warnings=warnings,
            ideas=ideas,
        )
    except Exception as exc:
        _mark_run_error(db, run_id, str(exc), total_found=total_found, total_saved=total_saved)
        raise


def fetch_keyword_ideas_page(
    *,
    limit: int = 100,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    source: str | None = None,
    run_id: int | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    where_sql, params = _build_idea_filters(
        marketplace=marketplace,
        status=status,
        keyword=keyword,
        source=source,
        run_id=run_id,
    )
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            cursor.execute(f"SELECT COUNT(*) AS total FROM keyword_ideas {where_sql}", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                SELECT *
                FROM keyword_ideas
                {where_sql}
                ORDER BY idea_score DESC, confidence_score DESC, updated_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_normalize_idea_row(row) for row in cursor.fetchall()]
    return {"rows": rows, "total": total, "limit": limit_value, "offset": offset_value}


def fetch_keyword_idea_runs_page(
    *,
    limit: int = 20,
    offset: int = 0,
    marketplace: str = "US",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM keyword_idea_runs
                WHERE marketplace = %s
                """,
                (marketplace,),
            )
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                """
                SELECT
                  r.*,
                  COALESCE(s.idea_count, 0) AS idea_count,
                  COALESCE(s.candidate_count, 0) AS candidate_count,
                  COALESCE(s.ignored_count, 0) AS ignored_count,
                  COALESCE(s.promoted_count, 0) AS promoted_count,
                  COALESCE(s.tracking_count, 0) AS tracking_count
                FROM keyword_idea_runs r
                LEFT JOIN (
                  SELECT
                    last_run_id,
                    COUNT(*) AS idea_count,
                    SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END) AS candidate_count,
                    SUM(CASE WHEN status = 'ignored' THEN 1 ELSE 0 END) AS ignored_count,
                    SUM(CASE WHEN status = 'promoted' THEN 1 ELSE 0 END) AS promoted_count,
                    SUM(CASE WHEN status = 'tracking' THEN 1 ELSE 0 END) AS tracking_count
                  FROM keyword_ideas
                  WHERE marketplace = %s
                    AND last_run_id IS NOT NULL
                  GROUP BY last_run_id
                ) s ON s.last_run_id = r.id
                WHERE r.marketplace = %s
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT %s OFFSET %s
                """,
                (marketplace, marketplace, limit_value, offset_value),
            )
            rows = [_normalize_run_row(row) for row in cursor.fetchall()]
    return {"rows": rows, "total": total, "limit": limit_value, "offset": offset_value}


def promote_keyword_ideas(
    idea_ids: list[int],
    *,
    marketplace: str = "US",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    ids = _normalize_ids(idea_ids)
    if not ids:
        raise ValueError("请选择要加入关键词库的候选词")
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    updated = 0
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            rows = _fetch_ideas_by_ids(cursor, ids, marketplace=marketplace)
            for row in rows:
                keyword_id = db.upsert_keyword(cursor, row["keyword"], marketplace)
                next_status = STATUS_TRACKING if row.get("status") == STATUS_TRACKING else STATUS_PROMOTED
                cursor.execute(
                    """
                    UPDATE keyword_ideas
                    SET status = %s, promoted_keyword_id = %s
                    WHERE id = %s
                    """,
                    (next_status, keyword_id, row["id"]),
                )
                updated += 1
    return {"updated": updated, "ids": ids}


def create_tracking_from_keyword_ideas(
    idea_ids: list[int],
    *,
    marketplace: str = "US",
    target_snapshots: int = 3,
    pages_per_keyword: int | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    ids = _normalize_ids(idea_ids)
    if not ids:
        raise ValueError("请选择要创建追踪任务的候选词")
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            rows = _fetch_ideas_by_ids(cursor, ids, marketplace=marketplace)

    tasks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        keyword_id = _promote_single_keyword(db, marketplace, row["keyword"])
        task, warning = _create_or_fetch_tracking_task(
            marketplace=marketplace,
            keyword=row["keyword"],
            target_snapshots=target_snapshots,
            pages_per_keyword=pages_per_keyword,
        )
        if warning:
            warnings.append(warning)
        tasks.append(task)
        with db.connect() as conn:
            with conn.cursor() as cursor:
                db.ensure_keyword_workshop_tables(cursor)
                cursor.execute(
                    """
                    UPDATE keyword_ideas
                    SET status = %s, promoted_keyword_id = %s, tracking_task_id = %s
                    WHERE id = %s
                    """,
                    (STATUS_TRACKING, keyword_id, task.get("id"), row["id"]),
                )
    return {"created_or_existing": len(tasks), "tasks": tasks, "warnings": warnings}


def update_keyword_idea_status(
    idea_ids: list[int],
    status: str,
    *,
    marketplace: str = "US",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    ids = _normalize_ids(idea_ids)
    if not ids:
        raise ValueError("请选择要更新的候选词")
    if status not in VALID_IDEA_STATUSES:
        raise ValueError("候选词状态只能是 candidate/promoted/tracking/ignored")
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    placeholders = ", ".join(["%s"] * len(ids))
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            cursor.execute(
                f"""
                UPDATE keyword_ideas
                SET status = %s
                WHERE marketplace = %s
                  AND id IN ({placeholders})
                """,
                [status, marketplace] + ids,
            )
            updated = int(cursor.rowcount or 0)
    return {"updated": updated, "ids": ids, "status": status}


def update_keyword_idea_status_by_run(
    run_id: int,
    status: str,
    *,
    marketplace: str = "US",
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    try:
        run_id_value = int(run_id)
    except (TypeError, ValueError):
        run_id_value = 0
    if run_id_value <= 0:
        raise ValueError("运行 ID 不合法")
    source_status = _source_status_for_run_transition(status)
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            cursor.execute(
                """
                UPDATE keyword_ideas
                SET status = %s
                WHERE marketplace = %s
                  AND last_run_id = %s
                  AND status = %s
                """,
                (status, marketplace, run_id_value, source_status),
            )
            updated = int(cursor.rowcount or 0)
    return {
        "updated": updated,
        "run_id": run_id_value,
        "status": status,
        "from_status": source_status,
    }


def _create_run(
    db: MySQLClient,
    *,
    marketplace: str,
    seeds: list[str],
    sources: dict[str, bool],
    expand_suggest: bool,
) -> int:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            cursor.execute(
                """
                INSERT INTO keyword_idea_runs (
                  marketplace, seed_keywords_json, sources_json, expansion_mode, status
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    marketplace,
                    _json_dumps(seeds),
                    _json_dumps(sources),
                    "suggest_alpha_num" if expand_suggest else "suggest_base",
                    RUNNING,
                ),
            )
            return int(cursor.lastrowid)


def _collect_suggest_candidates(
    candidates: dict[str, dict[str, Any]],
    *,
    seeds: list[str],
    marketplace: str,
    expand_suggest: bool,
    max_queries_per_seed: int,
    delay_seconds: float,
    fetcher: Callable[..., list[str]],
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    found = 0
    max_queries = _clamp_int(max_queries_per_seed, default=16, minimum=1, maximum=37)
    for seed in seeds:
        queries = build_expansion_queries(seed, expand_alpha_num=expand_suggest)[:max_queries]
        for query in queries:
            try:
                suggestions = fetcher(query, marketplace=marketplace)
            except Exception as exc:  # noqa: BLE001 - source failure should not block title extraction.
                warnings.append(f"Amazon Suggest 获取失败：{query}（{exc}）")
                continue
            for rank, suggestion in enumerate(suggestions, start=1):
                found += 1
                _merge_candidate(
                    candidates,
                    suggestion,
                    source=SOURCE_SUGGEST,
                    seed=seed,
                    detail={"query": query, "rank": rank},
                )
            if delay_seconds > 0 and fetcher is fetch_amazon_suggestions:
                time.sleep(delay_seconds)
    return found, warnings[:20]


def _collect_title_candidates(
    candidates: dict[str, dict[str, Any]],
    db: MySQLClient,
    *,
    seeds: list[str],
    marketplace: str,
    limit: int,
) -> int:
    titles: list[str] = []
    row_limit = _clamp_int(limit, default=300, minimum=20, maximum=2000)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_workshop_tables(cursor)
            for seed in seeds:
                cursor.execute(
                    """
                    SELECT title
                    FROM products
                    WHERE marketplace = %s
                      AND LOWER(title) LIKE %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (marketplace, f"%{seed}%", row_limit),
                )
                titles.extend(str(row.get("title") or "") for row in cursor.fetchall())
    title_rows = extract_title_ngrams(_unique_keep_order(titles), seeds, max_candidates=160)
    for row in title_rows:
        seed = (row.get("matched_seeds") or seeds)[0]
        _merge_candidate(
            candidates,
            row["keyword"],
            source=SOURCE_TITLE,
            seed=seed,
            detail={
                "count": row["count"],
                "examples": row["examples"],
                "matched_seeds": row.get("matched_seeds") or [],
            },
        )
    return sum(int(row["count"]) for row in title_rows)


def _merge_candidate(
    candidates: dict[str, dict[str, Any]],
    keyword: str,
    *,
    source: str,
    seed: str,
    detail: dict[str, Any],
) -> None:
    normalized = normalize_keyword(keyword)
    if source not in VALID_SOURCES or not is_valid_keyword(normalized):
        return
    item = candidates.setdefault(
        normalized,
        {
            "keyword": normalized,
            "normalized_keyword": normalized,
            "source_types": set(),
            "seed_keywords": set(),
            "evidence": {"sources": {}, "seeds": []},
            "occurrence_count": 0,
        },
    )
    item["source_types"].add(source)
    item["seed_keywords"].add(seed)
    item["occurrence_count"] += max(1, int(detail.get("count") or 1))
    sources = item["evidence"].setdefault("sources", {})
    source_info = sources.setdefault(source, {})
    if source == SOURCE_SUGGEST:
        rank = _optional_int(detail.get("rank"))
        if rank is not None:
            current = _optional_int(source_info.get("best_rank"))
            source_info["best_rank"] = rank if current is None else min(current, rank)
        _append_unique(source_info, "queries", detail.get("query"), max_items=20)
    elif source == SOURCE_TITLE:
        source_info["count"] = int(source_info.get("count") or 0) + int(detail.get("count") or 1)
        for example in detail.get("examples") or []:
            _append_unique(source_info, "examples", example, max_items=10)
    item["evidence"]["seeds"] = sorted(item["seed_keywords"])
    item["evidence"]["occurrence_count"] = item["occurrence_count"]


def _attach_existing_keyword_signals(
    cursor: Any,
    marketplace: str,
    candidates: dict[str, dict[str, Any]],
) -> None:
    if not candidates:
        return
    keys = list(candidates.keys())
    for chunk in _chunks(keys, 100):
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT
              LOWER(k.keyword) AS normalized_keyword,
              MAX(k.id) AS keyword_id,
              COUNT(DISTINCT krs.product_id) AS product_count,
              COUNT(DISTINCT krs.snapshot_at) AS snapshot_count,
              AVG(ps.total_score) AS avg_total_score
            FROM keywords k
            LEFT JOIN keyword_rank_snapshots krs ON krs.keyword_id = k.id
            LEFT JOIN product_scores ps ON ps.keyword_id = k.id
            WHERE k.marketplace = %s
              AND LOWER(k.keyword) IN ({placeholders})
            GROUP BY LOWER(k.keyword)
            """,
            [marketplace] + chunk,
        )
        for row in cursor.fetchall():
            normalized = normalize_keyword(row.get("normalized_keyword"))
            item = candidates.get(normalized)
            if not item:
                continue
            item["source_types"].add(SOURCE_EXISTING)
            item["evidence"].setdefault("sources", {})[SOURCE_EXISTING] = {
                "keyword_id": _optional_int(row.get("keyword_id")),
                "product_count": _optional_int(row.get("product_count")) or 0,
                "snapshot_count": _optional_int(row.get("snapshot_count")) or 0,
                "avg_total_score": _optional_float(row.get("avg_total_score")),
            }


def _save_candidates(
    cursor: Any,
    *,
    run_id: int,
    marketplace: str,
    candidates: dict[str, dict[str, Any]],
) -> int:
    saved = 0
    for normalized, item in sorted(candidates.items()):
        existing = _fetch_existing_idea(cursor, marketplace, normalized)
        merged = _merge_existing_candidate(existing, item) if existing else item
        source_types = set(merged["source_types"])
        seed_keywords = sorted(merged["seed_keywords"])
        evidence = merged["evidence"]
        evidence["seeds"] = seed_keywords
        evidence["occurrence_count"] = int(merged["occurrence_count"])
        scoring = score_keyword_idea(merged["keyword"], source_types, evidence)
        if existing:
            cursor.execute(
                """
                UPDATE keyword_ideas
                SET keyword = %s,
                    source_types = %s,
                    seed_keywords_json = %s,
                    evidence_json = %s,
                    idea_score = %s,
                    confidence_score = %s,
                    recommendation_level = %s,
                    reason = %s,
                    occurrence_count = %s,
                    last_run_id = %s
                WHERE id = %s
                """,
                (
                    merged["keyword"],
                    ",".join(sorted(source_types)),
                    _json_dumps(seed_keywords),
                    _json_dumps(evidence),
                    scoring["idea_score"],
                    scoring["confidence_score"],
                    scoring["recommendation_level"],
                    scoring["reason"],
                    int(merged["occurrence_count"]),
                    run_id,
                    existing["id"],
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO keyword_ideas (
                  marketplace, keyword, normalized_keyword, status, source_types,
                  seed_keywords_json, evidence_json, idea_score, confidence_score,
                  recommendation_level, reason, occurrence_count, last_run_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    marketplace,
                    item["keyword"],
                    normalized,
                    STATUS_CANDIDATE,
                    ",".join(sorted(source_types)),
                    _json_dumps(seed_keywords),
                    _json_dumps(evidence),
                    scoring["idea_score"],
                    scoring["confidence_score"],
                    scoring["recommendation_level"],
                    scoring["reason"],
                    int(item["occurrence_count"]),
                    run_id,
                ),
            )
        saved += 1
    return saved


def _fetch_existing_idea(cursor: Any, marketplace: str, normalized_keyword: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT *
        FROM keyword_ideas
        WHERE marketplace = %s
          AND normalized_keyword = %s
        FOR UPDATE
        """,
        (marketplace, normalized_keyword),
    )
    return cursor.fetchone()


def _merge_existing_candidate(existing: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    evidence = _loads_json(existing.get("evidence_json"), default={"sources": {}, "seeds": []})
    merged_evidence = _merge_evidence(evidence, item["evidence"])
    merged = {
        "keyword": item["keyword"],
        "normalized_keyword": item["normalized_keyword"],
        "source_types": set(str(existing.get("source_types") or "").split(",")) | set(item["source_types"]),
        "seed_keywords": set(_loads_json(existing.get("seed_keywords_json"), default=[])) | set(item["seed_keywords"]),
        "evidence": merged_evidence,
        "occurrence_count": _evidence_strength_count(merged_evidence, fallback=item.get("occurrence_count")),
    }
    merged["source_types"].discard("")
    merged["evidence"]["occurrence_count"] = merged["occurrence_count"]
    return merged


def _merge_evidence(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing or {})
    sources = dict(result.get("sources") or {})
    for source, detail in (new.get("sources") or {}).items():
        current = dict(sources.get(source) or {})
        if source == SOURCE_SUGGEST:
            new_rank = _optional_int(detail.get("best_rank"))
            old_rank = _optional_int(current.get("best_rank"))
            if new_rank is not None:
                current["best_rank"] = new_rank if old_rank is None else min(old_rank, new_rank)
            for query in detail.get("queries") or []:
                _append_unique(current, "queries", query, max_items=20)
        elif source == SOURCE_TITLE:
            current["count"] = int(detail.get("count") or 0)
            for example in detail.get("examples") or []:
                _append_unique(current, "examples", example, max_items=10)
        else:
            current.update(detail)
        sources[source] = current
    result["sources"] = sources
    return result


def _evidence_strength_count(evidence: dict[str, Any], *, fallback: Any = None) -> int:
    sources = evidence.get("sources") or {}
    count = 0
    title = sources.get(SOURCE_TITLE) or {}
    count += max(0, _optional_int(title.get("count")) or 0)
    suggest = sources.get(SOURCE_SUGGEST) or {}
    suggest_queries = [item for item in (suggest.get("queries") or []) if item]
    if suggest_queries or suggest.get("best_rank") is not None:
        count += max(1, len(suggest_queries))
    if sources.get(SOURCE_EXISTING):
        count += 1
    fallback_count = _optional_int(fallback) or 0
    return max(1, count or fallback_count)


def _build_idea_filters(
    *,
    marketplace: str,
    status: str | None,
    keyword: str | None,
    source: str | None,
    run_id: int | None,
) -> tuple[str, list[Any]]:
    clauses = ["marketplace = %s"]
    params: list[Any] = [marketplace]
    if status and status != "all":
        if status not in VALID_IDEA_STATUSES:
            raise ValueError("候选词状态只能是 candidate/promoted/tracking/ignored")
        clauses.append("status = %s")
        params.append(status)
    normalized = normalize_keyword(keyword)
    if normalized:
        clauses.append("normalized_keyword LIKE %s")
        params.append(f"%{normalized}%")
    if source and source != "all":
        if source not in VALID_SOURCES:
            raise ValueError("未知关键词创意来源")
        clauses.append("source_types LIKE %s")
        params.append(f"%{source}%")
    if run_id is not None:
        clauses.append("last_run_id = %s")
        params.append(int(run_id))
    return "WHERE " + " AND ".join(clauses), params


def _source_status_for_run_transition(target_status: str) -> str:
    if target_status == STATUS_IGNORED:
        return STATUS_CANDIDATE
    if target_status == STATUS_CANDIDATE:
        return STATUS_IGNORED
    raise ValueError("按运行批次只能在 candidate 与 ignored 之间切换，不影响已入库或追踪中的候选")


def _fetch_ideas_by_ids(cursor: Any, ids: list[int], *, marketplace: str) -> list[dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"""
        SELECT *
        FROM keyword_ideas
        WHERE marketplace = %s
          AND id IN ({placeholders})
        """,
        [marketplace] + ids,
    )
    rows = cursor.fetchall()
    if len(rows) != len(ids):
        found = {int(row["id"]) for row in rows}
        missing = [str(item) for item in ids if item not in found]
        raise ValueError(f"候选词不存在或不属于当前站点：{', '.join(missing)}")
    return rows


def _promote_single_keyword(db: MySQLClient, marketplace: str, keyword: str) -> int:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            return int(db.upsert_keyword(cursor, keyword, marketplace) or 0)


def _create_or_fetch_tracking_task(
    *,
    marketplace: str,
    keyword: str,
    target_snapshots: int,
    pages_per_keyword: int | None,
) -> tuple[dict[str, Any], str | None]:
    from services.keyword_tracking import create_tracking_task, list_tracking_tasks

    try:
        task = create_tracking_task(
            marketplace=marketplace,
            keyword=keyword,
            target_snapshots=target_snapshots,
            pages_per_keyword=pages_per_keyword,
        )
        return task.to_dict(), None
    except Exception as exc:  # noqa: BLE001 - duplicate active task should be treated as existing.
        tasks = list_tracking_tasks(status="active", marketplace=marketplace, keyword=keyword, limit=200)
        normalized = normalize_keyword(keyword)
        for task in tasks:
            if normalize_keyword(task.keyword) == normalized:
                return task.to_dict(), f"关键词「{keyword}」已有 active 追踪任务，已复用。"
        raise exc


def _finish_run(
    cursor: Any,
    *,
    run_id: int,
    status: str,
    total_found: int,
    total_saved: int,
    warnings: list[str],
) -> None:
    cursor.execute(
        """
        UPDATE keyword_idea_runs
        SET status = %s,
            total_found = %s,
            total_saved = %s,
            warning_message = %s,
            finished_at = %s
        WHERE id = %s
        """,
        (status, total_found, total_saved, "\n".join(warnings[:20]) or None, datetime.now(), run_id),
    )


def _mark_run_error(db: MySQLClient, run_id: int, message: str, *, total_found: int, total_saved: int) -> None:
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                db.ensure_keyword_workshop_tables(cursor)
                _finish_run(
                    cursor,
                    run_id=run_id,
                    status=ERROR,
                    total_found=total_found,
                    total_saved=total_saved,
                    warnings=[message],
                )
    except Exception:
        return


def _normalize_idea_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("idea_score", "confidence_score"):
        normalized[key] = _optional_float(normalized.get(key))
    for key in ("id", "occurrence_count", "last_run_id", "promoted_keyword_id", "tracking_task_id"):
        normalized[key] = _optional_int(normalized.get(key))
    normalized["source_types"] = [item for item in str(normalized.get("source_types") or "").split(",") if item]
    normalized["seed_keywords"] = _loads_json(normalized.pop("seed_keywords_json", None), default=[])
    normalized["evidence"] = _loads_json(normalized.pop("evidence_json", None), default={})
    for key in ("created_at", "updated_at"):
        if normalized.get(key) is not None:
            normalized[key] = str(normalized[key])
    return normalized


def _normalize_run_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in (
        "id",
        "total_found",
        "total_saved",
        "idea_count",
        "candidate_count",
        "ignored_count",
        "promoted_count",
        "tracking_count",
    ):
        normalized[key] = _optional_int(normalized.get(key)) or 0
    normalized["seed_keywords"] = _loads_json(normalized.pop("seed_keywords_json", None), default=[])
    normalized["sources"] = _loads_json(normalized.pop("sources_json", None), default={})
    for key in ("created_at", "finished_at"):
        if normalized.get(key) is not None:
            normalized[key] = str(normalized[key])
    return normalized


def _recommendation_level(idea_score: float, confidence_score: float) -> str:
    if idea_score >= 75 and confidence_score >= 55:
        return "优先验证"
    if idea_score >= 60:
        return "可观察"
    if idea_score >= 45:
        return "仅作灵感"
    return "暂不建议"


def _build_scoring_reason(
    *,
    token_count: int,
    best_rank: int | None,
    title_count: int,
    product_count: int,
    avg_total_score: float | None,
    idea_score: float,
    confidence_score: float,
) -> str:
    parts: list[str] = []
    if best_rank is not None:
        parts.append(f"Amazon 联想中最高第 {best_rank} 位，说明有搜索联想信号")
    if title_count:
        parts.append(f"现有商品标题抽词出现 {title_count} 次，可作为真实页面用词参考")
    if product_count:
        parts.append(f"已在关键词库/快照中关联 {product_count} 个商品")
    if avg_total_score is not None:
        parts.append(f"已有机会相关商品均分约 {avg_total_score:.0f}")
    if token_count in (3, 4):
        parts.append("长尾结构清晰，适合做差异化验证")
    elif token_count == 1:
        parts.append("词过宽，需要继续扩成长尾词后再判断")
    if confidence_score < 45:
        parts.append("证据仍偏少，暂不应直接进入采集队列")
    parts.append(f"创意分 {idea_score:.0f}，置信度 {confidence_score:.0f}")
    return "；".join(parts)


def _normalize_marketplace(value: str | None) -> str:
    return (value or "US").strip().upper()[:16] or "US"


def _normalize_limit(limit: int) -> int:
    return _clamp_int(limit, default=100, minimum=1, maximum=500)


def _normalize_offset(offset: int) -> int:
    return _clamp_int(offset, default=0, minimum=0, maximum=1_000_000)


def _normalize_ids(ids: list[int]) -> list[int]:
    normalized: list[int] = []
    for item in ids:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in normalized:
            normalized.append(value)
    return normalized[:200]


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads_json(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _append_unique(target: dict[str, Any], key: str, value: Any, *, max_items: int) -> None:
    if value is None or value == "":
        return
    items = list(target.get(key) or [])
    if value not in items:
        items.append(value)
    target[key] = items[:max_items]


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
