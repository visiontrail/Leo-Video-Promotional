"""Turn a narration script plus its rendered audio into a timed storyboard.

This is the stage that used to be missing. The old composer went straight from
"script lines" to "one caption div per line", which is why a 12-minute episode
rendered as 158 stacked subtitle clips and nothing else. A storyboard sits in
between: it groups the spoken lines into *scenes* (a handful of seconds each,
cut on natural pauses) and hands every downstream consumer — the AI visual
planner, the scene-kit renderer, and the Claude Agent SDK director — the same
timing truth.

Nothing here talks to a model. Timing must be reproducible and must line up with
the audio to the frame, so segmentation is pure arithmetic over the script and
the ffmpeg silence map. The creative layer (what each scene *looks* like) is
:mod:`backend.pipeline.visual_plan`.
"""

from __future__ import annotations

import json
import re
import wave
from collections.abc import Callable
from pathlib import Path

LogCallback = Callable[[str], None]

# Scene pacing. A scene is the unit of visual change: too short and the video
# strobes, too long and it reads as a static slide. 14s is roughly two to three
# spoken sentences, which is also about how long a single visual idea holds.
SCENE_TARGET_SECONDS = 14.0
SCENE_MIN_SECONDS = 7.0
SCENE_MAX_SECONDS = 24.0

# Lead-in title card and closing card, in seconds. Kept here (rather than in the
# composer) so scene start times in storyboard.json are already absolute
# composition times and no consumer has to re-apply the offset.
TITLE_DURATION = 5.0
OUTRO_DURATION = 5.0

SPEAKER_LABEL_RE = re.compile(r"^Speaker\s*(\d+)\s*[:：\-—–]\s*(.+)$", re.IGNORECASE)

# Words too generic to describe a scene visually. Used only for the keyword hint
# that helps the visual planner and the footage matcher; not user-visible.
_STOPWORDS = frozenset(
    """
    a about after all also am an and any are as at be because been before being but by came can come
    could did do does doing done down each even every for from get got had has have he her here him his
    how i if in into is it its just like make many me might more most much must my no not now of off on
    once one only or other our out over own said same say see she should so some still such take than
    that the their them then there these they thing think this those though through to too under until
    up us very was way we well were what when where which while who why will with would you your
    """.split()
)


def get_audio_duration(wav_path: str | Path) -> float:
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / w.getframerate()


def parse_script_lines(script_path: str | Path, is_monologue: bool = False) -> list[dict]:
    """Read the script into one record per spoken line.

    Accepts both the ``Speaker 1: ...`` dialogue form and the bare-line
    monologue form the scriptwriter prompt emits.
    """
    lines: list[dict] = []
    for raw in Path(script_path).read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text:
            continue
        match = SPEAKER_LABEL_RE.match(text)
        if match:
            speaker = int(match.group(1))
            text = match.group(2)
        else:
            speaker = 1 if is_monologue else (len(lines) % 2) + 1
        lines.append({"speaker": speaker, "text": text, "word_count": len(text.split())})
    return lines


def assign_line_timing(
    lines: list[dict],
    audio_duration: float,
    silence_boundaries: list[float] | None = None,
) -> list[dict]:
    """Give every line a start/duration inside ``audio_duration``.

    Prefers the ffmpeg silence map when it has at least one boundary per line
    gap — that tracks the actual synthesis. Otherwise falls back to splitting
    the timeline in proportion to word count, which is stable if imprecise.
    """
    if not lines:
        return lines

    total_words = sum(line["word_count"] for line in lines) or len(lines)
    boundaries = silence_boundaries or []

    if len(boundaries) >= len(lines) - 1 > 0:
        marks = [0.0] + boundaries[: len(lines) - 1]
        for i, line in enumerate(lines):
            end = marks[i + 1] if i + 1 < len(marks) else audio_duration
            line["start"] = round(marks[i], 3)
            line["duration"] = round(max(0.2, end - marks[i]), 3)
        return lines

    elapsed = 0.0
    for line in lines:
        share = (line["word_count"] or 1) / total_words
        duration = share * audio_duration
        line["start"] = round(elapsed, 3)
        line["duration"] = round(duration, 3)
        elapsed += duration
    return lines


