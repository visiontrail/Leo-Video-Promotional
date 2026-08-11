"""Automated editorial collage B-roll through Agent SDK + signed-in web apps."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config
from backend.pipeline.opencli import OpenCLIError, first_json, run_opencli
from backend.pipeline.video_format import FrameSpec

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

SOURCE_REPOSITORY = "https://github.com/pyang5166/gbro-collage-broll"
SOURCE_COMMIT = "a1a4ee2e2abf7d44e460026b706d0c72c2cf8a91"
CLIP_SECONDS = 5
CLIP_FPS = 24
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_COLORS = ("#D96B35", "#D2A928", "#315F4C", "#594080", "#188C85", "#B73D3D")


def _log(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)
    else:
        logger.info(message)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _json_array(value: str) -> list[dict[str, Any]]:
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "[":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    raise RuntimeError("Collage planning agent did not return a JSON array")


def _scene_choices(storyboard: dict, count: int, force_opening: bool) -> list[dict]:
    scenes = list(storyboard.get("scenes") or [])
    if not scenes:
        return []
    count = min(max(1, count), len(scenes))
    selected: list[dict] = [scenes[0]] if force_opening else []
    remaining = [scene for scene in scenes if scene not in selected]
    needed = count - len(selected)
    if needed <= 0:
        return selected
    if needed >= len(remaining):
        return [*selected, *remaining]
    for slot in range(needed):
        index = round(slot * (len(remaining) - 1) / max(1, needed - 1))
        scene = remaining[index]
        if scene not in selected:
            selected.append(scene)
    for scene in remaining:
        if len(selected) >= count:
            break
        if scene not in selected:
            selected.append(scene)
    return sorted(selected, key=lambda scene: float(scene.get("start") or 0))


def _fallback_spec(scene: dict, index: int) -> dict[str, Any]:
    text = str(scene.get("text") or "").strip()
    meaning = re.split(r"(?<=[.!?。！？])\s*", text)[0][:220] or "A hidden process becomes visible"
    objects = ["central halftone subject", "paper mechanism", "connector pieces", "result card"]
    return {
        "scene_id": scene["id"],
        "script_meaning": meaning,
        "emotion": "clarity",
        "visual_metaphor": f"A paper mechanism physically reveals how {meaning.rstrip('.。')}.",
        "background_hex": _COLORS[index % len(_COLORS)],
        "accent_colors": ["warm cream", "cyan"],
        "elements": [
            {"what": item, "role": "metaphor", "motion": "slides and snaps into place", "placement": "center"}
            for item in objects
        ],
        "assembly_order": objects,
        "final_frame": "A concentrated completed paper mechanism with generous clear color field.",
        "planner": "deterministic_fallback",
    }


def _normalize_spec(raw: dict, scene: dict, index: int) -> dict[str, Any]:
    fallback = _fallback_spec(scene, index)
    spec = {**fallback, **raw, "scene_id": scene["id"], "planner": "claude_agent_sdk"}
    color = str(spec.get("background_hex") or "")
    spec["background_hex"] = color.upper() if _HEX.fullmatch(color) else fallback["background_hex"]
    accents = [str(value)[:40] for value in (spec.get("accent_colors") or []) if str(value).strip()]
    spec["accent_colors"] = accents[:3] or fallback["accent_colors"]
    elements = [item for item in (spec.get("elements") or []) if isinstance(item, dict)][:6]
    spec["elements"] = elements if len(elements) >= 3 else fallback["elements"]
    order = [str(value)[:100] for value in (spec.get("assembly_order") or []) if str(value).strip()]
    spec["assembly_order"] = order[:6] or [item["what"] for item in spec["elements"]]
    for key in ("script_meaning", "emotion", "visual_metaphor", "final_frame"):
        spec[key] = str(spec.get(key) or fallback[key])[:500]
    return spec


async def plan_specs(
    storyboard: dict,
    *,
    count: int,
    force_opening: bool,
    frame: FrameSpec,
    provider_id: int | None = None,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    log: LogCallback | None = None,
) -> list[dict[str, Any]]:
    """Use a dedicated Agent SDK turn to select beats and design metaphors."""
    scenes = list(storyboard.get("scenes") or [])
    if not scenes:
        return []
    target_count = min(max(1, count), len(scenes))
    fallback_choices = _scene_choices(storyboard, target_count, force_opening)
    from backend.pipeline import agent
    from backend.pipeline.digester import _resolve_provider

    skill_path = config.PROJECT_ROOT / ".claude" / "skills" / "gbro-collage-broll" / "SKILL.md"
    system = skill_path.read_text(encoding="utf-8")
    system += (
        "\n\nFor this planning turn, do not use tools and do not generate media. "
        "Return exactly the JSON array described in the Agent visual-spec contract. "
        f"Select exactly {target_count} visually rich beats from the supplied candidate scenes, "
        f"use only their scene ids, distribute the choices across the timeline, and target {frame.aspect_ratio}. "
        + (f"The first item must be {scenes[0]['id']}. " if force_opening else "")
    )
    payload = {
        "video_thesis": storyboard.get("thesis", ""),
        "orientation": frame.orientation,
        "aspect_ratio": frame.aspect_ratio,
        "force_opening_scene": force_opening,
        "candidate_scenes": [
            {
                "scene_id": scene["id"],
                "start": scene.get("start"),
                "duration": scene.get("duration"),
                "narration": str(scene.get("text") or "")[:700],
                "keywords": scene.get("keywords") or [],
            }
            for scene in scenes
        ],
    }
    choices = fallback_choices
    try:
        endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
        _log(log, f"Collage B-roll agent: selecting and designing {target_count} visual metaphor(s)")
        answer = await agent.agent_complete(
            system,
            json.dumps(payload, indent=2, ensure_ascii=False),
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            max_tokens=4096,
            log=log,
            label="Collage B-roll agent",
        )
        raw_items = _json_array(answer)
        allowed = {str(scene["id"]): scene for scene in scenes}
        raw_by_id: dict[str, dict[str, Any]] = {}
        selected_ids: list[str] = []
        for item in raw_items:
            scene_id = str(item.get("scene_id") or "")
            if scene_id in allowed and scene_id not in selected_ids:
                raw_by_id[scene_id] = item
                selected_ids.append(scene_id)
        if force_opening and str(scenes[0]["id"]) not in selected_ids:
            selected_ids.insert(0, str(scenes[0]["id"]))
        for scene in fallback_choices:
            scene_id = str(scene["id"])
            if len(selected_ids) >= target_count:
                break
            if scene_id not in selected_ids:
                selected_ids.append(scene_id)
        selected_ids = selected_ids[:target_count]
        choices = sorted(
            (allowed[scene_id] for scene_id in selected_ids),
            key=lambda scene: float(scene.get("start") or 0),
        )
    except Exception as exc:  # noqa: BLE001 - deterministic specs preserve delivery
        _log(log, f"Collage B-roll agent unavailable ({exc}); using narration-derived specs")
        raw_by_id = {}
    return [
        _normalize_spec(raw_by_id.get(scene["id"], {}), scene, index)
        for index, scene in enumerate(choices)
    ]


def image_prompt(spec: dict[str, Any], frame: FrameSpec) -> str:
    elements = "; ".join(str(item.get("what") or "") for item in spec["elements"])
    accents = ", ".join(spec["accent_colors"])
    return f"""Use case: documentary B-roll.
