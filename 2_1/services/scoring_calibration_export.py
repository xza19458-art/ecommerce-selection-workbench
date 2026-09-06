"""Persist compact, versioned scoring V2 calibration datasets as local JSON."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from pkg_paths import user_data_path
from services.scoring_v2 import CALIBRATION_VERSION, MODEL_VERSION, STRATEGIES, get_scoring_v2_model


EXPORT_TYPE = "scoring_v2_shadow_calibration"
EXPORT_SCHEMA_VERSION = "scoring-calibration-dataset-v2"
EXPORT_DIRECTORY = ("exports", "scoring_calibration")
MAX_EXPORT_BYTES = 5 * 1024 * 1024
MAX_EXPORT_SAMPLES = 500
MAX_NOTE_LENGTH = 2_000
MAX_ARCHIVE_FILES = 100
MAX_ARCHIVE_ROWS = 500
EXPORT_PURPOSES = {
    "reviewed": "model_calibration_labels",
    "all": "audit_snapshot",
}
ALLOWED_JUDGMENTS = {
    "pending",
    "reasonable",
    "too_optimistic",
    "too_conservative",
    "evidence_issue",
    "needs_collection",
}
_MODEL_INPUT_FIELDS = (
    "price",
    "rating",
    "review_count",
    "monthly_bought",
    "organic_rank",
    "rank_confidence",
    "is_deal",
)
_TREND_FIELDS = (
    "sample_size",
    "signal_point_count",
    "span_days",
    "confidence_score",
    "growth_score",
    "promotion_warning",
)
_TREND_METRICS = ("monthly_bought", "organic_rank", "price", "rating", "review_count")
_TREND_METRIC_FIELDS = ("start", "end", "change_ratio", "direction")
_COMPONENT_GROUPS = {
    "opportunity": ("demand", "growth", "acceptance", "visibility", "improvement_space"),
    "risk": ("review_barrier", "incumbent_pressure", "quality_risk", "price_risk", "promo_risk"),
    "confidence": ("field_completeness", "trend_evidence", "rank_reliability", "freshness", "traceability"),
}
_COMPONENT_FIELDS = ("score", "weight", "status")
_OUTPUT_FIELDS = (
    "opportunity_score",
    "risk_score",
    "confidence_score",
    "legacy_total_score",
    "recommendation_code",
)
_STRATEGY_OUTPUT_FIELDS = (
    "opportunity_score",
    "risk_score",
    "confidence_score",
    "recommendation_code",
)
_EVIDENCE_ID_FIELDS = (
    "rank_snapshot_id",
    "product_snapshot_id",
    "history_snapshot_ids",
    "keyword_rank_snapshot_ids",
)
_SELECTION_FIELDS = (
    "marketplace",
    "keyword_filter",
    "sample_per_bucket",
    "source_sample_count",
    "review_pool_count",
    "bucket_count",
    "sample_generated_at",
    "sample_seed",
    "sampling_method",
    "excluded_asin_count",
    "excluded_asins",
    "batch_number",
)


class ScoringCalibrationExportError(ValueError):
    """Raised when an explicit calibration export is malformed or unsafe."""


def scoring_calibration_export_directory() -> Path:
    """Return the fixed writable export directory for dev and packaged runs."""

    return user_data_path(*EXPORT_DIRECTORY).resolve()


def load_exported_scoring_calibration_reviews(
    *,
    limit: int = 200,
    input_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Read reviewed samples from valid local exports without mutating them."""

    row_limit = max(1, min(MAX_ARCHIVE_ROWS, int(limit or 200)))
    directory = (
        Path(input_directory).expanduser().resolve()
        if input_directory is not None
        else scoring_calibration_export_directory()
    )
    if not directory.exists():
        return {
            "rows": [],
            "total": 0,
            "file_count": 0,
            "warnings": [],
            "directory": str(directory),
            "writes_database": False,
        }

    warnings: list[str] = []
    candidates: list[tuple[int, Path]] = []
    for path in directory.glob("*.json"):
        try:
            metadata = path.stat()
        except OSError as error:
            warnings.append(f"{path.name}：无法读取文件信息（{error}）")
            continue
        if metadata.st_size > MAX_EXPORT_BYTES:
            warnings.append(f"{path.name}：文件超过 5 MB，已跳过")
            continue
        candidates.append((metadata.st_mtime_ns, path))
    candidates.sort(key=lambda item: item[0], reverse=True)

    rows_by_key: dict[str, dict[str, Any]] = {}
    valid_file_count = 0
    for modified_ns, path in candidates[:MAX_ARCHIVE_FILES]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            document = _validated_document(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ScoringCalibrationExportError) as error:
            if len(warnings) < 20:
                warnings.append(f"{path.name}：{error}")
            continue

        valid_file_count += 1
        exported_at = str(
            raw.get("exported_at")
            or (raw.get("local_export") or {}).get("saved_at")
            or ""
        )
        selection = {
            **document["selection"],
            "primary_strategy": document["model_spec"]["primary_strategy"],
        }
        for sample in document["samples"]:
            if sample["human_label"]["judgment"] == "pending":
                continue
            sample_key = sample["sample_key"]
            archive_order = (
                _archive_timestamp(sample["human_label"].get("reviewed_at")),
                _archive_timestamp(exported_at),
                modified_ns,
            )
            existing = rows_by_key.get(sample_key)
            if existing and archive_order <= existing["_archive_order"]:
                continue
            rows_by_key[sample_key] = {
                **sample,
                "archive_source": "exported_json",
                "source_file": path.name,
                "exported_at": exported_at,
                "selection": selection,
                "_archive_order": archive_order,
            }

    ordered_rows = sorted(
        rows_by_key.values(),
        key=lambda item: item["_archive_order"],
        reverse=True,
    )
    rows = []
    for item in ordered_rows:
        row = dict(item)
        row.pop("_archive_order", None)
        rows.append(row)
    return {
        "rows": rows[:row_limit],
        "total": len(rows),
        "file_count": valid_file_count,
        "warnings": warnings[:20],
        "directory": str(directory),
        "writes_database": False,
    }


