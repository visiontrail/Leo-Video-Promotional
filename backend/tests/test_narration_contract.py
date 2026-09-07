import hashlib
import json
import wave
from backend.pipeline import composer, tts


def test_pocket_manifest_has_same_verified_source_contract_without_vibe_rewrites(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("Speaker 1: Qwen remains canonical in Pocket TTS.")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "tts_input_generated.wav"
    audio.write_bytes(b"pocket narration")
    canonical = tts._strip_speaker_labels(script.read_text())
    manifest = {
        "model": "pocket-tts-en",
        "pacing_policy": tts.NARRATION_PACING_POLICY,
        "synthesis_speed_ratio": tts.NARRATION_SYNTHESIS_SPEED_RATIO,
        "source_text_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "output_audio_sha256": tts._file_sha256(audio),
        "integrity": {"passed": True, "verified_source_coverage": 1.0},
    }
    (audio_dir / "tts_manifest.json").write_text(json.dumps(manifest))

    assert composer._narration_manifest_failures(
        script, audio, "pocket-tts-en"
    ) == []

    manifest["integrity"]["passed"] = False
    (audio_dir / "tts_manifest.json").write_text(json.dumps(manifest))
    failures = composer._narration_manifest_failures(script, audio, "pocket-tts-en")
    assert any("Kyutai Pocket TTS" in failure for failure in failures)


def test_vibevoice_manifest_also_binds_script_and_audio(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("A stable narration contract.", encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "tts_input_generated.wav"
    audio.write_bytes(b"generated narration")
    (audio_dir / "tts_manifest.json").write_text(
        json.dumps(
            {
                "model": "vibevoice-0.5b",
                "pacing_policy": tts.NARRATION_PACING_POLICY,
                "synthesis_speed_ratio": tts.NARRATION_SYNTHESIS_SPEED_RATIO,
                "source_text_sha256": hashlib.sha256(
                    script.read_text(encoding="utf-8").encode()
                ).hexdigest(),
                "output_audio_sha256": tts._file_sha256(audio),
            }
        ),
        encoding="utf-8",
    )

    assert composer._narration_manifest_failures(
        script, audio, "vibevoice-0.5b"
    ) == []
    script.write_text("Edited after audio generation.", encoding="utf-8")
    assert any(
        "script changed" in failure
        for failure in composer._narration_manifest_failures(
            script, audio, "vibevoice-0.5b"
        )
    )


def test_narration_manifest_rejects_retimed_or_unproven_speed(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("Natural narration owns the timeline.", encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "tts_input_generated.wav"
    audio.write_bytes(b"generated narration")
    base = {
        "model": "vibevoice-0.5b",
        "pacing_policy": tts.NARRATION_PACING_POLICY,
        "synthesis_speed_ratio": 1.0,
        "source_text_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "output_audio_sha256": tts._file_sha256(audio),
    }

    for speed_ratio in (None, 1.4):
        manifest = dict(base)
        if speed_ratio is None:
            manifest.pop("synthesis_speed_ratio")
        else:
            manifest["synthesis_speed_ratio"] = speed_ratio
        (audio_dir / "tts_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        failures = composer._narration_manifest_failures(
            script, audio, "vibevoice-0.5b"
        )

        assert any("natural 1.0x" in failure for failure in failures)


def test_narration_manifest_rejects_non_object_root(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("Narration", encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "audio.wav"
    audio.write_bytes(b"audio")
    (audio_dir / "tts_manifest.json").write_text("[]", encoding="utf-8")

    assert composer._narration_manifest_failures(
        script, audio, "orpheus-en"
    ) == ["narration integrity manifest must be a JSON object"]
