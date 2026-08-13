from backend import config
from backend.pipeline import composer


def test_narration_completeness_blocks_missing_script_audio(monkeypatch):
    monkeypatch.setattr(config, "AV_SYNC_MIN_WORD_COVERAGE_PERCENT", 65)
    failures = composer._narration_completeness_failures(
        {
            "method": "whisper_script_forced_alignment",
            "word_coverage": 0.20,
            "line_coverage": 0.35,
            "audio_coverage": 0.99,
        }
    )

    assert any("matched-word coverage" in failure for failure in failures)
    assert any("matched-line coverage" in failure for failure in failures)
    assert not any("transcript covers" in failure for failure in failures)


def test_narration_completeness_does_not_block_estimated_timing():
    assert composer._narration_completeness_failures(
        {
            "method": "word_count_estimate",
            "passed": False,
            "failure_reasons": ["word-level transcript unavailable"],
        }
    ) == []