def _archive_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def export_scoring_calibration(
    payload: Mapping[str, Any],
    *,
    output_directory: str | Path | None = None,
    saved_at: datetime | None = None,
) -> dict[str, Any]:
    """Whitelist, validate and atomically persist one calibration dataset."""

    document = _validated_document(payload)
    timestamp = (saved_at or datetime.now().astimezone()).astimezone()
    directory = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else scoring_calibration_export_directory()
    )
    directory.mkdir(parents=True, exist_ok=True)

    selection = document["selection"]
    strategy = str(document["model_spec"]["primary_strategy"])
    prefix = "评分V2校准标签" if selection["export_scope"] == "reviewed" else "评分V2审计样本"
    stamp = timestamp.strftime("%Y-%m-%d_%H%M%S_%f")[:-3]
    filename = f"{prefix}_{strategy}_{stamp}.json"
    target = directory / filename

    document["local_export"] = {
        "saved_at": timestamp.isoformat(timespec="seconds"),
        "storage": "fixed_local_directory",
        "filename": filename,
    }
    encoded = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise ScoringCalibrationExportError("校准导出内容超过 5 MB，请减少每层样本数后重试")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".scoring-calibration-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return {
        "saved": True,
        "path": str(target.resolve()),
        "directory": str(directory),
        "filename": filename,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_scope": selection["export_scope"],
        "bytes": len(encoded),
        "sample_count": selection["exported_sample_count"],
        "reviewed_count": selection["reviewed_count"],
        "saved_at": timestamp.isoformat(timespec="seconds"),
        "writes_database": False,
    }