Asset type: final still frame for a {frame.aspect_ratio} image-to-video clip.
Create a finished premium editorial paper-collage expressing this visual metaphor: {spec['visual_metaphor']}
Scene/backdrop: a perfectly flat {spec['background_hex']} paper field with subtle uncoated-paper fiber.
Style: sophisticated stop-motion editorial collage; black-and-white halftone photographic cut-outs mixed with selective {accents} colored cardstock.
Composition: locked {frame.aspect_ratio} frame; one concentrated subject inside the middle 70 percent; generous clean color-field negative space; three to six large separable groups for assemble-from-empty animation.
Required groups: {elements}.
Final relationship: {spec['final_frame']}
Materials: visible printed halftone dots, crisp machine-cut edges, thin warm-cream keylines, soft low-opacity physical shadows.
Avoid all typography, readable letters, numerals, logos, watermarks, UI, subtitles, glossy 3D, photoreal environments, clutter, frames, and borders."""


def video_prompt(spec: dict[str, Any], frame: FrameSpec) -> str:
    order = " → ".join(spec["assembly_order"])
    return f"""Paper-collage stop-motion assembly. Image 1 is the exact empty first frame and Image 2 is the exact completed last frame. In one continuous locked-off {frame.aspect_ratio} shot, open on the empty flat {spec['background_hex']} paper field.

