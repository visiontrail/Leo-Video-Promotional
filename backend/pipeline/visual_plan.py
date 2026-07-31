"""Decide what each storyboard scene should *look* like.

The storyboard says when a scene runs and what is said during it. This module
turns that into creative direction — an archetype, a headline, supporting copy,
an accent colour and a motif — which the scene kit renders and the director
agent uses as its brief.

The model call is plain JSON in / JSON out so it works with any provider the
task is pointed at. Every field has a deterministic fallback derived from the
narration itself, so a provider outage degrades to a plainer video, never to a
blank one.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from backend import config
from backend.pipeline import scene_kit

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# Scenes per model call. Large enough that the model can shape an arc across a
# stretch of the episode, small enough to stay inside a single response.
PLAN_BATCH_SIZE = 12

# Accent rotation used by the fallback and as a sanity default. Consecutive
# scenes never share an accent, which alone gives the video a sense of movement.
_ACCENT_CYCLE = ("amber", "teal", "coral", "violet", "sky", "rose", "lime")

# Motif rotation for the fallback path, biased toward the calmer shapes.
_MOTIF_CYCLE = ("orbit", "waves", "grid", "arcs", "bloom", "prism", "skyline", "sunburst")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _emit(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)
    else:
        logger.info(message)


def _strip_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _first_json_array(value: str) -> list | None:
    """Pull the first top-level JSON array out of a model response.

    Reasoning models like to wrap the answer in prose; a bracket scan is more
    forgiving than json.loads on the whole string and costs nothing.
    """
    text = _strip_fence(value)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("scenes"), list):
            return parsed["scenes"]
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, list):
                        return parsed
                    break
        start = text.find("[", start + 1)
    return None


_CLAUSE_BREAK = re.compile(r"[,;:—–-]\s")


def _headline_from(text: str, limit: int = 68) -> str:
    """Best-effort headline: the first sentence, trimmed to a readable length.

    A hard word-boundary cut leaves a headline dangling mid-thought ("... and
    realize it's"), which looks like a bug on screen. Prefer to end on a clause
    boundary, and only fall back to an ellipsis when there isn't one.
    """
    sentence = _SENTENCE_SPLIT.split(text.strip())[0].strip(" —-")
    if len(sentence) <= limit:
        return sentence

    window = sentence[: limit + 1]
    breaks = list(_CLAUSE_BREAK.finditer(window))
    if breaks and breaks[-1].start() >= limit * 0.45:
        return window[: breaks[-1].start()].rstrip()

    words = window.split()
    out: list[str] = []
    for word in words:
        if len(" ".join(out + [word])) > limit - 1:
            break
        out.append(word)
    return (" ".join(out) + "…") if out else sentence[:limit]


def _support_from(text: str, headline: str, limit: int = 190) -> str:
    remainder = text[len(headline) :].strip(" .—-") if text.startswith(headline) else text
    sentences = _SENTENCE_SPLIT.split(remainder.strip())
    out: list[str] = []
    for sentence in sentences:
        candidate = " ".join(out + [sentence])
        if len(candidate) > limit:
            break
        out.append(sentence)
    return " ".join(out).strip()


def fallback_plan(storyboard: dict) -> list[dict]:
    """Derive scene direction from the narration alone, no model involved."""
    plans: list[dict] = []
    for i, scene in enumerate(storyboard.get("scenes", [])):
        text = scene.get("text", "")
        headline = _headline_from(text)
        body = _support_from(text, headline)
        keywords = scene.get("keywords") or []
        # A scene that opens on a quoted line reads best as a pull quote.
        archetype = "quote" if text.strip().startswith(('"', "“")) else ("statement" if i % 5 == 0 else "topic")
        plans.append(
            {
                "id": scene["id"],
                "archetype": archetype,
                "kicker": (keywords[0] if keywords else "chapter").upper()[:28],
                "headline": headline,
                "body": body,
                "quote": headline if archetype == "quote" else "",
                "accent": _ACCENT_CYCLE[i % len(_ACCENT_CYCLE)],
                "motif": _MOTIF_CYCLE[i % len(_MOTIF_CYCLE)],
            }
        )
    return plans


def _normalise(raw: dict, scene: dict, index: int) -> dict:
    """Coerce one model-produced entry into the scene-kit contract."""
    plan = dict(raw or {})
    plan["id"] = scene["id"]

    archetype = str(plan.get("archetype") or "").lower()
    if archetype not in scene_kit.ARCHETYPES or archetype in ("title", "outro", "footage"):
        # title/outro are owned by the spine; footage is assigned by the
        # footage matcher, which knows which clips actually downloaded.
        archetype = "topic" if archetype not in scene_kit.ARCHETYPES else archetype
        if archetype in ("title", "outro", "footage"):
            archetype = "topic"
    plan["archetype"] = archetype

    if not str(plan.get("headline") or "").strip():
        plan["headline"] = _headline_from(scene.get("text", ""))
    if not str(plan.get("kicker") or "").strip():
        keywords = scene.get("keywords") or []
        plan["kicker"] = (keywords[0] if keywords else "chapter").upper()[:28]

    accent = str(plan.get("accent") or "").lower()
    plan["accent"] = accent if accent in scene_kit.ACCENTS else _ACCENT_CYCLE[index % len(_ACCENT_CYCLE)]
    motif = str(plan.get("motif") or "").lower()
    plan["motif"] = motif if motif in scene_kit.MOTIFS else _MOTIF_CYCLE[index % len(_MOTIF_CYCLE)]

    # Trim copy to what actually fits a 1920x1080 frame. The model is told the
    # limits, but enforcing them here is what keeps `hyperframes inspect` clean.
    plan["headline"] = str(plan["headline"])[:110]
    plan["body"] = str(plan.get("body") or "")[:260]
    plan["kicker"] = str(plan["kicker"])[:30]
    plan["items"] = [str(item)[:90] for item in (plan.get("items") or [])][:4]
    return plan


def _batch_prompt_payload(storyboard: dict, scenes: list[dict]) -> str:
    payload = {
        "episode_title": storyboard.get("title", ""),
        "thesis": storyboard.get("thesis", ""),
        "scenes": [
            {
                "id": scene["id"],
                "seconds": scene["duration"],
                "narration": scene["text"],
            }
            for scene in scenes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def plan_scene_visuals(
    storyboard: dict,
    *,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
    log: LogCallback | None = None,
) -> list[dict]:
    """Creative direction for every scene, model-driven with a safe fallback."""
    scenes = storyboard.get("scenes", [])
    if not scenes:
        return []

    # Imported here to avoid a module-level cycle: digester imports the agent
    # transport, which imports config, which the pipeline package initialises.
    from backend.pipeline.digester import _chat, _resolve_provider

    prompt_path = config.PROMPTS_DIR / "visual_plan.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    try:
        endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    except Exception as exc:  # noqa: BLE001 - provider lookup must never sink the render
        _emit(log, f"Visual plan: provider lookup failed ({exc}); using derived direction")
        return fallback_plan(storyboard)

    batches = [scenes[i : i + PLAN_BATCH_SIZE] for i in range(0, len(scenes), PLAN_BATCH_SIZE)]
    _emit(log, f"Visual plan: directing {len(scenes)} scenes in {len(batches)} batch(es)")

    by_id: dict[str, dict] = {}
    for n, batch in enumerate(batches, start=1):
        try:
            reply = await _chat(
                system_prompt,
                _batch_prompt_payload(storyboard, batch),
                endpoint,
                model,
                api_key,
                log,
                f"Visual plan {n}/{len(batches)}",
            )
            entries = _first_json_array(reply) or []
        except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the rest
            _emit(log, f"Visual plan batch {n} failed ({exc}); deriving that batch from narration")
            entries = []

        indexed = {str(entry.get("id")): entry for entry in entries if isinstance(entry, dict)}
        for scene in batch:
            raw = indexed.get(scene["id"])
            if raw is None:
                continue
            by_id[scene["id"]] = _normalise(raw, scene, scene["index"])

    derived = {plan["id"]: plan for plan in fallback_plan(storyboard)}
    plans = [by_id.get(scene["id"]) or derived[scene["id"]] for scene in scenes]
    planned = sum(1 for scene in scenes if scene["id"] in by_id)
    _emit(log, f"Visual plan ready: {planned}/{len(scenes)} scenes directed by the model")
    return plans


TITLE_SCENE_ID = "scene-00-title"
OUTRO_SCENE_ID = "scene-99-outro"


def title_plan(storyboard: dict, *, subtitle: str = "") -> dict:
    """Opening card. Mounted at t=0, before the narration starts."""
    return {
        "id": TITLE_SCENE_ID,
        "archetype": "title",
        "kicker": "",
        "headline": storyboard.get("title", "") or "Untitled Episode",
        "body": subtitle or _headline_from(storyboard.get("thesis", ""), limit=150),
        "accent": "amber",
        "motif": "sunburst",
    }


def outro_plan(storyboard: dict, *, brand: str = "") -> dict:
    """Closing card. The one scene allowed to animate itself out."""
    return {
        "id": OUTRO_SCENE_ID,
        "archetype": "outro",
        "kicker": "",
        "headline": "Thanks for watching",
        "body": brand,
        "accent": "coral",
        "motif": "bloom",
    }


def attach_footage(plans: list[dict], storyboard: dict, manifest: dict | None, task_dir: Path) -> int:
    """Promote scenes to full-bleed footage plates where a clip actually fits.

    Clips are matched to the scene whose keywords best overlap the query that
    found them, so a sunflower plate lands on the sunflower paragraph instead of
    wherever it happened to download.
    """
    clips = (manifest or {}).get("clips") or []
    if not clips:
        return 0

    scenes_by_id = {scene["id"]: scene for scene in storyboard.get("scenes", [])}
    used: set[str] = set()
    attached = 0

    for clip in clips:
        local = clip.get("local_path") or clip.get("path")
        if not local:
            continue
        path = Path(local)
        if not path.is_absolute():
            path = task_dir / local
        if not path.exists():
            continue
        try:
            rel = path.resolve().relative_to(task_dir.resolve()).as_posix()
        except ValueError:
            continue

        terms = {
            word.lower()
            for word in re.findall(r"[A-Za-z]{4,}", f"{clip.get('query', '')} {clip.get('title', '')}")
        }
        best_id, best_score = None, 0
        for plan in plans:
            if plan["id"] in used:
                continue
            scene = scenes_by_id.get(plan["id"])
            if not scene:
                continue
            score = len(terms & set(scene.get("keywords") or []))
            if score > best_score:
                best_id, best_score = plan["id"], score
        if best_id is None or best_score == 0:
            continue

        plan = next(p for p in plans if p["id"] == best_id)
        plan["archetype"] = "footage"
        plan["footage_src"] = rel
        plan["footage_kind"] = "video" if path.suffix.lower() in {".webm", ".mp4", ".ogv"} else "image"
        credit = clip.get("attribution") or clip.get("license_short_name")
        if not credit:
            creator = str(clip.get("creator") or "Unknown creator")
            provider = str(clip.get("provider") or "Footage source")
            credit = f"{creator} · {provider}"
        plan["footage_credit"] = str(credit)[:90]
        used.add(best_id)
        attached += 1

    return attached
