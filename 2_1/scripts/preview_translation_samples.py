from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.translation_quality import collect_translation_samples, export_translation_samples_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Export product/review translation samples for quality review.")
    parser.add_argument("--products", type=int, default=20, help="Number of product titles to sample.")
    parser.add_argument("--reviews", type=int, default=3, help="Number of review rows to sample.")
    parser.add_argument("--products-only", action="store_true", help="Only sample product titles.")
    parser.add_argument("--reviews-only", action="store_true", help="Only sample review title/body.")
    parser.add_argument("--use-cache", action="store_true", help="Allow reading/writing translation_cache during preview.")
    parser.add_argument(
        "--output",
        default="translation_samples_preview.csv",
        help="CSV output path for quality review.",
    )
    args = parser.parse_args()

    samples = collect_translation_samples(
        product_limit=args.products,
        review_limit=args.reviews,
        include_products=not args.reviews_only,
        include_reviews=not args.products_only,
        use_cache=args.use_cache,
    )
    output = export_translation_samples_csv(samples, args.output)
    status_counts: dict[str, int] = {}
    for sample in samples:
        status_counts[sample.status] = status_counts.get(sample.status, 0) + 1

    print("Translation sample preview")
    print("=" * 32)
    print(f"samples: {len(samples)}")
    print(f"output: {output}")
    for status, count in sorted(status_counts.items()):
        print(f"{status}: {count}")


if __name__ == "__main__":
    main()
