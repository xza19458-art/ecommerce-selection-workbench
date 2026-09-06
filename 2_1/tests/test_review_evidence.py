from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.review_insights import _review_evidence  # noqa: E402


def test_review_evidence_marks_untraceable_samples() -> None:
    result = _review_evidence({"imported_review_count": 3, "traceable_review_count": 0})

    assert result["status"] == "不可核验"
    assert result["traceable_rate"] == 0.0
    assert "不能作为正式选品证据" in result["message"]


def test_review_evidence_marks_partial_and_complete_sources() -> None:
    partial = _review_evidence({"imported_review_count": 4, "traceable_review_count": 2})
    complete = _review_evidence({"imported_review_count": 4, "traceable_review_count": 4})

    assert partial["status"] == "部分可追溯"
    assert partial["traceable_rate"] == 50.0
    assert complete["status"] == "可追溯"
    assert complete["traceable_rate"] == 100.0
