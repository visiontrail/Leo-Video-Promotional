from backend.models import TaskConfig


def test_captions_are_off_by_default():
    assert TaskConfig().captions_enabled is False


def test_audio_review_is_skipped_by_default():
    assert TaskConfig().auto_render is True


def test_task_config_defaults_to_landscape_and_four_optional_collages():
    config = TaskConfig()

    assert config.video_orientation == "landscape"
    assert config.footage_orientation == "landscape"
    assert config.collage_broll_enabled is False
    assert config.collage_broll_count == 4
    assert config.opening_style == "editorial_motion"


def test_task_orientation_controls_legacy_footage_orientation():
    config = TaskConfig(video_orientation="portrait", footage_orientation="landscape")

    assert config.video_orientation == "portrait"
    assert config.footage_orientation == "portrait"


def test_legacy_only_portrait_orientation_is_promoted():
    config = TaskConfig(footage_orientation="portrait")

    assert config.video_orientation == "portrait"
    assert config.footage_orientation == "portrait"


def test_tts_uses_the_fast_model_by_default():
    assert TaskConfig().tts_model == "vibevoice-0.5b"
