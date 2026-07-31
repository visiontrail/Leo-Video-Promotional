from pathlib import Path

import pytest

from backend.pipeline import storyboard as sb


def write_script(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "script.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parse_script_lines_reads_bare_monologue_lines(tmp_path):
    path = write_script(tmp_path, ["First line here.", "", "Second line here."])
    lines = sb.parse_script_lines(path, is_monologue=True)
    assert [line["text"] for line in lines] == ["First line here.", "Second line here."]
    assert {line["speaker"] for line in lines} == {1}


def test_parse_script_lines_reads_speaker_labels(tmp_path):
    path = write_script(tmp_path, ["Speaker 1: Hello.", "Speaker 2: Hi back."])
    lines = sb.parse_script_lines(path)
    assert [line["speaker"] for line in lines] == [1, 2]
    assert [line["text"] for line in lines] == ["Hello.", "Hi back."]


def test_line_timing_falls_back_to_word_count_and_fills_the_audio(tmp_path):
    lines = sb.parse_script_lines(write_script(tmp_path, ["one two", "three four five six"]))
    timed = sb.assign_line_timing(lines, audio_duration=60.0, silence_boundaries=[])
    assert timed[0]["start"] == 0.0
    # Six words total, two in the first line: a third of the runtime.
    assert timed[0]["duration"] == pytest.approx(20.0, abs=0.01)
    assert timed[1]["start"] == pytest.approx(20.0, abs=0.01)
    assert timed[-1]["start"] + timed[-1]["duration"] == pytest.approx(60.0, abs=0.01)


def test_line_timing_prefers_the_silence_map_when_it_is_dense_enough(tmp_path):
    lines = sb.parse_script_lines(write_script(tmp_path, ["a b", "c d", "e f"]))
    timed = sb.assign_line_timing(lines, audio_duration=30.0, silence_boundaries=[8.0, 21.0])
    assert [line["start"] for line in timed] == [0.0, 8.0, 21.0]
    assert timed[-1]["duration"] == pytest.approx(9.0)


def test_scenes_cover_the_audio_with_no_gaps_and_carry_the_title_offset(tmp_path):
    path = write_script(tmp_path, [f"Sentence number {i} with several words in it." for i in range(30)])
    board = sb.build_storyboard(
        script_path=path, audio_duration=300.0, title="T", is_monologue=True
    )
    scenes = board["scenes"]
    assert scenes[0]["start"] == sb.TITLE_DURATION
    for earlier, later in zip(scenes, scenes[1:]):
        assert earlier["start"] + earlier["duration"] == pytest.approx(later["start"], abs=0.05)
    last = scenes[-1]
    assert last["start"] + last["duration"] == pytest.approx(board["outro_start"], abs=0.05)
    assert board["total_duration"] == pytest.approx(
        sb.TITLE_DURATION + 300.0 + sb.OUTRO_DURATION, abs=0.05
    )


def test_short_trailing_scene_is_folded_into_its_predecessor(tmp_path):
    # A long line followed by a very short one would otherwise leave a stub scene.
    path = write_script(tmp_path, ["word " * 200, "ok"])
    board = sb.build_storyboard(
        script_path=path, audio_duration=120.0, title="T", is_monologue=True
    )
    assert all(scene["duration"] >= sb.SCENE_MIN_SECONDS for scene in board["scenes"][1:])


def test_scene_ids_are_sequential_after_merging(tmp_path):
    path = write_script(tmp_path, [f"Line {i} here with words." for i in range(40)])
    board = sb.build_storyboard(
        script_path=path, audio_duration=400.0, title="T", is_monologue=True
    )
    ids = [scene["id"] for scene in board["scenes"]]
    assert ids == [f"scene-{i + 1:02d}" for i in range(len(ids))]
    assert [scene["index"] for scene in board["scenes"]] == list(range(len(ids)))


def test_keywords_skip_stopwords(tmp_path):
    path = write_script(tmp_path, ["The sunflowers in the fields were there because of the war."])
    board = sb.build_storyboard(
        script_path=path, audio_duration=20.0, title="T", is_monologue=True
    )
    keywords = board["scenes"][0]["keywords"]
    assert "sunflowers" in keywords
    assert "the" not in keywords and "were" not in keywords
