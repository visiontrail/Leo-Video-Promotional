import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.pipeline import collage_broll
from backend.pipeline.video_format import LANDSCAPE, PORTRAIT


def _board(count: int = 6) -> dict:
    return {
        "thesis": "Systems preserve judgment",
        "scenes": [
            {
                "id": f"scene-{index + 1:02d}",
                "start": index * 6.0,
                "duration": 6.0,
                "text": f"Narration idea {index + 1} becomes a visible system.",
                "keywords": ["system", f"idea-{index + 1}"],
            }
            for index in range(count)
        ],
    }


def test_scene_selection_forces_opening_and_spreads_the_rest():
    selected = collage_broll._scene_choices(_board(), 4, True)

    assert len(selected) == 4
    assert selected[0]["id"] == "scene-01"
    assert len({scene["id"] for scene in selected}) == 4
    assert selected[-1]["id"] == "scene-06"


def test_prompts_follow_the_task_orientation_and_keep_media_clean():
    spec = collage_broll._fallback_spec(_board(1)["scenes"][0], 0)

    still = collage_broll.image_prompt(spec, LANDSCAPE)
    motion = collage_broll.video_prompt(spec, PORTRAIT)

    assert "16:9" in still
    assert "9:16" in motion
    assert "Image 1 is the exact empty first frame" in motion
    assert "No scene cuts, camera movement, zoom" in motion
    assert "Avoid all typography" in still


def test_agent_selects_beats_from_the_full_timeline():
    answer = json.dumps(
        [
            {"scene_id": "scene-03", "visual_metaphor": "a hinge reveals a hidden system"},
            {"scene_id": "scene-06", "visual_metaphor": "a bridge locks its final span"},
        ]
    )
    with (
        patch(
            "backend.pipeline.digester._resolve_provider",
            AsyncMock(return_value=("https://example.test", "model", "key")),
        ),
        patch("backend.pipeline.agent.agent_complete", AsyncMock(return_value=answer)),
    ):
        specs = asyncio.run(
            collage_broll.plan_specs(
                _board(), count=2, force_opening=False, frame=LANDSCAPE
            )
        )

    assert [spec["scene_id"] for spec in specs] == ["scene-03", "scene-06"]
    assert all(spec["planner"] == "claude_agent_sdk" for spec in specs)


def test_attach_collage_only_promotes_ready_existing_clips(tmp_path: Path):
    clip = tmp_path / "collage_broll" / "01-scene-01" / "video" / "final-5s-noaudio.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"video")
    plans = [
        {"id": "scene-01", "archetype": "topic", "headline": "First"},
        {"id": "scene-02", "archetype": "topic", "headline": "Second"},
    ]
    manifest = {
        "items": [
            {
                "scene_id": "scene-01",
                "status": "ready",
                "video_path": str(clip.relative_to(tmp_path)),
                "spec": {"visual_metaphor": "a machine snaps together"},
                "qa": {"passed": True},
            },
            {
                "scene_id": "scene-02",
                "status": "failed",
                "video_path": "missing.mp4",
            },
        ]
    }

    assert collage_broll.attach_collage(plans, manifest, tmp_path) == 1
    assert plans[0]["archetype"] == "footage"
    assert plans[0]["collage_broll"] is True
    assert plans[0]["footage_src"] == f"../{clip.relative_to(tmp_path).as_posix()}"
    assert plans[0]["footage_credit"] == ""
    assert plans[1]["archetype"] == "topic"


def test_generate_records_ready_and_failed_items_without_stopping(tmp_path: Path):
    specs = [
        collage_broll._fallback_spec(scene, index)
        for index, scene in enumerate(_board(2)["scenes"])
    ]
    still = tmp_path / "source.png"
    still.write_bytes(b"png")

    async def frames(_source, item_dir, _color, _frame):
        frame_dir = item_dir / "frames"
        frame_dir.mkdir(parents=True)
        first = frame_dir / "first-frame.png"
        last = frame_dir / "last-frame.png"
        first.write_bytes(b"first")
        last.write_bytes(b"last")
        return first, last

    calls = 0

    async def video(_prompt, _first, _last, item_dir, _frame):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("quota exhausted")
        raw = item_dir / "video" / "raw.mp4"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"raw")
        return raw, "https://gemini.google.com/videos/test"

    async def normalize(_raw, item_dir, _frame):
        final = item_dir / "video" / "final-5s-noaudio.mp4"
        final.write_bytes(b"final")
        return final

    async def sheet(_video, item_dir, _frame):
        output = item_dir / "video" / "contact-sheet.jpg"
        output.write_bytes(b"sheet")
        return output

    with (
        patch.object(collage_broll, "plan_specs", AsyncMock(return_value=specs)),
        patch.object(collage_broll, "_generate_still", AsyncMock(return_value=(still, "https://chatgpt.com/c/test"))),
        patch.object(collage_broll, "_prepare_frames", frames),
        patch.object(collage_broll, "_generate_video", video),
        patch.object(collage_broll, "_normalize_video", normalize),
        patch.object(collage_broll, "probe_video", AsyncMock(return_value={"passed": True})),
        patch.object(collage_broll, "_contact_sheet", sheet),
    ):
        manifest = asyncio.run(
            collage_broll.generate_collage_broll(
                _board(2), tmp_path, count=2, force_opening=False, frame=LANDSCAPE
            )
        )

    assert manifest["status"] == "partial"
    assert manifest["ready_count"] == 1
    assert manifest["gemini_api_key_used"] is False
    assert manifest["approval_gates"] == []
    assert [item["status"] for item in manifest["items"]] == ["ready", "failed"]
    saved = json.loads((tmp_path / "collage_broll" / "manifest.json").read_text())
    assert saved["errors"][0]["message"] == "quota exhausted"
