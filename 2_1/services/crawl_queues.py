"""Named manual-crawl queues — 本地按用户的采集工作流配置。

镜像 `services/settings.py` 的本地 JSON 存储做法。命名队列是采集工作流配置（**非业务
数据**），存 `config/crawl_queues.json`，不进 MySQL、不涉 Storage。每个队列 = 名称 +
关键词任务列表（`{keyword, pages}`），用于复用同一批关键词的连续采集。
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from pkg_paths import user_data_path


MAX_PAGES_PER_KEYWORD = 7
MAX_QUEUE_NAME_LEN = 64
MAX_ITEMS = 200


class CrawlQueueError(ValueError):
    """队列配置错误（命名空、JSON 损坏等）。"""


def _config_path() -> Path:
    # 与 settings.json 同目录；冻结态走 exe 同级、可写。
    return user_data_path("config", "crawl_queues.json")


def _normalize_pages(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 1
    return max(1, min(number, MAX_PAGES_PER_KEYWORD))


def _normalize_keyword(value: Any) -> str:
    return str(value or "").strip()


def _normalize_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise CrawlQueueError("队列名不能为空")
    return name[:MAX_QUEUE_NAME_LEN]


def _normalize_items(raw: Any) -> list[dict[str, Any]]:
    """规范化任务列表：去空关键词、页数钳 1..7、按关键词去重、限量。"""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        keyword = _normalize_keyword(entry.get("keyword"))
        if not keyword:
            continue
        dedupe_key = keyword.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append({
            "keyword": keyword,
            "pages": _normalize_pages(entry.get("pages")),
            # 上次成功采集时间；保存后复用可知该词是否/何时采过。空串=未采过。
            "collected_at": str(entry.get("collected_at") or ""),
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


def _normalize_queue(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _normalize_name(raw.get("name")),
        "items": _normalize_items(raw.get("items")),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def _load_raw(path: Path | None = None) -> list[dict[str, Any]]:
    config_path = path or _config_path()
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrawlQueueError(f"队列配置 JSON 格式错误: {exc}") from exc
    if not isinstance(data, dict):
        raise CrawlQueueError("队列配置必须是 JSON 对象。")
    queues = data.get("queues")
    return queues if isinstance(queues, list) else []


def _write_raw(queues: list[dict[str, Any]], path: Path | None = None) -> None:
    config_path = path or _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"queues": queues}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_queues(*, path: Path | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in _load_raw(path):
        if not isinstance(entry, dict):
            continue
        try:
            out.append(_normalize_queue(entry))
        except CrawlQueueError:
            continue
    return out


def get_queue(name: str, *, path: Path | None = None) -> dict[str, Any] | None:
    target = _normalize_name(name)
    for queue in list_queues(path=path):
        if queue["name"] == target:
            return queue
    return None


def save_queue(name: str, items: Any, *, path: Path | None = None) -> dict[str, Any]:
    """按名 upsert 一个队列；返回规范化后的队列。"""
    queue = {
        "name": _normalize_name(name),
        "items": _normalize_items(items),
        "updated_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
    }
    kept = [
        entry
        for entry in list_queues(path=path)
        if entry["name"] != queue["name"]
    ]
    kept.append(queue)
    _write_raw(kept, path)
    return queue


def delete_queue(name: str, *, path: Path | None = None) -> bool:
    target = _normalize_name(name)
    existing = list_queues(path=path)
    kept = [entry for entry in existing if entry["name"] != target]
    if len(kept) == len(existing):
        return False
    _write_raw(kept, path)
    return True
