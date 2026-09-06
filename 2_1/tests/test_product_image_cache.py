from __future__ import annotations

from pathlib import Path
import sys
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.product_image_cache import (  # noqa: E402
    _AllowedImageRedirectHandler,
    _is_allowed_host,
    _looks_like_image,
)


def test_image_host_allowlist_excludes_general_amazon_pages() -> None:
    assert _is_allowed_host("https://m.media-amazon.com/images/I/example.jpg") is True
    assert _is_allowed_host("https://images-na.ssl-images-amazon.com/images/I/example.jpg") is True
    assert _is_allowed_host("https://www.amazon.com/dp/B000000001") is False
    assert _is_allowed_host("http://127.0.0.1/image.jpg") is False


def test_image_redirect_handler_blocks_non_media_target() -> None:
    handler = _AllowedImageRedirectHandler()

    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/private")


def test_image_signature_detection_rejects_html() -> None:
    assert _looks_like_image(b"\xff\xd8\xff\x00") is True
    assert _looks_like_image(b"\x89PNG\r\n\x1a\nrest") is True
    assert _looks_like_image(b"<html>not an image</html>") is False
