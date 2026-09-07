from backend.pipeline import composer, visual_plan
from backend.pipeline.video_format import PORTRAIT


def test_mount_list_starts_with_content_and_has_no_title_card():
    board = {
        "content_start": 0.0,
        "outro_start": 12.0,
        "outro_duration": 5.0,
        "scenes": [
            {"id": "scene-01", "start": 0.0, "duration": 7.0},
            {"id": "scene-02", "start": 7.0, "duration": 5.0},
        ],
    }

    mounts = composer._mount_list(board)

    assert mounts[0] == {"id": "scene-01", "start": 0.0, "duration": 7.0}
    assert [mount["id"] for mount in mounts] == [
        "scene-01",
        "scene-02",
        visual_plan.OUTRO_SCENE_ID,
    ]


def test_quality_failures_become_final_delivery_warnings():
    warnings = composer._quality_warnings(
        {
            "passed": False,
            "failure_reasons": ["word-level transcript unavailable"],
            "transcription_failures": ["mlx-whisper attempt 3: timeout"],
        },
        {
            "passed": False,
            "scenes": [
                {"id": "scene-01", "grounded": True},
                {"id": "scene-02", "grounded": False},
            ],
        },
        {
            "status": "failed",
            "passed": False,
            "failed_scene_ids": ["scene-03"],
            "errors": ["batch 1: malformed response"],
        },
    )

    assert len(warnings) == 3
    assert "estimated timing was used" in warnings[0]
    assert "scene-02" in warnings[1]
    assert "scene-03" in warnings[2]
    assert "malformed response" in warnings[2]


def test_clean_quality_report_has_no_warnings():
    assert composer._quality_warnings(
        {"passed": True},
        {"passed": True, "scenes": []},
        {"status": "passed", "passed": True},
    ) == []


def test_failed_review_completes_delivery_with_warning():
    report = composer._finalize_quality_report(
        {},
        {"passed": True},
        {"passed": True, "scenes": []},
        {
            "status": "failed",
            "passed": False,
            "average_score": 81.42,
            "failed_scene_ids": [],
            "errors": [],
        },
        multimodal_enabled=True,
    )

    assert report["passed"] is False
    assert report["quality_status"] == "warning"
    assert report["delivery_status"] == "completed_with_warnings"
    assert "81.42" in report["warnings"][0]


def test_portrait_render_command_uses_task_resolution(tmp_path):
    command = composer._build_render_command(tmp_path, tmp_path / "video.mp4", PORTRAIT)

    assert command[command.index("--resolution") + 1] == "portrait"


def test_render_command_allows_long_browser_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(composer.config, "RENDER_PROTOCOL_TIMEOUT_MS", 1_800_000)

    command = composer._build_render_command(
        tmp_path,
        tmp_path / "video.mp4",
        protocol_timeout_ms=11_199_000,
    )

    assert command[command.index("--protocol-timeout") + 1] == "11199000"


def test_render_timeouts_stay_ordered_for_long_video(monkeypatch):
    monkeypatch.setattr(composer.config, "RENDER_PROTOCOL_TIMEOUT_MS", 300_000)

    total, protocol_ms, stall = composer._render_timeouts(7_346)

    assert (total, protocol_ms, stall) == (11_319, 300_000, 11_259)
    assert protocol_ms // 1000 < stall < total


def test_render_timeouts_honor_configured_protocol_floor(monkeypatch):
    monkeypatch.setattr(composer.config, "RENDER_PROTOCOL_TIMEOUT_MS", 1_800_000)

    total, protocol_ms, stall = composer._render_timeouts(1)

    assert (total, protocol_ms, stall) == (1_920, 1_800_000, 1_860)


def test_long_render_does_not_expand_one_cdp_operation(monkeypatch):
    monkeypatch.setattr(composer.config, "RENDER_PROTOCOL_TIMEOUT_MS", 300_000)

    short = composer._render_timeouts(1)
    long = composer._render_timeouts(20_000)

    assert short[1] == long[1] == 300_000
    assert long[0] > short[0]
    assert long[2] > short[2]


