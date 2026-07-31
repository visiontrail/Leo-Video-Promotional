import re

from backend.pipeline import assembler, scene_kit


def board(scenes: list[dict], *, audio_duration: float = 60.0) -> dict:
    return {
        "title": "T",
        "audio_duration": audio_duration,
        "content_start": 5.0,
        "outro_start": 5.0 + audio_duration,
        "outro_duration": 5.0,
        "total_duration": 10.0 + audio_duration,
        "scenes": scenes,
    }


def scene(id_: str, start: float, duration: float, lines: list[dict]) -> dict:
    return {"id": id_, "start": start, "duration": duration, "lines": lines, "text": " "}


def test_spine_declares_the_runtime_contract_the_old_template_was_missing():
    data = board([scene("scene-01", 5.0, 55.0, [{"start": 5.0, "duration": 5.0, "text": "hi", "speaker": 1}])])
    html = assembler.build_spine(
        data,
        audio_src="audio/narration.wav",
        mounts=[{"id": "scene-01", "start": 5.0, "duration": 55.0}],
    )
    root = re.search(r'<div id="root"[^>]*>', html).group(0)
    assert 'data-start="0"' in root
    assert 'data-composition-id="root"' in root
    assert re.search(r'window\.__timelines\[\s*"root"\s*\]\s*=', html)
    assert "vendor/gsap.min.js" in html
    # Every timed element must be a .clip or the runtime never hides it.
    for tag in re.findall(r"<div [^>]*data-start=[^>]*>", html):
        if 'id="root"' in tag:
            continue
        assert 'class="clip' in tag, tag


def test_spine_has_no_remote_asset_references():
    data = board([scene("scene-01", 5.0, 55.0, [])])
    html = assembler.build_spine(
        data, audio_src="audio/a.wav", mounts=[{"id": "scene-01", "start": 5.0, "duration": 55.0}]
    )
    assert "https://" not in html and "http://" not in html


def test_adjacent_scenes_alternate_tracks_so_boundaries_cannot_overlap():
    mounts = [
        {"id": "scene-01", "start": 0.0, "duration": 10.0},
        {"id": "scene-02", "start": 10.0, "duration": 10.0},
        {"id": "scene-03", "start": 20.0, "duration": 10.0},
    ]
    html = assembler.build_spine(board([]), audio_src="a.wav", mounts=mounts)
    tracks = re.findall(r'data-track-index="(\d)"></div>', html)
    assert tracks[:3] == [
        str(assembler.TRACK_SCENE_A),
        str(assembler.TRACK_SCENE_B),
        str(assembler.TRACK_SCENE_A),
    ]


def test_captions_are_clamped_so_they_never_overlap_on_their_track():
    # Silence-map rounding routinely produces a caption that ends a hundredth of
    # a second after the next one starts, which lint reports as an overlap.
    lines = [
        {"start": 0.0, "duration": 5.02, "text": "first", "speaker": 1},
        {"start": 5.0, "duration": 4.0, "text": "second", "speaker": 1},
    ]
    html = assembler.build_spine(
        board([scene("scene-01", 0.0, 9.0, lines)]),
        audio_src="a.wav",
        mounts=[{"id": "scene-01", "start": 0.0, "duration": 9.0}],
    )
    caps = re.findall(r'id="cap-\d+"[^>]*data-start="([\d.]+)" data-duration="([\d.]+)"', html)
    assert len(caps) == 2
    first_end = float(caps[0][0]) + float(caps[0][1])
    assert first_end <= float(caps[1][0])


def test_captions_are_escaped():
    lines = [{"start": 0.0, "duration": 2.0, "text": '<b>x</b> & "y"', "speaker": 1}]
    html = assembler.build_spine(
        board([scene("scene-01", 0.0, 2.0, lines)]),
        audio_src="a.wav",
        mounts=[{"id": "scene-01", "start": 0.0, "duration": 2.0}],
    )
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;" in html


def test_audio_starts_after_the_title_card():
    data = board([], audio_duration=100.0)
    html = assembler.build_spine(data, audio_src="audio/n.wav", mounts=[])
    audio = re.search(r"<audio[^>]*>", html).group(0)
    assert 'data-start="5.0"' in audio
    assert 'data-duration="100.0"' in audio
    assert 'src="audio/n.wav"' in audio


def test_write_scene_files_creates_one_file_per_plan(tmp_path):
    plans = [
        scene_kit.ScenePlan(id="scene-01", duration=5.0, headline="One"),
        scene_kit.ScenePlan(id="scene-02", duration=5.0, headline="Two"),
    ]
    written = assembler.write_scene_files(tmp_path, plans)
    assert [p.name for p in written] == ["scene-01.html", "scene-02.html"]
    assert 'data-composition-id="scene-02"' in written[1].read_text()


def test_theme_reaches_the_spine():
    html = assembler.build_spine(
        board([]), audio_src="a.wav", mounts=[], theme=scene_kit.THEMES["swiss"]
    )
    assert scene_kit.THEMES["swiss"].bg in html