def _validated_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ScoringCalibrationExportError("校准导出内容必须是 JSON 对象")
    if payload.get("export_type") != EXPORT_TYPE:
        raise ScoringCalibrationExportError("不是受支持的评分 V2 校准导出格式")
    if payload.get("schema_version") != EXPORT_SCHEMA_VERSION:
        raise ScoringCalibrationExportError("校准导出结构已升级，请刷新页面后重新导出")

    model_spec = _normalize_model_spec(payload.get("model_spec"))
    calibration_spec = _normalize_calibration_spec(payload.get("calibration_spec"))
    selection_raw = _mapping(payload.get("selection"), "校准导出缺少筛选与范围信息")
    export_scope = str(selection_raw.get("export_scope") or "").strip()
    if export_scope not in EXPORT_PURPOSES:
        raise ScoringCalibrationExportError("导出范围仅支持已复核标签或全部审计样本")
    expected_purpose = EXPORT_PURPOSES[export_scope]
    if payload.get("dataset_purpose") != expected_purpose:
        raise ScoringCalibrationExportError("导出用途与导出范围不匹配")

    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        if export_scope == "reviewed":
            raise ScoringCalibrationExportError("当前没有已复核样本，请先填写人工判断")
        raise ScoringCalibrationExportError("当前没有可导出的校准样本")
    if len(raw_samples) > MAX_EXPORT_SAMPLES:
        raise ScoringCalibrationExportError("单次最多导出 500 条校准样本")

    samples: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, raw_sample in enumerate(raw_samples, start=1):
        sample = _normalize_sample(raw_sample, index=index)
        sample_key = sample["sample_key"]
        if sample_key in seen_keys:
            raise ScoringCalibrationExportError(f"校准样本键重复：{sample_key}")
        seen_keys.add(sample_key)
        if export_scope == "reviewed" and sample["human_label"]["judgment"] == "pending":
            raise ScoringCalibrationExportError("已复核导出不能包含未复核样本")
        samples.append(sample)

    reviewed_count = sum(sample["human_label"]["judgment"] != "pending" for sample in samples)
    selection = _subset(selection_raw, _SELECTION_FIELDS)
    selection.update(
        {
            "export_scope": export_scope,
            "source_sample_count": _as_nonnegative_int(selection_raw.get("source_sample_count"), len(samples)),
            "review_pool_count": _as_nonnegative_int(selection_raw.get("review_pool_count"), reviewed_count),
            "excluded_asin_count": _as_nonnegative_int(selection_raw.get("excluded_asin_count"), 0),
            "excluded_asins": _string_list(selection_raw.get("excluded_asins"), limit=300),
            "batch_number": max(1, _as_nonnegative_int(selection_raw.get("batch_number"), 1)),
            "exported_sample_count": len(samples),
            "reviewed_count": reviewed_count,
        }
    )

    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_type": EXPORT_TYPE,
        "dataset_purpose": expected_purpose,
        "exported_at": str(payload.get("exported_at") or ""),
        "selection": selection,
        "samples": samples,
        "model_spec": model_spec,
        "calibration_spec": calibration_spec,
    }


def _normalize_model_spec(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "校准导出缺少模型规格")
    version = str(raw.get("version") or "").strip()
    primary_strategy = str(raw.get("primary_strategy") or "").strip()
    if version != MODEL_VERSION:
        raise ScoringCalibrationExportError("模型版本不匹配，请重新生成校准样本后再导出")
    if primary_strategy not in STRATEGIES:
        raise ScoringCalibrationExportError("校准导出的主策略无效")

    canonical = get_scoring_v2_model()
    strategy_specs = {
        str(item["code"]): {
            "opportunity_weights": dict(item["opportunity_weights"]),
            "risk_weights": dict(item["risk_weights"]),
        }
        for item in canonical["strategies"]
    }
    return {
        "version": MODEL_VERSION,
        "scope": canonical["scope"],
        "rank_basis": canonical["rank_basis"],
        "primary_strategy": primary_strategy,
        "strategies": strategy_specs,
        "recommendation_labels": dict(canonical["recommendations"]),
        "missing_value_policy": canonical["missing_value_policy"],
    }


def _normalize_calibration_spec(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "校准导出缺少校准规格")
    if raw.get("version") != CALIBRATION_VERSION:
        raise ScoringCalibrationExportError("校准版本不匹配，请重新生成样本后再导出")
    labels = raw.get("label_definitions")
    label_definitions = []
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, Mapping):
                label_definitions.append(_subset(item, ("code", "label")))
    audit_labels = raw.get("audit_flag_labels")
    normalized_audit_labels: dict[str, Any] = {}
    if isinstance(audit_labels, Mapping):
        for code, item in audit_labels.items():
            if isinstance(item, Mapping):
                normalized_audit_labels[str(code)] = _subset(item, ("label", "severity"))
    return {
        "version": CALIBRATION_VERSION,
        "label_definitions": label_definitions,
        "audit_flag_labels": normalized_audit_labels,
    }


