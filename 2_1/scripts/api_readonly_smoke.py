"""Read-only cross-module API smoke check for a running local application."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen


READ_ONLY_PATHS = (
    "/api/health",
    "/api/ready",
    "/api/recommendations?limit=1",
    "/api/products?limit=1",
    "/api/metrics/products?limit=1",
    "/api/metrics/evidence?limit=1&file_limit=1",
    "/api/keywords/opportunities?limit=1",
    "/api/keyword-library/keywords?limit=1",
    "/api/keyword-library/tree?max_keywords=20",
    "/api/keyword-workshop/runs?limit=1",
    "/api/keyword-workshop/ideas?limit=1",
    "/api/research-projects?limit=1",
    "/api/research-review-queue?limit=1",
    "/api/market-niches?limit=1",
    "/api/scoring-v2/replay?limit=1",
    "/api/scoring-v2/calibration?sample_per_bucket=1",
    "/api/scoring-v2/calibration/reviews?limit=1",
    "/api/domain-models/catalog",
    "/api/domain-models?status=active&limit=1",
    "/api/reviews/insights?limit=1",
    "/api/tasks?limit=1",
    "/api/tracking/tasks?limit=1",
    "/api/crawl/queues",
    "/api/warehouse/status",
    "/api/settings",
    "/api/system/deployment-preflight",
    "/api/agent/config",
)


def fetch_json(base_url: str, path: str, *, timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if response.status != 200 or payload.get("ok") is not True:
        raise RuntimeError(f"unexpected response: HTTP {response.status}")
    return payload


def post_readonly_json(
    base_url: str,
    path: str,
    body: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    if response.status != 200 or payload.get("ok") is not True or data.get("writes_production_score") is not False:
        raise RuntimeError(f"unexpected read-only response: HTTP {response.status}")
    return payload


def page_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return [row for row in data["rows"] if isinstance(row, dict)]
    return []


def run(base_url: str, *, timeout: float = 60.0) -> int:
    failures: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    for path in READ_ONLY_PATHS:
        try:
            payloads[path] = fetch_json(base_url, path, timeout=timeout)
            print(f"[PASS] {path}")
        except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
            failures.append(path)
            print(f"[FAIL] {path}: {exc}")

    dynamic_paths: list[str] = []
    products = page_rows(payloads.get("/api/products?limit=1", {}))
    if products:
        asin = quote(str(products[0]["asin"]), safe="")
        score_keyword = str(products[0].get("score_keyword") or "").strip()
        query = "?" + urlencode({"score_keyword": score_keyword}) if score_keyword else ""
        dynamic_paths.extend(
            (
                f"/api/products/{asin}{query}",
                f"/api/products/{asin}/trend{query}",
                f"/api/products/{asin}/advice{query}",
                f"/api/metrics/products/{asin}",
            )
        )
    else:
        print("[SKIP] 商品详情、趋势、建议和指标：商品池为空")
    tracking_tasks = page_rows(payloads.get("/api/tracking/tasks?limit=1", {}))
    if tracking_tasks:
        task_id = int(tracking_tasks[0]["id"])
        dynamic_paths.append(f"/api/tracking/tasks/{task_id}/evidence")
    else:
        print("[SKIP] 关键词追踪证据复盘：当前没有追踪任务")
    projects = page_rows(payloads.get("/api/research-projects?limit=1", {}))
    if projects:
        project_id = projects[0]["id"]
        dynamic_paths.extend(
            (
                f"/api/research-projects/{project_id}",
                f"/api/research-projects/{project_id}/detail-readiness",
                f"/api/metrics/evidence?limit=1&file_limit=1&project_id={project_id}",
                f"/api/research-projects/{project_id}/decision-report",
                f"/api/research-projects/{project_id}/observation-plan",
                f"/api/research-projects/{project_id}/report-baseline-diff",
            )
        )
        versions_path = f"/api/research-projects/{project_id}/report-versions?limit=2&offset=0"
        try:
            versions_payload = fetch_json(base_url, versions_path, timeout=timeout)
            print(f"[PASS] {versions_path}")
            versions = page_rows(versions_payload)
            if versions:
                to_version = int(versions[0]["version_no"])
                from_version = int(versions[1]["version_no"]) if len(versions) > 1 else to_version
                dynamic_paths.extend(
                    (
                        f"/api/research-projects/{project_id}/report-versions/{to_version}",
                        (
                            f"/api/research-projects/{project_id}/report-version-diff"
                            f"?from_version={from_version}&to_version={to_version}"
                        ),
                    )
                )
            else:
                print("[SKIP] 冻结报告详情与差异：当前项目尚无报告版本")
        except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
            failures.append(versions_path)
            print(f"[FAIL] {versions_path}: {exc}")
    niches = page_rows(payloads.get("/api/market-niches?limit=1", {}))
    if niches:
        niche = niches[0]
        dynamic_paths.append(f"/api/market-niches/{niche['id']}")
        if int(niche.get("snapshot_count") or 0) > 0:
            dynamic_paths.append(f"/api/competitive-graph/{niche['id']}")
        else:
            print("[SKIP] 竞品图谱：当前利基尚无冻结快照")

    domain_profiles = page_rows(payloads.get("/api/domain-models?status=active&limit=1", {}))
    if domain_profiles:
        profile_id = int(domain_profiles[0]["id"])
        batch_path = f"/api/domain-models/{profile_id}/evaluate-batch"
        try:
            post_readonly_json(
                base_url,
                batch_path,
                {"sample_limit": 1, "limit": 1, "offset": 0},
                timeout=timeout,
            )
            print(f"[PASS] {batch_path}（只读试算）")
        except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
            failures.append(batch_path)
            print(f"[FAIL] {batch_path}: {exc}")
    else:
        print("[SKIP] 领域模型批量验证：当前没有已启用模型")

    for path in dynamic_paths:
        try:
            fetch_json(base_url, path, timeout=timeout)
            print(f"[PASS] {path}")
        except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
            failures.append(path)
            print(f"[FAIL] {path}: {exc}")

    asset_path = "/vendor/echarts-5.6.0.min.js"
    try:
        url = urljoin(base_url.rstrip("/") + "/", asset_path.lstrip("/"))
        with urlopen(url, timeout=timeout) as response:
            body = response.read()
        if response.status != 200 or len(body) <= 1_000_000 or b'version="5.6.0"' not in body:
            raise RuntimeError("本地 ECharts 文件不完整")
        print(f"[PASS] {asset_path}")
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        failures.append(asset_path)
        print(f"[FAIL] {asset_path}: {exc}")

    print(f"完成：失败 {len(failures)} 项。")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查本地应用主要 API 与静态资源")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    return run(args.base_url, timeout=max(1.0, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
