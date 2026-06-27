"""商品主图联网缓存（前端详情页用）。

详情页在商品名下方展示一张商品主图，方便看大致样貌。图片源是采集时存下的
Amazon 媒体 URL（`products.image_url`）。本模块只读用途，负责：

- 从 MySQL 取该 ASIN 的商品主图 URL；
- **仅允许 Amazon 媒体域名**（白名单，防 SSRF / 开放代理）；
- 首次联网下载后缓存到 `user_data_path('cache/product_images')`，之后命中本地
  缓存，避免重复联网、离线也能看。

缓存目录是纯派生数据，可随时删；不写任何业务表，不改采集 / 评分口径。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request
from urllib.parse import urlparse

from database.mysql_client import MySQLClient
from pkg_paths import user_data_path


# Amazon 商品图 CDN 域名白名单（精确或子域后缀匹配）。
_ALLOWED_HOST_SUFFIXES = (
    "media-amazon.com",
    "ssl-images-amazon.com",
    "images-amazon.com",
    "amazon.com",
)

_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_DEFAULT_EXT = ".jpg"
_MAX_BYTES = 8 * 1024 * 1024  # 单图上限 8MB，防异常大响应
_TIMEOUT_SECONDS = 12

_CONTENT_TYPE_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _cache_dir() -> Path:
    path = user_data_path("cache", "product_images")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_image_url(asin: str, *, client: MySQLClient | None = None) -> str | None:
    """取该 ASIN 的商品主图 URL（`products.image_url`）。"""
    asin = (asin or "").strip()
    if not asin:
        return None
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT image_url FROM products WHERE asin = %s LIMIT 1",
                (asin,),
            )
            row = cursor.fetchone()
    if not row:
        return None
    url = row.get("image_url") if isinstance(row, dict) else row[0]
    url = (url or "").strip()
    return url or None


def _is_allowed_host(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _ALLOWED_EXTS else _DEFAULT_EXT


def content_type_for(path: Path) -> str:
    return _CONTENT_TYPE_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _cache_path_for(url: str) -> Path:
    # 按 URL 哈希命名：image_url 一变就指向新缓存文件，自动避开陈旧图。
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{digest}{_ext_from_url(url)}"


def fetch_product_image(asin: str, *, client: MySQLClient | None = None) -> Path | None:
    """返回该 ASIN 主图的本地缓存路径；缺失则联网下载一次再缓存。

    返回 ``None`` 表示无 image_url、域名不在白名单、或下载失败——端点据此回 404，
    前端 onerror 隐藏图片区。
    """
    url = resolve_image_url(asin, client=client)
    if not url or not _is_allowed_host(url):
        return None
    path = _cache_path_for(url)
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            data = response.read(_MAX_BYTES + 1)
    except Exception:
        return None
    if not data or len(data) > _MAX_BYTES:
        return None
    # 先写临时文件再原子替换，避免并发/中断留下半截缓存。
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