Assemble the scene piece by piece with crisp physical stop-motion timing in this exact order: {order}. Pieces slide, drop, stamp, and snap into place. End by holding the supplied completed Image 2 composition.

Preserve the exact {frame.aspect_ratio} framing, {spec['background_hex']} color field, colored cardstock accents, uncoated paper grain, halftone dots, cream keylines, crisp cut edges, and soft paper shadows. Restrained tactile 2D paper craft only. Aim for five seconds.

No scene cuts, camera movement, zoom, morphing, new objects, text, letters, numbers, logos, watermark, UI, or sound."""


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "results", "rows"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def _field(row: dict[str, Any], name: str) -> str:
    for key, value in row.items():
        if str(key).lower() == name:
            return str(value or "").lstrip("📁🔗 ").strip()
    return ""


async def _media_command(args: Sequence[str], *, timeout: int) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"Media command timed out after {timeout}s: {args[0]}")
    if process.returncode:
        detail = stderr.decode(errors="replace")[-1200:] or stdout.decode(errors="replace")[-1200:]
        raise RuntimeError(f"Media command failed ({process.returncode}): {detail}")


async def _generate_still(prompt: str, item_dir: Path) -> tuple[Path, str]:
    still_dir = item_dir / "stills"
    still_dir.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in still_dir.iterdir() if path.is_file()}
    timeout = config.COLLAGE_CHATGPT_TIMEOUT
    result = await run_opencli(
        [
            "chatgpt", "image", prompt,
            "--op", str(still_dir.resolve()),
            "--timeout", str(timeout),
            "--window", "background",
            "--site-session", "persistent",
            "--keep-tab", "false",
            "-f", "json",
        ],
        timeout=timeout + 60,
    )
    parsed = _rows(first_json(result.stdout))
    reported: list[Path] = []
    for row in parsed:
        raw = _field(row, "file")
        if raw and raw != "-":
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = config.PROJECT_ROOT / candidate
            if candidate.is_file():
                reported.append(candidate.resolve())
    created = sorted(
        (
            path.resolve()
            for path in still_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES and path.resolve() not in before
        ),
        key=lambda path: path.stat().st_mtime_ns,
    )
    images = list(dict.fromkeys([*reported, *created]))
    if not images:
        raise OpenCLIError("ChatGPT Web produced no downloaded collage still")
    conversation = next((_field(row, "link") for row in parsed if _field(row, "link")), "")
    return images[0], conversation


async def _prepare_frames(source: Path, item_dir: Path, color: str, frame: FrameSpec) -> tuple[Path, Path]:
    frames = item_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    original = frames / f"last-frame-original{source.suffix.lower()}"
    shutil.copyfile(source, original)
    first = frames / "first-frame.png"
    # JPEG keeps the browser-bridge fallback payload well below its message
    # ceiling while preserving the exact target pixels and collage detail.
    last = frames / "last-frame.jpg"
    await _media_command(
        [
            "ffmpeg", "-y", "-i", str(original), "-vf",
            f"scale={frame.media_width}:{frame.media_height}:force_original_aspect_ratio=increase,crop={frame.media_width}:{frame.media_height}",
            "-frames:v", "1", "-q:v", "3", str(last),
        ],
        timeout=120,
    )
    await _media_command(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=0x{color.lstrip('#')}:s={frame.media_width}x{frame.media_height}",
            "-frames:v", "1", str(first),
        ],
        timeout=120,
    )
    return first, last


async def _generate_video(prompt: str, first: Path, last: Path, item_dir: Path, frame: FrameSpec) -> tuple[Path, str]:
    video_dir = item_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    raw = video_dir / "gemini-web-original.mp4"
    timeout = config.COLLAGE_GEMINI_TIMEOUT
    result = await run_opencli(
        [
            "gemini", "video", prompt,
            "--first", str(first.resolve()),
            "--last", str(last.resolve()),
            "--aspect", frame.aspect_ratio,
            "--output", str(raw.resolve()),
            "--timeout", str(timeout),
            "--window", "background",
            "--site-session", "persistent",
            "--keep-tab", "false",
            "-f", "json",
        ],
        timeout=timeout + 120,
    )
    if not raw.is_file() or raw.stat().st_size < 1024:
        raise OpenCLIError("Gemini Web Create Video produced no downloaded MP4")
    rows = _rows(first_json(result.stdout))
    conversation = next((_field(row, "link") for row in rows if _field(row, "link")), "")
    return raw, conversation


async def _normalize_video(raw: Path, item_dir: Path, frame: FrameSpec) -> Path:
    final = item_dir / "video" / "final-5s-noaudio.mp4"
    await _media_command(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw),
            "-t", str(CLIP_SECONDS), "-vf",
            f"scale={frame.media_width}:{frame.media_height}:force_original_aspect_ratio=increase,crop={frame.media_width}:{frame.media_height},fps={CLIP_FPS}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final),
        ],
        timeout=300,
    )
    return final


async def probe_video(path: Path, frame: FrameSpec) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-800:])
    data = json.loads(stdout)
    videos = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    audio = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    stream = videos[0] if videos else {}
    rate = str(stream.get("avg_frame_rate") or "0/1").split("/")
    fps = float(rate[0]) / max(1.0, float(rate[1]))
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    checks = {
        "dimensions": (stream.get("width"), stream.get("height")) == (frame.media_width, frame.media_height),
        "duration": abs(duration - CLIP_SECONDS) <= 0.15,
        "fps": abs(fps - CLIP_FPS) <= 0.1,
        "no_audio": not audio,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": round(duration, 3),
        "fps": round(fps, 3),
        "audio_streams": len(audio),
    }


async def _contact_sheet(video: Path, item_dir: Path, frame: FrameSpec) -> Path:
    sheet = item_dir / "video" / "contact-sheet.jpg"
    thumb_w, thumb_h = ((256, 144) if not frame.is_portrait else (144, 256))
    await _media_command(
        [
            "ffmpeg", "-y", "-i", str(video), "-vf",
            f"fps=1,scale={thumb_w}:{thumb_h},tile=5x1", "-frames:v", "1", str(sheet),
        ],
        timeout=120,
    )
    return sheet


async def generate_collage_broll(
    storyboard: dict,
    task_dir: Path,
    *,
    count: int,
    force_opening: bool,
    frame: FrameSpec,
    provider_id: int | None = None,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    """Run the former three-gate workflow automatically, one web job at a time."""
    root = task_dir / "collage_broll"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {
        "status": "planning",
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "approval_gates": [],
        "approval_mode": "automatic",
        "planner": "claude_agent_sdk",
        "still_provider": "chatgpt_web_via_opencli",
        "video_provider": "gemini_web_create_video_via_opencli",
        "gemini_api_key_used": False,
        "orientation": frame.orientation,
        "aspect_ratio": frame.aspect_ratio,
        "requested_count": count,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "items": [],
        "errors": [],
    }
    _write_json(manifest_path, manifest)
    specs = await plan_specs(
        storyboard,
        count=count,
        force_opening=force_opening,
        frame=frame,
        provider_id=provider_id,
        ai_endpoint=ai_endpoint,
        ai_model=ai_model,
        log=log,
    )
    _write_json(root / "visual-spec.json", specs)
    manifest["status"] = "generating"
    _write_json(manifest_path, manifest)

    for index, spec in enumerate(specs, start=1):
        scene_id = spec["scene_id"]
        item_dir = root / f"{index:02d}-{scene_id}"
        item_dir.mkdir(parents=True, exist_ok=True)
        still_prompt = image_prompt(spec, frame)
        motion_prompt = video_prompt(spec, frame)
        (item_dir / "image-prompt.txt").write_text(still_prompt + "\n", encoding="utf-8")
        (item_dir / "video-prompt.txt").write_text(motion_prompt + "\n", encoding="utf-8")
        item: dict[str, Any] = {
            "scene_id": scene_id,
            "status": "generating",
            "spec": spec,
            "still_path": "",
            "video_path": "",
            "contact_sheet": "",
            "chatgpt_url": "",
            "gemini_url": "",
            "qa": {},
            "error": None,
        }
        manifest["items"].append(item)
        _write_json(manifest_path, manifest)
        try:
            _log(log, f"Collage B-roll {index}/{len(specs)}: generating {frame.aspect_ratio} still for {scene_id}")
            still, chatgpt_url = await _generate_still(still_prompt, item_dir)
            first, last = await _prepare_frames(still, item_dir, spec["background_hex"], frame)
            _log(log, f"Collage B-roll {index}/{len(specs)}: animating {scene_id} in Gemini Web Create Video")
            raw, gemini_url = await _generate_video(motion_prompt, first, last, item_dir, frame)
            final = await _normalize_video(raw, item_dir, frame)
            qa = await probe_video(final, frame)
            if not qa["passed"]:
                raise RuntimeError(f"normalized collage clip failed QA: {qa['checks']}")
            sheet = await _contact_sheet(final, item_dir, frame)
            item.update(
                {
                    "status": "ready",
                    "still_path": str(last.relative_to(task_dir)),
                    "video_path": str(final.relative_to(task_dir)),
                    "contact_sheet": str(sheet.relative_to(task_dir)),
                    "chatgpt_url": chatgpt_url,
                    "gemini_url": gemini_url,
                    "qa": qa,
                }
            )
            _log(log, f"Collage B-roll {index}/{len(specs)}: ready for {scene_id}")
        except Exception as exc:  # noqa: BLE001 - preserve other clips and fallback scene
            item.update({"status": "failed", "error": str(exc)})
            manifest["errors"].append({"scene_id": scene_id, "message": str(exc)})
            _log(log, f"Collage B-roll {index}/{len(specs)} failed for {scene_id}: {exc}")
        _write_json(manifest_path, manifest)

    ready = sum(1 for item in manifest["items"] if item["status"] == "ready")
    manifest["status"] = "ready" if ready == len(specs) else ("partial" if ready else "failed")
    manifest["ready_count"] = ready
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest_path, manifest)
    return manifest


def attach_collage(plans: list[dict], manifest: dict | None, task_dir: Path) -> int:
    """Promote successful generated items to clean, full-bleed scene plates."""
    by_id = {plan.get("id"): plan for plan in plans}
    attached = 0
    for item in (manifest or {}).get("items") or []:
        if item.get("status") != "ready":
            continue
        plan = by_id.get(item.get("scene_id"))
        raw = str(item.get("video_path") or "")
        path = task_dir / raw
        if not plan or not raw or not path.is_file():
            continue
        plan.update(
            {
                "archetype": "footage",
                # Scene files live in compositions/, so media stored relative
                # to the render-project root needs one parent hop.
                "footage_src": f"../{raw}",
                "footage_kind": "video",
                "footage_credit": "",
                "collage_broll": True,
                "collage_metaphor": item.get("spec", {}).get("visual_metaphor", ""),
                "collage_qa": item.get("qa", {}),
            }
        )
        attached += 1
    return attached
