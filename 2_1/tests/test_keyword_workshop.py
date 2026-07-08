from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_workshop import (  # noqa: E402
    SOURCE_SUGGEST,
    SOURCE_TITLE,
    _merge_existing_candidate,
    _normalize_run_row,
    _source_status_for_run_transition,
    build_expansion_queries,
    extract_title_ngrams,
    is_valid_keyword,
    normalize_keyword,
    normalize_seed_keywords,
    parse_suggestions,
    score_keyword_idea,
)


def test_normalize_and_filter_keywords() -> None:
    assert normalize_keyword("  Cow Squishy!!  ") == "cow squishy"
    assert normalize_seed_keywords("Squishy\nsquishy, Fidget Toys") == ["squishy", "fidget toys"]
    assert is_valid_keyword("cow squishy")
    assert not is_valid_keyword("12")
    assert not is_valid_keyword("https://www.amazon.com/dp/B000000000")
    assert not is_valid_keyword("squishy slow rising squishy")


def test_build_expansion_queries_uses_base_and_alpha_num_suffixes() -> None:
    queries = build_expansion_queries("Squishy", expand_alpha_num=True)

    assert queries[0] == "squishy"
    assert "squishy a" in queries
    assert "squishy z" in queries
    assert "squishy 9" in queries
    assert len(queries) == 37


def test_parse_suggestions_accepts_amazon_dict_shape() -> None:
    payload = {
        "suggestions": [
            {"value": "Cow Squishy"},
            {"value": "Mini Squishy"},
            {"value": "https://bad.example"},
            "Slow Rising Squishy",
        ]
    }

    assert parse_suggestions(payload) == ["cow squishy", "mini squishy", "slow rising squishy"]


def test_extract_title_ngrams_keeps_seed_related_phrases() -> None:
    rows = extract_title_ngrams(
        [
            "Cow Squishy Toy Slow Rising Stress Relief",
            "Mini Squishy Animals Mochi Squishy Pack",
            "Kitchen Gadget Stainless Steel",
        ],
        ["squishy"],
    )
    keywords = {row["keyword"]: row for row in rows}

    assert "cow squishy" in keywords
    assert "mini squishy" in keywords
    assert "kitchen gadget" not in keywords
    assert keywords["mini squishy"]["count"] == 1


def test_extract_title_ngrams_filters_cross_segment_and_weak_edge_noise() -> None:
    rows = extract_title_ngrams(
        [
            "Squishy Toys, Slow Rising Squishy, Stress Relief",
            "120 Pack Mochi Squishy Toys | Party Favors",
        ],
        ["squishy"],
    )
    keywords = {row["keyword"] for row in rows}

    assert "squishy toys" in keywords
    assert "slow rising squishy" in keywords
    assert "mochi squishy toys" in keywords
    assert "rising squishy" not in keywords
    assert "squishy toys slow" not in keywords
    assert "squishy slow rising squishy" not in keywords
    assert "pack mochi squishy toys" not in keywords


def test_extract_title_ngrams_filters_truncated_stress_and_broad_intent_fragments() -> None:
    rows = extract_title_ngrams(
        [
            "3Pcs Stress Cube Squishy Toys, Slow Rising Ice Cube Stress Balls, Sensory Fidget Toys for Anxiety Relief",
            "Stress Balls for Kids - 24 Pack Dough Squishy Fidget Toys for Anxiety Relief",
            "Fidget Toys for Stress Relief, Sensory Toys for Adults and Kids",
            "Stress Balls Set, Squishy Stress Ball, 4 Pack Squeeze Ball for Adults",
            "Stress Balls Fidget Toys, Fidget Stress Balls, Ball Squeeze Toys",
            "Animal Stress Ball Birthday Gifts, Ball Birthday Gifts for Kids",
        ],
        ["stress ball"],
        max_candidates=120,
    )
    keywords = {row["keyword"] for row in rows}

    assert "stress balls" in keywords
    assert "stress balls for kids" in keywords
    assert "squishy stress ball" in keywords
    assert "squeeze ball" in keywords
    assert "squeeze ball for adults" in keywords
    assert "fidget stress balls" in keywords
    assert "ball for adults" not in keywords
    assert "ball squeeze" not in keywords
    assert "ball squeeze toys" not in keywords
    assert "ball toys" not in keywords
    assert "ball birthday" not in keywords
    assert "ball birthday gifts" not in keywords
    assert "cube squishy stress" not in keywords
    assert "cube squishy stress balls" not in keywords
    assert "stress cube squishy" not in keywords
    assert "stress balls fidget" not in keywords
    assert "toys for stress" not in keywords
    assert "toys for stress relief" not in keywords
    assert "fidget toys for stress" not in keywords
    assert "squishy stress" not in keywords
    assert "3pcs stress" not in keywords
    assert "4 pack squeeze ball" not in keywords


