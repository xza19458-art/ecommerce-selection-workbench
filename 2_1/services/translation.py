"""Optional translation service for product and review text."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from services.translation_rules import (
    detect_language,
    is_target_language,
    protect_translation_terms,
    restore_translation_terms,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "translation.json"
ARGOS_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / ".argos"


def ensure_argos_runtime_env(root: Path = ARGOS_RUNTIME_ROOT) -> Path:
    """Keep Argos runtime files inside the project workspace by default."""
    root.mkdir(parents=True, exist_ok=True)
    defaults = {
        "XDG_DATA_HOME": root / "data",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_CACHE_HOME": root / "cache",
        "ARGOS_PACKAGES_DIR": root / "packages",
        "ARGOS_DEVICE_TYPE": "cpu",
    }
    for key, path in defaults.items():
        if key not in os.environ:
            os.environ[key] = str(path)
    for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "ARGOS_PACKAGES_DIR"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class TranslationConfig:
    enabled: bool = False
    engine: str = "argos"
    source_lang: str = "en"
    target_lang: str = "zh"
    batch_size: int = 20
    timeout_seconds: int = 30
    translate_products: bool = True
    translate_reviews: bool = True
    use_cache: bool = True

    @classmethod
    def from_file(cls, path: Path = CONFIG_PATH) -> "TranslationConfig":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            enabled=bool(data.get("enabled", False)),
            engine=str(data.get("engine", "argos")),
            source_lang=str(data.get("source_lang", "en")),
            target_lang=str(data.get("target_lang", "zh")),
            batch_size=int(data.get("batch_size", 20)),
            timeout_seconds=int(data.get("timeout_seconds", 30)),
            translate_products=bool(data.get("translate_products", True)),
            translate_reviews=bool(data.get("translate_reviews", True)),
            use_cache=bool(data.get("use_cache", True)),
        )


@dataclass(frozen=True)
class TranslationResult:
    source_text: str | None
    translated_text: str | None
    source_lang: str | None
    target_lang: str
    engine: str
    status: str
    translated_at: datetime | None = None
    error_message: str | None = None


class BaseTranslator:
    engine = "base"

    def __init__(self, config: TranslationConfig) -> None:
        self.config = config

    def translate_text(self, text: str | None) -> TranslationResult:
        raise NotImplementedError


class NullTranslator(BaseTranslator):
    engine = "none"

    def translate_text(self, text: str | None) -> TranslationResult:
        if text and text.strip() and is_target_language(text, self.config.target_lang):
            return TranslationResult(
                source_text=text,
                translated_text=text,
                source_lang=detect_language(text),
                target_lang=self.config.target_lang,
                engine=self.engine,
                status="already_target",
                translated_at=datetime.now(),
            )
        return _base_result(
            text,
            target_lang=self.config.target_lang,
            engine=self.engine,
            status="empty" if not text or not text.strip() else "skipped",
        )


class ArgosTranslator(BaseTranslator):
    engine = "argos"

    def __init__(self, config: TranslationConfig) -> None:
        super().__init__(config)
        self._translation: Any | None = None
        self._load_error: str | None = None
        self._load_translation()

    def translate_text(self, text: str | None) -> TranslationResult:
        if not text or not text.strip():
            return _base_result(text, target_lang=self.config.target_lang, engine=self.engine, status="empty")

        source_lang = detect_language(text)
        if is_target_language(text, self.config.target_lang):
            return TranslationResult(
                source_text=text,
                translated_text=text,
                source_lang=source_lang,
                target_lang=self.config.target_lang,
                engine=self.engine,
                status="already_target",
                translated_at=datetime.now(),
            )

        if self._translation is None:
            return TranslationResult(
                source_text=text,
                translated_text=None,
                source_lang=source_lang,
                target_lang=self.config.target_lang,
                engine=self.engine,
                status="failed",
                error_message=self._load_error or "Argos translation model is not available",
            )

        try:
            protected_text, replacements = protect_translation_terms(text)
            translated = self._translation.translate(protected_text)
            translated = restore_translation_terms(translated, replacements)
        except Exception as exc:  # noqa: BLE001 - translation must not block ingestion.
            return TranslationResult(
                source_text=text,
                translated_text=None,
                source_lang=source_lang,
                target_lang=self.config.target_lang,
                engine=self.engine,
                status="failed",
                error_message=str(exc),
            )

        return TranslationResult(
            source_text=text,
            translated_text=translated.strip() if translated else None,
            source_lang=source_lang,
            target_lang=self.config.target_lang,
            engine=self.engine,
            status="translated" if translated else "failed",
            translated_at=datetime.now() if translated else None,
        )

    def _load_translation(self) -> None:
        ensure_argos_runtime_env()
        try:
            from argostranslate import translate
        except ImportError as exc:
            self._load_error = "argostranslate is not installed"
            return

        try:
            source_code = _normalize_argos_lang(self.config.source_lang)
            target_code = _normalize_argos_lang(self.config.target_lang)
            languages = translate.get_installed_languages()
            source = next((lang for lang in languages if lang.code == source_code), None)
            target = next((lang for lang in languages if lang.code == target_code), None)
            if source is None or target is None:
                self._load_error = f"Argos model {source_code}->{target_code} is not installed"
                return
            self._translation = source.get_translation(target)
        except Exception as exc:  # noqa: BLE001 - store the error and let callers continue.
            self._load_error = str(exc)


def load_translation_config(path: Path = CONFIG_PATH) -> TranslationConfig:
    try:
        return TranslationConfig.from_file(path)
    except Exception:
        return TranslationConfig()


def build_translator(config: TranslationConfig | None = None) -> BaseTranslator:
    resolved = config or load_translation_config()
    if not resolved.enabled:
        return NullTranslator(resolved)
    if resolved.engine.lower() == "argos":
        return ArgosTranslator(resolved)
    return NullTranslator(resolved)


def _base_result(text: str | None, *, target_lang: str, engine: str, status: str) -> TranslationResult:
    return TranslationResult(
        source_text=text,
        translated_text=None,
        source_lang=detect_language(text),
        target_lang=target_lang,
        engine=engine,
        status=status,
    )


def _normalize_argos_lang(value: str) -> str:
    normalized = value.lower().replace("_", "-")
    if normalized in {"zh-cn", "zh-hans", "chinese"}:
        return "zh"
    if normalized in {"en-us", "en-gb", "english"}:
        return "en"
    return normalized
