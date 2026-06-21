from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.translation_backfill import backfill_translations


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Chinese translation fields for products and reviews.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum products and maximum reviews to scan per run.")
    parser.add_argument("--products-only", action="store_true", help="Only backfill product titles.")
    parser.add_argument("--reviews-only", action="store_true", help="Only backfill review title/body.")
    parser.add_argument("--dry-run", action="store_true", help="Preview candidate rows without updating MySQL.")
    args = parser.parse_args()

    include_products = not args.reviews_only
    include_reviews = not args.products_only
    summary = backfill_translations(
        limit=args.limit,
        include_products=include_products,
        include_reviews=include_reviews,
        dry_run=args.dry_run,
    )

    print("Translation backfill summary")
    print("=" * 32)
    print(f"dry_run: {summary.dry_run}")
    print(f"products_checked: {summary.products_checked}")
    print(f"products_updated: {summary.products_updated}")
    print(f"reviews_checked: {summary.reviews_checked}")
    print(f"reviews_updated: {summary.reviews_updated}")
    print(f"translated: {summary.translated}")
    print(f"already_target: {summary.already_target}")
    print(f"skipped: {summary.skipped}")
    print(f"failed: {summary.failed}")
    print(f"migration_needed: {summary.migration_needed}")


if __name__ == "__main__":
    main()