def test_clean_initial_pixel_matches_and_exact_grounding_end_calibration_loop():
    initial_reviews = [
        {
            "id": "scene-01",
            "passed": True,
            "rubric_consistent": True,
            "gemini_verdict": "match",
            "issues": [],
            "score": 76,
        },
        {
            "id": "scene-02",
            "passed": True,
            "rubric_consistent": True,
            "gemini_verdict": "match",
            "issues": [],
            "score": 74,
        },
    ]
    calibrated_reviews = [
        {"id": "scene-01", "passed": False, "score": 78, "issues": ["Add polish."]},
        {"id": "scene-02", "passed": False, "score": 77, "issues": ["Add polish."]},
    ]
    multimodal = {
        "status": "failed",
        "passed": False,
        "scene_count": 2,
        "average_score": 77.5,
        "failed_scene_ids": ["scene-01", "scene-02"],
        "errors": [],
        "batches": [
            {
                "image_received": True,
                "structure_valid": True,
                "contract_valid": True,
            }
        ],
        "calibration": {
            "attempted": True,
            "fallback_to_initial": False,
            "initial_average_score": 75.0,
            "initial_scenes": initial_reviews,
        },
        "scenes": calibrated_reviews,
    }

    report = composer._finalize_quality_report(
        {},
        {"passed": True},
        {
            "passed": True,
            "scenes": [
                {"id": "scene-01", "grounded": True},
                {"id": "scene-02", "grounded": True},
            ],
        },
        multimodal,
        multimodal_enabled=True,
    )

    assert report["passed"] is True
    assert report["quality_status"] == "passed"
    assert report["multimodal"]["release_basis"] == (
        "clean_initial_matches_plus_deterministic_grounding"
    )
    assert report["multimodal"]["average_score"] == 75.0
    assert report["multimodal"]["failed_scene_ids"] == []
    assert report["multimodal"]["scenes"] == initial_reviews
    assert report["multimodal"]["calibration"]["advisory_scenes"] == calibrated_reviews


def test_calibration_disagreement_still_fails_without_clean_initial_evidence():
    multimodal = {
        "status": "failed",
        "passed": False,
        "scene_count": 1,
        "average_score": 76,
        "failed_scene_ids": ["scene-01"],
        "errors": [],
        "batches": [
            {
                "image_received": True,
                "structure_valid": True,
                "contract_valid": True,
            }
        ],
        "calibration": {
            "attempted": True,
            "fallback_to_initial": False,
            "initial_average_score": 72,
            "initial_scenes": [
                {
                    "id": "scene-01",
                    "passed": False,
                    "rubric_consistent": True,
                    "gemini_verdict": "partial",
                    "issues": ["The subject is missing."],
                }
            ],
        },
        "scenes": [{"id": "scene-01", "passed": False, "score": 76}],
    }

    report = composer._finalize_quality_report(
        {},
        {"passed": True},
        {"passed": True, "scenes": [{"id": "scene-01", "grounded": True}]},
        multimodal,
        multimodal_enabled=True,
    )

    assert report["passed"] is False
    assert report["quality_status"] == "warning"


def test_clean_initial_pixel_matches_still_fail_without_exact_grounding():
    multimodal = {
        "status": "failed",
        "passed": False,
        "scene_count": 1,
        "average_score": 78,
        "failed_scene_ids": ["scene-01"],
        "errors": [],
        "batches": [
            {
                "image_received": True,
                "structure_valid": True,
                "contract_valid": True,
            }
        ],
        "calibration": {
            "attempted": True,
            "fallback_to_initial": False,
            "initial_average_score": 76,
            "initial_scenes": [
                {
                    "id": "scene-01",
                    "passed": True,
                    "rubric_consistent": True,
                    "gemini_verdict": "match",
                    "issues": [],
                }
            ],
        },
        "scenes": [{"id": "scene-01", "passed": False, "score": 78}],
    }

    report = composer._finalize_quality_report(
        {},
        {"passed": True},
        {"passed": False, "scenes": [{"id": "scene-01", "grounded": False}]},
        multimodal,
        multimodal_enabled=True,
    )

    assert report["passed"] is False
    assert report["quality_status"] == "warning"