def test_extract_title_ngrams_requires_specific_seed_token_for_generic_product_seed() -> None:
    rows = extract_title_ngrams(
        [
            "Squishy Fidget Toys for Kids, Sensory Fidget Toys Bulk",
            "Fidget Toys Adults, Soft Stretchy Fidget Toys",
            "Mochi Squishy Toys, Treasure Box Toys, Toys for Adults",
            "Party Favors Toys for Classroom Prizes",
            "Stress Relief Fidget Toys, Relief Fidget Toys",
            "Fidget Toys Squishy, Fidget Toys Colorful Mochi, Fidget Toys Goody Bag",
        ],
        ["fidget toys"],
        max_candidates=120,
    )
    keywords = {row["keyword"] for row in rows}

    assert "squishy fidget toys" in keywords
    assert "fidget toys for kids" in keywords
    assert "sensory fidget toys" in keywords
    assert "fidget toys adults" in keywords
    assert "soft stretchy fidget toys" in keywords
    assert "stress relief fidget toys" in keywords
    assert "mochi squishy toys" not in keywords
    assert "treasure box toys" not in keywords
    assert "toys for adults" not in keywords
    assert "toys for classroom" not in keywords
    assert "relief fidget toys" not in keywords
    assert "fidget toys squishy" not in keywords
    assert "fidget toys colorful mochi" not in keywords
    assert "fidget toys goody" not in keywords
    assert "fidget toys goody bag" not in keywords


def test_score_keyword_idea_separates_score_and_confidence() -> None:
    evidence = {
        "sources": {
            SOURCE_SUGGEST: {"best_rank": 2, "queries": ["squishy c"]},
            SOURCE_TITLE: {"count": 4, "examples": ["Cow Squishy Toy"]},
        },
        "occurrence_count": 5,
    }

    score = score_keyword_idea("cow squishy toy", {SOURCE_SUGGEST, SOURCE_TITLE}, evidence)

    assert score["idea_score"] >= 70
    assert score["confidence_score"] >= 50
    assert score["recommendation_level"] in {"优先验证", "可观察"}
    assert "创意分" in score["reason"]


def test_merge_existing_candidate_uses_current_evidence_strength_not_cumulative() -> None:
    existing = {
        "keyword": "squishy fidget toys",
        "normalized_keyword": "squishy fidget toys",
        "source_types": SOURCE_TITLE,
        "seed_keywords_json": '["fidget toys"]',
        "evidence_json": json.dumps(
            {
                "sources": {
                    SOURCE_TITLE: {"count": 120, "examples": ["old inflated evidence"]},
                },
                "seeds": ["fidget toys"],
                "occurrence_count": 120,
            },
            ensure_ascii=False,
        ),
        "occurrence_count": 120,
    }
    current = {
        "keyword": "squishy fidget toys",
        "normalized_keyword": "squishy fidget toys",
        "source_types": {SOURCE_TITLE, SOURCE_SUGGEST},
        "seed_keywords": {"fidget toys"},
        "evidence": {
            "sources": {
                SOURCE_TITLE: {"count": 36, "examples": ["new current evidence"]},
                SOURCE_SUGGEST: {"best_rank": 3, "queries": ["fidget toys"]},
            },
            "seeds": ["fidget toys"],
            "occurrence_count": 37,
        },
        "occurrence_count": 37,
    }

    merged = _merge_existing_candidate(existing, current)

    assert merged["evidence"]["sources"][SOURCE_TITLE]["count"] == 36
    assert "old inflated evidence" in merged["evidence"]["sources"][SOURCE_TITLE]["examples"]
    assert "new current evidence" in merged["evidence"]["sources"][SOURCE_TITLE]["examples"]
    assert merged["occurrence_count"] == 37
    assert merged["evidence"]["occurrence_count"] == 37


def test_run_status_transition_only_allows_safe_candidate_ignore_cycle() -> None:
    assert _source_status_for_run_transition("ignored") == "candidate"
    assert _source_status_for_run_transition("candidate") == "ignored"
    try:
        _source_status_for_run_transition("tracking")
    except ValueError as exc:
        assert "不影响已入库或追踪" in str(exc)
    else:
        raise AssertionError("tracking should not be a run-level status transition")


def test_normalize_run_row_decodes_json_and_counts() -> None:
    row = {
        "id": "8",
        "marketplace": "US",
        "seed_keywords_json": '["squishy", "fidget toys"]',
        "sources_json": '{"amazon_suggest": true, "title_ngram": false}',
        "total_found": "25",
        "total_saved": "12",
        "idea_count": "10",
        "candidate_count": "7",
        "ignored_count": "1",
        "promoted_count": "1",
        "tracking_count": "1",
        "created_at": "2026-07-03 10:00:00",
        "finished_at": None,
    }

    result = _normalize_run_row(row)

    assert result["id"] == 8
    assert result["seed_keywords"] == ["squishy", "fidget toys"]
    assert result["sources"] == {"amazon_suggest": True, "title_ngram": False}
    assert result["candidate_count"] == 7
    assert result["finished_at"] is None


if __name__ == "__main__":
    tests = [
        test_normalize_and_filter_keywords,
        test_build_expansion_queries_uses_base_and_alpha_num_suffixes,
        test_parse_suggestions_accepts_amazon_dict_shape,
        test_extract_title_ngrams_keeps_seed_related_phrases,
        test_extract_title_ngrams_filters_cross_segment_and_weak_edge_noise,
        test_extract_title_ngrams_filters_truncated_stress_and_broad_intent_fragments,
        test_extract_title_ngrams_requires_specific_seed_token_for_generic_product_seed,
        test_score_keyword_idea_separates_score_and_confidence,
        test_merge_existing_candidate_uses_current_evidence_strength_not_cumulative,
        test_run_status_transition_only_allows_safe_candidate_ignore_cycle,
        test_normalize_run_row_decodes_json_and_counts,
    ]
    for test in tests:
        test()
    print(f"keyword_workshop tests passed: {len(tests)}/{len(tests)}")
