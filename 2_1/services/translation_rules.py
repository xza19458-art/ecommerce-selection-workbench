"""Text classification and protection helpers for translation."""

from __future__ import annotations

import re


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z]")

PROTECTED_TOKEN_RE = re.compile(
    r"(?x)"
    r"("
    r"\b(?=[A-Z0-9]{10}\b)(?=.*\d)[A-Z0-9]{10}\b"
    r"|\b(?i:USB-C|USB|BPA-Free|LED|PVC|ABS|HDMI|Wi[- ]?Fi|Bluetooth|IPX\d|FCC|FDA)\b"
    r"|\b(?i:iPhone|iPad|MacBook|Apple Watch|Nintendo Switch|Kindle)\b(?:\s+\d+)?(?:\s+(?i:Pro|Max|Plus|Mini))*"
    r"|\b[A-Z]{1,6}[-_]?\d+[A-Z0-9_-]*\b"
    r"|\b\d+(?:\.\d+)?\s?(?i:oz|fl oz|lb|lbs|g|kg|ml|l|inch|inches|in|ft|cm|mm|count|ct|pcs?|pack|packs)\b"
    r"|\b(?i:Pack)\s+(?i:of)\s+\d+\b"
    r"|\b\d+\s?[-x]\s?\d+\b"
    r")"
)


def detect_language(text: str | None) -> str | None:
    """Return a small language label useful for storage decisions."""
    if not text or not text.strip():
        return None
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact:
        return None
    cjk_count = len(CJK_RE.findall(compact))
    latin_count = len(LATIN_RE.findall(compact))
    if cjk_count and cjk_count / len(compact) >= 0.2:
        return "zh"
    if latin_count and latin_count >= cjk_count:
        return "en"
    return "unknown"


def is_target_language(text: str | None, target_lang: str) -> bool:
    lang = detect_language(text)
    if not lang:
        return False
    if target_lang.lower().startswith("zh"):
        return lang == "zh"
    return lang == target_lang.lower()


def protect_translation_terms(text: str) -> tuple[str, dict[str, str]]:
    """Mask product identifiers and specs before machine translation."""
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        placeholder = f"__KEEP_{len(replacements)}__"
        replacements[placeholder] = token
        return placeholder

    return PROTECTED_TOKEN_RE.sub(replace, text), replacements


def restore_translation_terms(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for placeholder, token in replacements.items():
        restored = restored.replace(placeholder, token)
        index_match = re.search(r"\d+", placeholder)
        if not index_match:
            continue
        index = index_match.group(0)
        restored = re.sub(rf"(?i)__\s*KEEP\s*[_\s-]*{index}\s*__", token, restored)
        restored = re.sub(rf"(?i)\bKEEP\s*[_\s-]*{index}\b(?:\s*\[[^\]]+\])?", token, restored)
    return restored
