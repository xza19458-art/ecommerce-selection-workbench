"""应用控制器：处理网页爬取和数据分析逻辑。"""

from pathlib import Path
from datetime import datetime
import logging
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import random
from typing import Callable

from main import parse_amazon_page


logger = logging.getLogger(__name__)


class AppController:
    """应用控制器"""

    def __init__(self) -> None:
        self._browser = None
        self._amazon_location_prepared = False

    def _get_browser(self) -> webdriver.Chrome:
        """获取浏览器实例"""
        if not self._browser:
            options = Options()

            # 使用本地Chrome浏览器
            chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            import os
            if os.path.exists(chrome_path):
                options.binary_location = chrome_path

            # 伪装成正常用户
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-infobars")
            options.add_argument("--start-maximized")
            options.add_argument("--disable-notifications")

            # 使用临时用户数据目录
            import tempfile
            temp_dir = tempfile.mkdtemp()
            options.add_argument(f"--user-data-dir={temp_dir}")

            # 随机选择用户代理
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.160 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.117 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.5938.132 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.180 Safari/537.36"
            ]
            options.add_argument(f"user-agent={random.choice(user_agents)}")

            # 禁用自动化扩展
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            # 使用Selenium的自动驱动管理
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager

            service = Service(ChromeDriverManager().install())
            self._browser = webdriver.Chrome(service=service, options=options)

            # 设置超时时间
            self._browser.set_page_load_timeout(30)
            self._browser.set_script_timeout(30)
            self._browser.implicitly_wait(10)

            # 去 webdriver 标识
            self._browser.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        return self._browser

    def stop_browser(self) -> None:
        """停止浏览器"""
        if self._browser:
            try:
                self._browser.quit()
            except:
                pass
            self._browser = None
            self._amazon_location_prepared = False

    def open_amazon_page(self) -> dict:
        """Open Amazon in the shared crawler browser for manual preparation."""
        browser = self._get_browser()
        browser.get("https://www.amazon.com/")
        time.sleep(random.uniform(1, 2))
        self._amazon_location_prepared = True
        return {
            "状态": "已打开",
            "URL": getattr(browser, "current_url", "https://www.amazon.com/"),
            "标题": getattr(browser, "title", ""),
            "message": "Amazon 页面已打开，后续手动采集将复用当前浏览器会话。",
        }

    def collect_amazon_search_pages(
        self,
        url: str,
        pages: int = 1,
        on_page: Callable[[int, str, str, str], bool | None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
        page_delay_seconds: tuple[int, int] = (3, 5),
    ) -> list[dict]:
        """按 Amazon 搜索页真实翻页流程采集 HTML。

        这是 `crawl_amazon()` 与 B1 快照采集器共享的底层流程：打开搜索页、
        尝试设置纽约邮编、滚动加载、点击 Amazon 的下一页按钮。调用方通过
        `on_page` 决定如何保存/校验页面；返回 False 时立即停止后续页面。
        """
        browser = self._get_browser()

        # 从URL中提取关键词
        import urllib.parse
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        keyword = query_params.get('k', ['unknown'])[0].replace('+', ' ')

        results: list[dict] = []

        # 先打开Chrome浏览器
        browser.get("about:blank")
        time.sleep(random.uniform(2, 4))

        # 再搜索amazon.com
        browser.get(url)
        time.sleep(random.uniform(2, 5))

        if not self._amazon_location_prepared:
            # 设置地址为纽约
            try:
                location_button = browser.find_element(By.ID, "nav-global-location-popover-link")
                location_button.click()
                time.sleep(2)

                zip_input = browser.find_element(By.ID, "GLUXZipUpdateInput")
                zip_input.clear()
                zip_input.send_keys("10001")

                apply_button = browser.find_element(By.XPATH, '//input[@aria-labelledby="GLUXZipUpdate-announce"]')
                apply_button.click()

                time.sleep(3)
                self._amazon_location_prepared = True
            except Exception as e:
                print(f"设置地址失败: {str(e)}")

        for page_num in range(1, pages + 1):
            if stop_requested and stop_requested():
                break

            # 上下小幅度滚动至少3次
            for i in range(3):
                browser.execute_script("window.scrollBy(0, 500);")
                time.sleep(random.uniform(1, 2))
                browser.execute_script("window.scrollBy(0, -300);")
                time.sleep(random.uniform(1, 2))

            browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(2, 4))

            browser.execute_script("window.scrollTo(0, 0);")
            time.sleep(random.uniform(1, 2))

            try:
                WebDriverWait(browser, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-component-type='s-search-result']"))
                )
            except:
                pass

            time.sleep(random.uniform(3, 5))

            page_html = browser.page_source
            current_url = getattr(browser, "current_url", url)
            title = getattr(browser, "title", "")
            should_continue = True
            if on_page:
                callback_result = on_page(page_num, page_html, current_url, title)
                should_continue = callback_result is not False

            results.append(
                {
                    "page_no": page_num,
                    "url": current_url,
                    "title": title,
                    "html": page_html,
                    "keyword": keyword,
                }
            )

            if not should_continue or page_num >= pages:
                break

            if stop_requested and stop_requested():
                break

            try:
                next_button = browser.find_element(By.CSS_SELECTOR, "a.s-pagination-next")
                next_button.click()
                time.sleep(random.uniform(*page_delay_seconds))
            except Exception as e:
                print(f"无法点击下一页: {str(e)}")
                try:
                    current_page_elem = browser.find_element(By.CSS_SELECTOR, "span.s-pagination-item.s-pagination-selected")
                    current_page = int(current_page_elem.text)
                    if current_page < pages:
                        print(f"已达到最大页数 {current_page}，无法继续爬取")
                except:
                    pass
                break

        time.sleep(random.uniform(2, 4))
        return results

    def crawl_amazon(self, url: str, save_path: Path, pages: int = 1) -> None:
        """爬取Amazon网页

        Args:
            url: Amazon搜索URL
            save_path: 保存HTML的路径
            pages: 要爬取的页数，从第一页开始到第pages页
        """
        import urllib.parse
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        keyword = query_params.get('k', ['unknown'])[0].replace('+', ' ')

        # 创建以关键词命名的文件夹
        keyword_folder = save_path.parent / keyword.replace(' ', '_')
        keyword_folder.mkdir(exist_ok=True)

        def save_page(page_num: int, page_html: str, _current_url: str, _title: str) -> bool:
            page_file_name = f"{keyword.replace(' ', '_')}_{page_num}.html"
            page_save_path = keyword_folder / page_file_name

            with open(page_save_path, "w", encoding="utf-8") as f:
                f.write(page_html)

            print(f"已保存第 {page_num} 页到: {page_save_path}")
            return True

        limits = _collection_limits()
        effective_pages = _clamp_int(pages, 1, limits["max_pages_per_keyword"])
        self.collect_amazon_search_pages(
            url,
            pages=effective_pages,
            on_page=save_page,
            page_delay_seconds=(limits["page_delay_min_seconds"], limits["page_delay_max_seconds"]),
        )

    def run_keyword_crawl(
        self,
        keyword: str,
        pages: int | None = None,
        *,
        record_job: bool = True,
    ) -> dict:
        """Run a manual Amazon keyword crawl and return saved HTML files.

        This is the Web equivalent of the Tkinter "运行爬取" button: it saves
        search-result HTML under `html/<keyword>/` only, and does not import or
        score data. Basic blocking / empty-page detection stops the run early.
        """
        import urllib.parse

        from parsers.amazon_search_parser import parse_amazon_search_content
        from services.snapshot_collection_runner import classify_amazon_search_page

        keyword = (keyword or "").strip()
        if not keyword:
            raise ValueError("请输入爬取关键词")
        limits = _collection_limits()
        try:
            requested_pages = limits["pages_per_keyword"] if pages is None else int(pages)
        except (TypeError, ValueError) as exc:
            raise ValueError("请输入有效的爬取页数") from exc
        pages = _clamp_int(requested_pages, 1, limits["max_pages_per_keyword"])
        parameter_changes: list[dict] = []
        if pages != requested_pages:
            parameter_changes.append(
                {
                    "字段": "pages",
                    "原值": requested_pages,
                    "实际值": pages,
                    "原因": f"服务端按设置将单关键词页数限制在 1..{limits['max_pages_per_keyword']}。",
                }
            )

        project_root = Path(__file__).resolve().parents[1]
        html_root = project_root / "html"
        safe_keyword = _safe_file_part(keyword)
        keyword_dir = html_root / safe_keyword
        blocked_dir = html_root / "_blocked" / safe_keyword
        url = f"https://www.amazon.com/s?k={urllib.parse.quote_plus(keyword)}"
        started_at = datetime.now().replace(microsecond=0)
        pages_saved: list[dict] = []
        stop_reason: str | None = None
        job_id = self._try_create_crawl_job(keyword, url, pages) if record_job else None

        def relative(path: Path) -> str:
            return path.relative_to(project_root).as_posix()

        def save_page(page_num: int, page_html: str, current_url: str, title: str) -> bool:
            nonlocal stop_reason
            state, reason = classify_amazon_search_page(page_html, current_url=current_url, title=title)
            total_found = 0
            total_valid = 0
            save_dir = keyword_dir
            suffix = ""

            if state == "ok":
                parse_result = parse_amazon_search_content(
                    page_html,
                    source_file=f"{safe_keyword}_{page_num}.html",
                    keyword=keyword,
                    marketplace="US",
                    require_complete=True,
                )
                total_found = parse_result.total_found
                total_valid = parse_result.total_valid
                if total_valid <= 0:
                    state = "empty"
                    reason = "页面存在结果节点，但严格解析后有效商品为 0，停止本轮避免写入噪声。"

            if state != "ok":
                save_dir = blocked_dir
                suffix = f"_{state}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / f"{safe_keyword}_{page_num}{suffix}.html"
            save_path.write_text(page_html, encoding="utf-8")

            page_result = {
                "页码": page_num,
                "URL": current_url,
                "状态": "已保存" if state == "ok" else state,
                "保存文件": relative(save_path),
                "解析商品数": total_found,
                "有效商品数": total_valid,
                "原因": reason or "",
            }
            pages_saved.append(page_result)
            if state != "ok":
                stop_reason = reason or "页面异常，已停止本轮。"
                return False
            return True

        try:
            self.collect_amazon_search_pages(
                url,
                pages=pages,
                on_page=save_page,
                page_delay_seconds=(limits["page_delay_min_seconds"], limits["page_delay_max_seconds"]),
            )
            status = "完成" if not stop_reason else "异常停止"
            message = stop_reason or "爬取完成；HTML 已保存，后续可到「本地 HTML 入库」预览并写入数据库。"
            self._try_finish_crawl_job(job_id, status, pages_saved, None if status == "完成" else message)
            return {
                "关键词": keyword,
                "URL": url,
                "请求页数": requested_pages,
                "实际页数": pages,
                "页间隔秒": [limits["page_delay_min_seconds"], limits["page_delay_max_seconds"]],
                "参数调整": parameter_changes,
                "保存页数": len([p for p in pages_saved if p.get("状态") == "已保存"]),
                "状态": status,
                "开始时间": started_at.isoformat(sep=" "),
                "结束时间": datetime.now().replace(microsecond=0).isoformat(sep=" "),
                "保存目录": relative(keyword_dir),
                "页面": pages_saved,
                "message": message,
            }
        except Exception as exc:
            self._try_finish_crawl_job(job_id, "失败", pages_saved, str(exc))
            raise

    def _try_create_crawl_job(self, keyword: str, url: str, pages: int) -> int | None:
        try:
            from database.mysql_client import MySQLClient

            db = MySQLClient()
            with db.connect() as conn:
                with conn.cursor() as cursor:
                    return db.create_job(cursor, keyword, url, pages)
        except Exception:
            logger.warning("写入爬取任务日志失败，继续执行爬取。", exc_info=True)
            return None

    def _try_finish_crawl_job(
        self,
        job_id: int | None,
        status: str,
        pages: list[dict],
        error_message: str | None,
    ) -> None:
        if job_id is None:
            return
        try:
            from database.mysql_client import MySQLClient

            db = MySQLClient()
            with db.connect() as conn:
                with conn.cursor() as cursor:
                    db.finish_job(
                        cursor,
                        job_id,
                        status,
                        total_found=sum(int(page.get("解析商品数") or 0) for page in pages),
                        total_valid=sum(int(page.get("有效商品数") or 0) for page in pages),
                        total_inserted=0,
                        error_message=error_message,
                    )
        except Exception:
            logger.warning("更新爬取任务日志失败。", exc_info=True)

    def _try_create_import_job(self, keyword: str | None, files: list[Path]) -> int | None:
        try:
            from database.mysql_client import MySQLClient
            from services.task_center import IMPORT_JOB_URL_PREFIX

            project_root = Path(__file__).resolve().parents[1]
            rel_files: list[str] = []
            for file_path in files:
                try:
                    rel_files.append(file_path.relative_to(project_root).as_posix())
                except ValueError:
                    rel_files.append(file_path.as_posix())
            file_label = ";".join(rel_files[:8])
            if len(rel_files) > 8:
                file_label += f";...(+{len(rel_files) - 8})"
            db = MySQLClient()
            with db.connect() as conn:
                with conn.cursor() as cursor:
                    return db.create_job(
                        cursor,
                        keyword or self._infer_import_keyword(files),
                        f"{IMPORT_JOB_URL_PREFIX}{file_label}",
                        None,
                    )
        except Exception:
            logger.warning("创建入库任务日志失败。", exc_info=True)
            return None

    def _try_finish_import_job(
        self,
        job_id: int | None,
        status: str,
        total_found: int,
        total_valid: int,
        total_inserted: int,
        error_message: str | None,
    ) -> None:
        if job_id is None:
            return
        try:
            from database.mysql_client import MySQLClient

            db = MySQLClient()
            with db.connect() as conn:
                with conn.cursor() as cursor:
                    db.finish_job(
                        cursor,
                        job_id,
                        status,
                        total_found=total_found,
                        total_valid=total_valid,
                        total_inserted=total_inserted,
                        error_message=error_message,
                    )
        except Exception:
            logger.warning("更新入库任务日志失败。", exc_info=True)

    def _infer_import_keyword(self, files: list[Path]) -> str | None:
        if not files:
            return None
        project_html = Path(__file__).resolve().parents[1] / "html"
        try:
            rel = files[0].relative_to(project_html)
        except ValueError:
            return None
        return rel.parts[0] if len(rel.parts) > 1 else None

    def process_files(self, files: list[str], save_folder: str = "数据结果", merge_analysis: bool = False) -> None:
        """处理选中的文件"""
        if merge_analysis and len(files) > 1:
            # 合并分析
            self._merge_analysis(files, save_folder)
        else:
            # 分别分析每个文件
            for file_name in files:
                # 如果路径已经包含html/前缀，直接使用；否则添加html/前缀
                if file_name.startswith("html/"):
                    file_path = Path(file_name)
                else:
                    file_path = Path("html") / file_name

                if file_path.exists():
                    # 分析文件
                    parse_amazon_page(str(file_path))

                    # 移动结果到指定文件夹
                    self._move_results(save_folder)

    def preview_files_for_database(self, files: list[str], save_folder: str = "数据结果", keyword: str | None = None) -> dict:
        """Preview strict database candidates and export Chinese CSV files."""
        from services.ingestion import count_rejected_reasons, export_preview, parse_html_files

        html_files = [self._resolve_html_file(file_name) for file_name in files]
        valid_records, rejected_records = parse_html_files(html_files, keyword=keyword, require_complete=True)
        export_preview(valid_records, rejected_records, save_folder, prefix="database_candidates")
        return {
            "解析商品数": len(valid_records) + len(rejected_records),
            "有效入库候选": len(valid_records),
            "过滤商品数": len(rejected_records),
            "过滤原因": count_rejected_reasons(rejected_records),
        }

    def import_files_to_database(self, files: list[str], keyword: str | None = None) -> dict:
        """Import strict database candidates into MySQL."""
        from services.ingestion import ingest_html_files_to_mysql

        html_files = [self._resolve_html_file(file_name) for file_name in files]
        job_id = self._try_create_import_job(keyword, html_files)
        try:
            summary = ingest_html_files_to_mysql(html_files, keyword=keyword, require_complete=True)
        except Exception as exc:
            self._try_finish_import_job(job_id, "失败", 0, 0, 0, str(exc))
            raise
        self._try_finish_import_job(
            job_id,
            "完成",
            summary.total_found,
            summary.total_valid,
            summary.total_inserted,
            None,
        )
        return {
            "解析商品数": summary.total_found,
            "有效商品数": summary.total_valid,
            "过滤商品数": summary.total_rejected,
            "入库商品数": summary.total_inserted,
            "过滤原因": summary.rejected_reasons,
        }

    def sync_analytics_warehouse(self) -> dict:
        """Sync MySQL analysis data into the local DuckDB/Parquet warehouse."""
        from services.analytics_warehouse import sync_analytics_warehouse

        summary = sync_analytics_warehouse()
        return {
            "总行数": summary.total_rows,
            "DuckDB": str(summary.duckdb_path),
            "Parquet": str(summary.parquet_dir),
            "同步表": {table.name: table.rows for table in summary.tables},
        }

    def get_settings(self) -> dict:
        """Current user settings + schema + defaults (S3 设置页透出 services.settings)."""
        from services.settings import get_default_settings, get_settings_schema, load_settings_result

        result = load_settings_result()

        return {
            "settings": result.settings,
            "changes": [change.to_dict() for change in result.changes],
            "schema": get_settings_schema(),
            "defaults": get_default_settings(),
        }

    def update_settings(self, patch: dict) -> dict:
        """Deep-merge patch into settings, normalize + server-side clamp, persist.

        Returns {settings, changes}: changes 是被服务端钳制/回落的项（B 层硬边界等），
        供前端如实回报用户。C 层自定义评分只作单独层，不替换标准评分口径。
        """
        from services.settings import update_settings as _update

        return _update(patch).to_dict()

    def get_top_recommendations(self, limit: int = 50) -> list[dict]:
        """Fetch top product recommendations from MySQL."""
        from services.recommendations import fetch_top_recommendations

        return fetch_top_recommendations(limit=limit)

    def get_recommendations_page(
        self,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "total_score",
        sort_dir: str = "desc",
        min_score: float | None = None,
    ) -> dict:
        """Fetch paged product recommendations from MySQL."""
        from services.recommendations import fetch_recommendations_page

        return fetch_recommendations_page(
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            min_score=min_score,
        )

    def export_top_recommendations(self, save_folder: str = "数据结果", limit: int = 50) -> Path:
        """Export top product recommendations to a Chinese CSV file."""
        from services.recommendations import export_recommendations_csv

        return export_recommendations_csv(save_folder, limit=limit)

    def get_product_pool(
        self,
        limit: int = 100,
        keyword: str | None = None,
        keyword_exact: bool = False,
        min_score: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        max_reviews: int | None = None,
    ) -> list[dict]:
        """Fetch product pool rows from MySQL."""
        from services.product_pool import fetch_product_pool

        return fetch_product_pool(
            limit=limit,
            keyword=keyword,
            keyword_exact=keyword_exact,
            min_score=min_score,
            min_price=min_price,
            max_price=max_price,
            max_reviews=max_reviews,
        )

    def get_product_pool_page(
        self,
        limit: int = 100,
        offset: int = 0,
        keyword: str | None = None,
        keyword_exact: bool = False,
        min_score: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        max_reviews: int | None = None,
    ) -> dict:
        """Fetch paged product pool rows from MySQL."""
        from services.product_pool import fetch_product_pool_page

        return fetch_product_pool_page(
            limit=limit,
            offset=offset,
            keyword=keyword,
            keyword_exact=keyword_exact,
            min_score=min_score,
            min_price=min_price,
            max_price=max_price,
            max_reviews=max_reviews,
        )

    def get_product_history(self, asin: str) -> dict:
        """Fetch one product and its time-series snapshots from MySQL."""
        from services.product_pool import fetch_product_history
        from services.review_insights import fetch_product_review_insight

        detail = fetch_product_history(asin)
        detail["review_insight"] = fetch_product_review_insight(asin)
        return detail

    def get_product_advice(self, asin: str) -> dict:
        """Selection conclusion / risk / entry-strategy for one product (shared with GUI)."""
        from services.product_advice import entry_strategy, risk_text, selection_conclusion

        detail = self.get_product_history(asin)
        product = (detail.get("product") or {}) if isinstance(detail, dict) else {}
        snapshots = (detail.get("snapshots") or []) if isinstance(detail, dict) else []
        return {
            "conclusion": selection_conclusion(product, snapshots),
            "risk": risk_text(product, snapshots),
            "entry_strategy": entry_strategy(product, snapshots),
        }

    def get_task_jobs(self, limit: int = 100, status: str | None = None) -> list[dict]:
        """Fetch recent crawl/import task logs from MySQL."""
        from services.task_center import fetch_task_jobs

        return fetch_task_jobs(limit=limit, status=status)

    def get_keyword_opportunities(
        self,
        limit: int = 100,
        keyword: str | None = None,
        min_products: int | None = None,
    ) -> list[dict]:
        """Fetch keyword-level opportunity aggregates from MySQL."""
        from services.keyword_opportunities import fetch_keyword_opportunities

        return fetch_keyword_opportunities(limit=limit, keyword=keyword, min_products=min_products)

    def get_keyword_opportunities_page(
        self,
        limit: int = 100,
        offset: int = 0,
        keyword: str | None = None,
        min_products: int | None = None,
    ) -> dict:
        """Fetch paged keyword-level opportunity aggregates."""
        from services.keyword_opportunities import fetch_keyword_opportunities_page

        return fetch_keyword_opportunities_page(
            limit=limit,
            offset=offset,
            keyword=keyword,
            min_products=min_products,
        )

    def preview_review_import(self, file_path: str, default_asin: str | None = None) -> dict:
        """Preview local review CSV/JSON import without writing to MySQL."""
        from services.review_import import preview_reviews_from_file

        return self._review_import_summary_to_dict(preview_reviews_from_file(file_path, default_asin=default_asin))

    def import_review_file(self, file_path: str, default_asin: str | None = None) -> dict:
        """Import local review CSV/JSON and refresh review insights."""
        from services.review_import import import_reviews_from_file

        return self._review_import_summary_to_dict(import_reviews_from_file(file_path, default_asin=default_asin))

    def export_review_html(
        self,
        html_files: list[str],
        output_path: str | None = None,
        output_format: str = "csv",
        default_asin: str | None = None,
    ) -> dict:
        """Parse local Amazon review-page HTML and export import-ready CSV/JSON."""
        from services.review_html_export import export_review_html_files

        summary = export_review_html_files(
            html_files,
            output_path=output_path or None,
            output_format=output_format,
            default_asin=default_asin,
        )
        return {
            "解析评论数": summary.total_found,
            "有效评论数": summary.total_valid,
            "过滤评论数": summary.total_rejected,
            "输出文件": str(summary.output_path) if summary.output_path else "",
            "过滤明细": str(summary.rejected_output_path) if summary.rejected_output_path else "",
            "涉及 ASIN": sorted(summary.involved_asins),
            "过滤原因": summary.rejected_reasons,
        }

    def get_review_insights(self, limit: int = 100, keyword: str | None = None) -> list[dict]:
        """Fetch latest review insights across products."""
        from services.review_insights import fetch_review_insight_list

        return fetch_review_insight_list(limit=limit, keyword=keyword)

    def get_product_review_insight(self, asin: str) -> dict:
        """Fetch review insight and low-rating samples for one product."""
        from services.review_insights import fetch_product_review_insight

        return fetch_product_review_insight(asin)

    def _review_import_summary_to_dict(self, summary) -> dict:
        return {
            "解析评论数": summary.total_found,
            "有效评论数": summary.total_valid,
            "过滤评论数": summary.total_rejected,
            "写入/更新评论数": summary.total_upserted,
            "生成洞察商品数": summary.insights_generated,
            "涉及 ASIN": sorted(summary.involved_asins),
            "过滤原因": summary.rejected_reasons,
        }

    def _resolve_html_file(self, file_name: str) -> Path:
        project_root = Path(__file__).resolve().parents[1]
        path = Path(file_name)
        if path.exists():
            return path
        if file_name.startswith("html/") or file_name.startswith("html\\"):
            return project_root / path
        return project_root / "html" / file_name

    def _merge_analysis(self, files: list[str], save_folder: str = "数据结果") -> None:
        """合并分析多个HTML文件"""
        import pandas as pd
        import os
        import datetime
        from main import parse_amazon_page, calculate_blue_score

        # 获取运行开始的时间，精确到小时
        start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:00:00")

        # 收集所有文件的数据
        all_data = []
        columns = [
            "asin", "title", "link", "rating", "review_count",
            "price", "monthly_bought", "is_deal", "image_url", "rank", "time"
        ]

        # 从首个文件提取基础名称
        first_file = files[0]
        # 提取文件名部分（去除路径）
        file_name_only = os.path.basename(first_file)
        base_name = os.path.splitext(file_name_only)[0]

        # 分析每个文件并收集数据
        for file_name in files:
            # 如果路径已经包含html/前缀，直接使用；否则添加html/前缀
            if file_name.startswith("html/"):
                file_path = Path(file_name)
            else:
                file_path = Path("html") / file_name

            if file_path.exists():
                print(f"开始分析: {file_path}")

                # 读取HTML
                with open(file_path, "r", encoding="utf-8") as f:
                    html_content = f.read()

                # 解析HTML
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, "lxml")

                # 移除脚本和样式标签
                for script in soup(['script', 'style']):
                    script.decompose()

                # 移除广告和推荐部分
                for ad in soup.select('.s-sponsored-search-results'):
                    ad.decompose()

                for rec in soup.select('.s-result-item.s-asin.AdHolder'):
                    rec.decompose()

                # 选择商品项
                items = soup.select('div[data-component-type="s-search-result"]:not(.AdHolder)')
                carousel_items = soup.select('.a-carousel-card div[data-asin]')
                items.extend(carousel_items)

                # 去重
                seen_asins = set()
                unique_items = []
                for item in items:
                    asin = item.get('data-asin')
                    if asin and asin not in seen_asins:
                        seen_asins.add(asin)
                        unique_items.append(item)

                items = unique_items

                # 提取数据
                from main import parse_count, clean_price, extract_monthly_bought

                for item in items:
                    try:
                        # 提取ASIN
                        asin = item.get("data-asin")
                        if not asin:
                            continue

                        # 提取标题
                        title = item.select_one("h2 span")
                        title = title.text.strip() if title else None

                        # 提取商品链接
                        link = item.select_one("h2 a")
                        link = "https://www.amazon.com" + link["href"] if link else None

                        # 提取评分
                        rating_tag = item.select_one(".a-icon-alt")
                        rating = float(rating_tag.text.split(" ")[0]) if rating_tag else None

                        # 提取评论数
                        review_tag = item.select_one("span.a-size-mini")
                        review_count = parse_count(review_tag.text.strip("()")) if review_tag else None

                        # 提取价格
                        price_tag = item.select_one(".a-price .a-offscreen")
                        price = clean_price(price_tag.text) if price_tag else None

                        # 提取月销量
                        monthly_bought = extract_monthly_bought(item)

                        # 提取是否促销
                        is_deal = 1 if item.select_one(".a-badge-text") else 0

                        # 提取图片链接
                        image_tag = item.select_one(".s-image")
                        image_url = image_tag.get("src") if image_tag else None

                        # 提取搜索排名
                        rank = int(item.get("data-index")) if item.get("data-index") else None

                        # 添加到数据列表
                        all_data.append([asin, title, link, rating, review_count, price, monthly_bought, is_deal, image_url, rank, start_time])

                    except Exception as e:
                        print(f"处理商品时出错: {e}")
                        continue

        # 创建DataFrame
        df = pd.DataFrame(all_data, columns=columns)

        # 数据清洗：移除重复项
        df = df.drop_duplicates(subset=["asin"])

        # 计算蓝海评分
        df["blue_score"] = df.apply(calculate_blue_score, axis=1)

        # 保存原始数据
        raw_csv = f"{base_name}_raw.csv"
        try:
            df.to_csv(raw_csv, index=False, encoding="utf-8-sig")
        except PermissionError:
            import time
            timestamp = int(time.time())
            temp_filename = f"{base_name}_raw_{timestamp}.csv"
            df.to_csv(temp_filename, index=False, encoding="utf-8-sig")
            print(f"警告：文件被占用，已保存为 {temp_filename}")

        # 蓝海筛选
        blue_df = df[
            (df["monthly_bought"] > 1000) &
            (df["review_count"] < 500) &
            (df["rating"] >= 4.0) &
            (df["price"].between(10, 50))
        ]

        blue_df = blue_df.sort_values(by="blue_score", ascending=False)

        # 保存蓝海产品
        blue_csv = f"{base_name}_blue_products.csv"
        try:
            blue_df.to_csv(blue_csv, index=False, encoding="utf-8-sig")
        except PermissionError:
            import time
            timestamp = int(time.time())
            temp_filename = f"{base_name}_blue_products_{timestamp}.csv"
            blue_df.to_csv(temp_filename, index=False, encoding="utf-8-sig")
            print(f"警告：文件被占用，已保存为 {temp_filename}")

        # Excel输出
        excel_file = f"{base_name}_analysis.xlsx"
        try:
            with pd.ExcelWriter(excel_file) as writer:
                df.to_excel(writer, sheet_name="全部数据", index=False)
                blue_df.to_excel(writer, sheet_name="蓝海产品", index=False)
        except PermissionError:
            import time
            timestamp = int(time.time())
            temp_filename = f"{base_name}_analysis_{timestamp}.xlsx"
            with pd.ExcelWriter(temp_filename) as writer:
                df.to_excel(writer, sheet_name="全部数据", index=False)
                blue_df.to_excel(writer, sheet_name="蓝海产品", index=False)
            print(f"警告：文件被占用，已保存为 {temp_filename}")

        # 可视化
        import matplotlib.pyplot as plt
        import numpy as np

        plt.figure(figsize=(12, 8))

        # 散点图：竞争 vs 需求
        plt.subplot(2, 2, 1)
        plt.scatter(df["review_count"], df["monthly_bought"], alpha=0.5)
        plt.xlabel("Review Count (Competition)")
        plt.ylabel("Monthly Bought (Demand)")
        plt.title("Competition vs Demand")

        # 散点图：价格 vs 月销量
        plt.subplot(2, 2, 2)
        plt.scatter(df["price"], df["monthly_bought"], alpha=0.5)
        plt.xlabel("Price")
        plt.ylabel("Monthly Bought")
        plt.title("Price vs Monthly Bought")

        # 散点图：排名 vs 月销量
        plt.subplot(2, 2, 3)
        plt.scatter(df["rank"], df["monthly_bought"], alpha=0.5)
        plt.xlabel("Rank")
        plt.ylabel("Monthly Bought")
        plt.title("Rank vs Monthly Bought")

        # 散点图：评分 vs 月销量
        plt.subplot(2, 2, 4)
        plt.scatter(df["rating"], df["monthly_bought"], alpha=0.5)
        plt.xlabel("Rating")
        plt.ylabel("Monthly Bought")
        plt.title("Rating vs Monthly Bought")

        plt.tight_layout()
        plot_file = f"{base_name}_analysis_plots.png"
        try:
            plt.savefig(plot_file)
        except PermissionError:
            import time
            timestamp = int(time.time())
            temp_filename = f"{base_name}_analysis_plots_{timestamp}.png"
            plt.savefig(temp_filename)
            print(f"警告：文件被占用，已保存为 {temp_filename}")
        plt.close()

        print("✅ 合并分析完成！")
        print(f"总商品数: {len(df)}")
        print(f"蓝海产品数: {len(blue_df)}")
        print(f"数据字段: {list(df.columns)}")

        # 移动结果到指定文件夹
        self._move_results(save_folder)

    def _move_results(self, save_folder: str = "数据结果") -> None:
        """移动结果文件到指定文件夹"""
        data_dir = Path(save_folder)

        # 确保保存文件夹存在
        data_dir.mkdir(exist_ok=True)

        # 移动CSV文件
        for csv_file in Path(".").glob("*.csv"):
            if csv_file.exists():
                new_path = data_dir / csv_file.name
                if new_path.exists():
                    new_path.unlink()
                csv_file.rename(new_path)

        # 移动Excel文件
        for xlsx_file in Path(".").glob("*.xlsx"):
            if xlsx_file.exists():
                new_path = data_dir / xlsx_file.name
                if new_path.exists():
                    new_path.unlink()
                xlsx_file.rename(new_path)

        # 移动图片文件
        for png_file in Path(".").glob("*.png"):
            if png_file.exists():
                new_path = data_dir / png_file.name
                if new_path.exists():
                    new_path.unlink()
                png_file.rename(new_path)


def _safe_file_part(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return cleaned[:80] or "amazon_search"


def _collection_limits() -> dict[str, int]:
    from services.settings import get_collection_limits

    limits = get_collection_limits()
    return {
        "page_delay_min_seconds": limits.page_delay_min_seconds,
        "page_delay_max_seconds": limits.page_delay_max_seconds,
        "pages_per_keyword": limits.pages_per_keyword,
        "max_pages_per_keyword": limits.max_pages_per_keyword,
    }


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(number, maximum))