def _keywords(text: str, limit: int = 8) -> list[str]:
    """Content words of a scene, most frequent first, for visual/footage hints."""
    counts: dict[str, int] = {}
    for word in re.findall(r"[A-Za-z][A-Za-z'\-]+", text.lower()):
        if len(word) < 4 or word in _STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def group_lines_into_scenes(lines: list[dict], offset: float = 0.0) -> list[dict]:
    """Batch timed lines into scenes of roughly ``SCENE_TARGET_SECONDS``.

    A scene closes once it has reached the target length; a line that would push
    it past ``SCENE_MAX_SECONDS`` starts a new scene instead. ``offset`` shifts
    every time by the title-card length so scene times are absolute composition
    times.
    """
    scenes: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        start = current[0]["start"]
        end = current[-1]["start"] + current[-1]["duration"]
        text = " ".join(line["text"] for line in current)
        scenes.append(
            {
                "id": f"scene-{len(scenes) + 1:02d}",
                "index": len(scenes),
                "start": round(start + offset, 2),
                "duration": round(max(0.5, end - start), 2),
                "lines": [
                    {
                        "start": round(line["start"] + offset, 2),
                        "duration": round(line["duration"], 2),
                        "speaker": line["speaker"],
                        "text": line["text"],
                    }
                    for line in current
                ],
                "text": text,
                "word_count": sum(line["word_count"] for line in current),
                "keywords": _keywords(text),
            }
        )
        current.clear()

    for line in lines:
        if current:
            span = (line["start"] + line["duration"]) - current[0]["start"]
            reached_target = (line["start"] - current[0]["start"]) >= SCENE_TARGET_SECONDS
            if reached_target or span > SCENE_MAX_SECONDS:
                flush()
        current.append(line)
    flush()

    # A trailing stub (one short line orphaned by the max-length cut) reads as a
    # flash frame. Fold anything under the minimum back into its predecessor.
    merged: list[dict] = []
    for scene in scenes:
        if merged and scene["duration"] < SCENE_MIN_SECONDS:
            prev = merged[-1]
            prev["lines"].extend(scene["lines"])
            prev["duration"] = round(
                (scene["start"] + scene["duration"]) - prev["start"], 2
            )
            prev["text"] = f"{prev['text']} {scene['text']}"
            prev["word_count"] += scene["word_count"]
            prev["keywords"] = _keywords(prev["text"])
            continue
        merged.append(scene)

    for i, scene in enumerate(merged):
        scene["id"] = f"scene-{i + 1:02d}"
        scene["index"] = i
    return merged


def build_storyboard(
    *,
    script_path: str | Path,
    audio_duration: float,
    title: str,
    silence_boundaries: list[float] | None = None,
    is_monologue: bool = True,
    summary: dict | None = None,
    log: LogCallback | None = None,
) -> dict:
    """Assemble the full storyboard document for one task."""
    lines = parse_script_lines(script_path, is_monologue=is_monologue)
    lines = assign_line_timing(lines, audio_duration, silence_boundaries)
    scenes = group_lines_into_scenes(lines, offset=TITLE_DURATION)

    if log:
        log(
            f"Storyboard: {len(lines)} spoken lines -> {len(scenes)} scenes "
            f"(avg {audio_duration / max(1, len(scenes)):.1f}s each)"
        )

    return {
        "title": title,
        "thesis": (summary or {}).get("thesis", ""),
        "audio_duration": round(audio_duration, 2),
        "title_duration": TITLE_DURATION,
        "outro_duration": OUTRO_DURATION,
        "content_start": TITLE_DURATION,
        "outro_start": round(TITLE_DURATION + audio_duration, 2),
        "total_duration": round(TITLE_DURATION + audio_duration + OUTRO_DURATION, 2),
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def write_storyboard(task_dir: str | Path, storyboard: dict) -> Path:
    path = Path(task_dir) / "storyboard.json"
    path.write_text(json.dumps(storyboard, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_storyboard(task_dir: str | Path) -> dict | None:
    path = Path(task_dir) / "storyboard.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
