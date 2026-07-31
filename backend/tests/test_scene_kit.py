import re

import pytest

from backend.pipeline import scene_kit as sk
from backend.pipeline.director import validate_scene_html


def plan(**kwargs) -> sk.ScenePlan:
    base = {"id": "scene-01", "duration": 12.0, "headline": "A headline that reads well"}
    base.update(kwargs)
    return sk.ScenePlan(**base)


@pytest.mark.parametrize("archetype", ["title", "statement", "topic", "outro"])
def test_text_archetypes_satisfy_the_runtime_contract(archetype):
    html = sk.render_scene(plan(archetype=archetype, kicker="KICK", body="Some support copy."))
    assert validate_scene_html(html, "scene-01") == []


def test_contrast_and_list_render_when_given_their_content():
    contrast = sk.render_scene(
        plan(archetype="contrast", left_label="Then", left_text="A", right_label="Now", right_text="B")
    )
    assert validate_scene_html(contrast, "scene-01") == []
    assert "Then" in contrast and "Now" in contrast

    listed = sk.render_scene(plan(archetype="list", items=("One", "Two", "Three")))
    assert validate_scene_html(listed, "scene-01") == []
    assert "Three" in listed


def test_archetypes_degrade_when_their_required_content_is_missing():
    # A contrast with only one side would render an empty panel; fall back instead.
    assert 'class="cols"' not in sk.render_scene(plan(archetype="contrast", left_text="only one"))
    assert 'class="rows"' not in sk.render_scene(plan(archetype="list", items=("just one",)))
    assert 'class="frame"' not in sk.render_scene(plan(archetype="footage"))


def test_scene_root_does_not_declare_its_own_timing():
    # The spine owns scene timing; a scene declaring data-start would double-schedule.
    html = sk.render_scene(plan(archetype="topic"))
    root = re.search(r"<div id=\"scene-01\"[^>]*>", html).group(0)
    assert "data-start" not in root
    assert "data-track-index" not in root
    assert 'data-width="1920"' in root and 'data-height="1080"' in root


def test_every_selector_is_scoped_to_the_scene():
    html = sk.render_scene(plan(archetype="topic", kicker="K", body="B"))
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    selectors = [
        line.split("{")[0].strip()
        for line in style.splitlines()
        if "{" in line and not line.strip().startswith(("/*", "*"))
    ]
    assert selectors, "expected scoped rules"
    assert all(sel.startswith("#scene-01") for sel in selectors), selectors


def test_motifs_are_deterministic_and_seeded_per_scene():
    first = sk.render_motif("sunburst", "amber", "scene-01")
    assert first == sk.render_motif("sunburst", "amber", "scene-01")
    assert first != sk.render_motif("sunburst", "amber", "scene-02")
    assert sk.render_motif("none", "amber", "scene-01") == ""
    assert sk.render_motif("not-a-motif", "amber", "scene-01") == ""


@pytest.mark.parametrize("motif", [m for m in sk.MOTIFS if m != "none"])
def test_every_motif_produces_svg(motif):
    svg = sk.render_motif(motif, "teal", "seed")
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_headline_size_shrinks_as_copy_grows():
    sizes = [sk.headline_size("x" * n) for n in (10, 30, 50, 80, 200)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] >= 56


def test_theme_selection_changes_surface_colours():
    dark = sk.render_scene(plan(theme=sk.THEMES["podcast"]))
    light = sk.render_scene(plan(theme=sk.THEMES["swiss"]))
    assert sk.THEMES["swiss"].bg in light
    assert sk.THEMES["swiss"].bg not in dark
    assert sk.resolve_theme("nope") is sk.DEFAULT_THEME


def test_copy_is_html_escaped():
    html = sk.render_scene(plan(headline='Rats & <script>alert("x")</script>'))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_footage_scene_references_its_asset_relatively():
    html = sk.render_scene(
        plan(archetype="footage", footage_src="footage/paris.jpg", footage_credit="CC BY")
    )
    assert validate_scene_html(html, "scene-01") == []
    assert 'src="footage/paris.jpg"' in html
    assert "CC BY" in html
