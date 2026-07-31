import json
from pathlib import Path

from backend.pipeline import scene_kit, visual_plan


def board(n: int = 3) -> dict:
    return {
        "title": "Episode",
        "thesis": "A thesis sentence about the episode.",
        "scenes": [
            {
                "id": f"scene-{i + 1:02d}",
                "index": i,
                "start": 5.0 + i * 10,
                "duration": 10.0,
                "text": f"Sentence {i} opens the scene. It continues with more detail after that.",
                "keywords": ["sunflowers", "paris"],
                "lines": [],
            }
            for i in range(n)
        ],
    }


def test_fallback_plan_covers_every_scene_with_usable_direction():
    plans = visual_plan.fallback_plan(board(4))
    assert [p["id"] for p in plans] == ["scene-01", "scene-02", "scene-03", "scene-04"]
    for plan in plans:
        assert plan["headline"]
        assert plan["accent"] in scene_kit.ACCENTS
        assert plan["motif"] in scene_kit.MOTIFS


def test_fallback_headlines_end_on_a_clause_or_an_ellipsis():
    long_text = (
        "You haven't really been somewhere until you go back and realize it has become "
        "a completely different place while you were not looking."
    )
    headline = visual_plan._headline_from(long_text)
    assert len(headline) <= 68
    # Never leave the reader mid-thought without a signal.
    assert headline.endswith("…") or not long_text.startswith(headline + " and")


def test_consecutive_fallback_scenes_do_not_share_an_accent():
    plans = visual_plan.fallback_plan(board(6))
    accents = [p["accent"] for p in plans]
    assert all(a != b for a, b in zip(accents, accents[1:]))


def test_json_array_is_recovered_from_prose_wrapped_replies():
    reply = 'Here is the plan:\n```json\n[{"id": "scene-01"}]\n```\nHope that helps.'
    assert visual_plan._first_json_array(reply) == [{"id": "scene-01"}]

    bare = 'Thinking... [{"id": "scene-02"}] done'
    assert visual_plan._first_json_array(bare) == [{"id": "scene-02"}]

    wrapped = json.dumps({"scenes": [{"id": "scene-03"}]})
    assert visual_plan._first_json_array(wrapped) == [{"id": "scene-03"}]

    assert visual_plan._first_json_array("no json at all") is None


def test_normalise_rejects_spine_owned_archetypes_and_bad_enums():
    scene = board(1)["scenes"][0]
    plan = visual_plan._normalise(
        {"archetype": "title", "accent": "chartreuse", "motif": "spirograph"}, scene, 0
    )
    # title/outro/footage belong to the spine and the footage matcher.
    assert plan["archetype"] == "topic"
    assert plan["accent"] in scene_kit.ACCENTS
    assert plan["motif"] in scene_kit.MOTIFS


def test_normalise_fills_missing_copy_from_the_narration():
    scene = board(1)["scenes"][0]
    plan = visual_plan._normalise({}, scene, 0)
    assert plan["headline"]
    assert plan["kicker"]


def test_normalise_clamps_copy_to_what_fits_a_frame():
    scene = board(1)["scenes"][0]
    plan = visual_plan._normalise(
        {"headline": "h" * 400, "body": "b" * 900, "kicker": "k" * 90, "items": ["i" * 200] * 9},
        scene,
        0,
    )
    assert len(plan["headline"]) <= 110
    assert len(plan["body"]) <= 260
    assert len(plan["kicker"]) <= 30
    assert len(plan["items"]) <= 4


def test_footage_is_attached_to_the_scene_whose_keywords_match(tmp_path: Path):
    data = board(2)
    data["scenes"][0]["keywords"] = ["hotpot", "sichuan"]
    data["scenes"][1]["keywords"] = ["sunflowers", "fields"]
    plans = visual_plan.fallback_plan(data)

    clip_dir = tmp_path / "footage"
    clip_dir.mkdir()
    (clip_dir / "field.jpg").write_bytes(b"x")
    manifest = {
        "clips": [
            {
                "local_path": "footage/field.jpg",
                "query": "sunflower fields countryside",
                "title": "Sunflowers",
                "attribution": "CC BY-SA",
            }
        ]
    }

    assert visual_plan.attach_footage(plans, data, manifest, tmp_path) == 1
    assert plans[1]["archetype"] == "footage"
    assert plans[1]["footage_src"] == "footage/field.jpg"
    assert plans[1]["footage_kind"] == "image"
    assert plans[0]["archetype"] != "footage"


def test_footage_with_no_keyword_overlap_is_not_forced_onto_a_scene(tmp_path: Path):
    data = board(1)
    plans = visual_plan.fallback_plan(data)
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "x.webm").write_bytes(b"x")
    manifest = {"clips": [{"local_path": "footage/x.webm", "query": "unrelated subject matter"}]}
    assert visual_plan.attach_footage(plans, data, manifest, tmp_path) == 0
    assert plans[0]["archetype"] != "footage"


def test_missing_clip_files_are_skipped(tmp_path: Path):
    data = board(1)
    plans = visual_plan.fallback_plan(data)
    manifest = {"clips": [{"local_path": "footage/gone.jpg", "query": "sunflowers paris"}]}
    assert visual_plan.attach_footage(plans, data, manifest, tmp_path) == 0


def test_title_and_outro_plans_are_spine_owned():
    data = board(1)
    title = visual_plan.title_plan(data)
    outro = visual_plan.outro_plan(data, brand="Brand")
    assert title["id"] == visual_plan.TITLE_SCENE_ID
    assert title["archetype"] == "title"
    assert title["headline"] == "Episode"
    assert outro["archetype"] == "outro"
    assert outro["body"] == "Brand"