def _normalize_sample(value: Any, *, index: int) -> dict[str, Any]:
    raw = _mapping(value, f"第 {index} 条校准样本格式无效")
    sample_key = str(raw.get("sample_key") or "").strip()
    sample_seed = str(raw.get("sample_seed") or "baseline").strip() or "baseline"
    asin = str(raw.get("asin") or "").strip().upper()
    if not sample_key or not asin:
        raise ScoringCalibrationExportError(f"第 {index} 条校准样本缺少样本键或 ASIN")
    if len(sample_seed) > 64:
        raise ScoringCalibrationExportError(f"第 {index} 条校准样本的随机种子超过 64 个字符")

    keyword = raw.get("keyword")
    keyword = _subset(keyword, ("id", "text")) if isinstance(keyword, Mapping) else {}
    stratum = raw.get("stratum")
    stratum = _subset(stratum, ("code", "label")) if isinstance(stratum, Mapping) else {}
    model_inputs = _subset(raw.get("model_inputs"), _MODEL_INPUT_FIELDS)
    trend_features = _normalize_trend(raw.get("trend_features"))
    component_scores = _normalize_components(raw.get("component_scores"))
    primary_output = _subset(raw.get("primary_output"), _OUTPUT_FIELDS)
    strategy_outputs = _normalize_strategy_outputs(raw.get("strategy_outputs"))
    evidence_ids = _normalize_evidence_ids(raw.get("evidence_ids"))
    human_label = _normalize_human_label(raw.get("human_label"), index=index)
    audit_flag_codes = _string_list(raw.get("audit_flag_codes"), limit=50)

    return {
        "sample_key": sample_key,
        "sample_seed": sample_seed,
        "product_id": raw.get("product_id"),
        "asin": asin,
        "title": str(raw.get("title") or ""),
        "keyword": keyword,
        "stratum": stratum,
        "observed_at": raw.get("observed_at"),
        "human_label": human_label,
        "primary_output": primary_output,
        "model_inputs": model_inputs,
        "trend_features": trend_features,
        "component_scores": component_scores,
        "strategy_outputs": strategy_outputs,
        "audit_flag_codes": audit_flag_codes,
        "evidence_ids": evidence_ids,
    }


def _normalize_trend(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    result = _subset(raw, _TREND_FIELDS)
    metrics = raw.get("metrics")
    normalized_metrics: dict[str, Any] = {}
    if isinstance(metrics, Mapping):
        for key in _TREND_METRICS:
            item = metrics.get(key)
            if isinstance(item, Mapping):
                normalized_metrics[key] = _subset(item, _TREND_METRIC_FIELDS)
    result["metrics"] = normalized_metrics
    return result


def _normalize_components(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    groups: dict[str, Any] = {}
    for group, allowed_codes in _COMPONENT_GROUPS.items():
        group_raw = raw.get(group)
        normalized: dict[str, Any] = {}
        if isinstance(group_raw, Mapping):
            for code in allowed_codes:
                item = group_raw.get(code)
                if isinstance(item, Mapping):
                    normalized[code] = _subset(item, _COMPONENT_FIELDS)
        groups[group] = normalized
    return groups


def _normalize_strategy_outputs(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    result: dict[str, Any] = {}
    for code in STRATEGIES:
        item = raw.get(code)
        if isinstance(item, Mapping):
            result[code] = _subset(item, _STRATEGY_OUTPUT_FIELDS)
    return result


def _normalize_evidence_ids(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    result = _subset(raw, _EVIDENCE_ID_FIELDS)
    result["history_snapshot_ids"] = _integer_list(result.get("history_snapshot_ids"), limit=100)
    result["keyword_rank_snapshot_ids"] = _integer_list(result.get("keyword_rank_snapshot_ids"), limit=100)
    return result


def _normalize_human_label(value: Any, *, index: int) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    judgment = str(raw.get("judgment") or "pending").strip()
    note = str(raw.get("note") or "").strip()
    if judgment not in ALLOWED_JUDGMENTS:
        raise ScoringCalibrationExportError(f"第 {index} 条样本的人工判断无效")
    if len(note) > MAX_NOTE_LENGTH:
        raise ScoringCalibrationExportError(f"第 {index} 条样本的备注超过 2000 个字符")
    return {
        "judgment": judgment,
        "note": note,
        "reviewed_at": raw.get("reviewed_at"),
    }


def _mapping(value: Any, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScoringCalibrationExportError(message)
    return value


def _subset(value: Any, fields: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {field: value.get(field) for field in fields if field in value}


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _integer_list(value: Any, *, limit: int) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value[:limit]:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _as_nonnegative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default
