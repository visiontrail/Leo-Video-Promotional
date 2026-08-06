from backend.models import TaskConfig


def test_captions_are_off_by_default():
    assert TaskConfig().captions_enabled is False


def test_audio_review_is_skipped_by_default():
    assert TaskConfig().auto_render is True


def test_tts_uses_the_fast_model_by_default():
    assert TaskConfig().tts_model == "vibevoice-0.5b"
