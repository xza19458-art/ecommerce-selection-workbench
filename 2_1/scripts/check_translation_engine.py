from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.translation import build_translator, ensure_argos_runtime_env, load_translation_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Check translation engine configuration and local availability.")
    parser.add_argument(
        "--sample",
        default="Leaked after two days and the zipper broke after one use.",
        help="Sample text to translate when an engine is available.",
    )
    args = parser.parse_args()

    config = load_translation_config()
    print("Translation engine check")
    print("=" * 32)
    print(f"enabled: {config.enabled}")
    print(f"engine: {config.engine}")
    print(f"source_lang: {config.source_lang}")
    print(f"target_lang: {config.target_lang}")
    print(f"use_cache: {config.use_cache}")

    if not config.enabled:
        print("status: disabled")
        return 0

    if config.engine.lower() == "argos":
        ensure_argos_runtime_env()
        available = importlib.util.find_spec("argostranslate") is not None
        print(f"argostranslate_installed: {available}")
        if not available:
            print("status: missing_dependency")
            return 1
        from argostranslate import translate

        languages = translate.get_installed_languages()
        print("installed_languages: " + ", ".join(f"{lang.code}:{lang.name}" for lang in languages))

    translator = build_translator(config)
    result = translator.translate_text(args.sample)
    print(f"translation_status: {result.status}")
    print(f"translation_engine: {result.engine}")
    print(f"source_lang_detected: {result.source_lang}")
    if result.translated_text:
        print(f"translated_text: {result.translated_text}")
    if result.error_message:
        print(f"error_message: {result.error_message}")
    return 0 if result.status in {"translated", "already_target", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
