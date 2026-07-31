"""Bilibili/YouTube discovery, web-model trim analysis, and FFmpeg editing.

The source ledger is intentionally stricter than the Wikimedia path: a file
being downloadable does not imply reuse rights. Platform-hosted clips are
marked ``review_required`` in the manifest and remain traceable to their source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from backend import config
from backend.pipeline.extractors.youtube import _yt_dlp_common_args
from backend.pipeline.opencli import OpenCLIError, first_json, run_opencli

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

BV_RE = re.compile(r"\b(BV[0-9A-Za-z]+)\b")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{2,}")


class WebFootageError(RuntimeError):
    """One candidate failed without invalidating the rest of the scout."""


def _emit(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)
    else:
        logger.info(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def _tool_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [str(config.PROJECT_ROOT / ".venv" / "bin"), env.get("PATH", "")]
    )
    return env


async def _run_command(
    command: list[str],
    *,
    timeout: int,
    check: bool = True,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(config.PROJECT_ROOT),
        env=_tool_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise WebFootageError(
            f"Command timed out after {timeout}s: {Path(command[0]).name}"
        ) from exc
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    returncode = process.returncode or 0
    if check and returncode != 0:
        raise WebFootageError(
            f"{Path(command[0]).name} failed with exit {returncode}: "
            f"{(stderr or stdout)[-1200:]}"
        )
    return returncode, stdout, stderr


def _yt_dlp_bin() -> str:
    project_binary = config.PROJECT_ROOT / ".venv" / "bin" / "yt-dlp"
    if project_binary.is_file():
        return str(project_binary)
    binary = shutil.which("yt-dlp")
    if binary:
        return binary
    raise WebFootageError("yt-dlp is not installed in the project virtualenv")


async def search_bilibili(query: str, *, limit: int = 4) -> list[dict]:
    result = await run_opencli(
        ["bilibili", "search", query, "--limit", str(limit), "-f", "json"],
        timeout=min(config.OPENCLI_TIMEOUT, 90),
    )
    rows = first_json(result.stdout)
    if not isinstance(rows, list):
        raise OpenCLIError("Bilibili search returned a non-array result")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("url"):
            continue
        candidates.append(
            {
                "platform": "bilibili",
                "provider": "Bilibili via OpenCLI",
                "provider_id": "opencli-bilibili",
                "title": str(row.get("title") or "Bilibili video"),
                "creator": str(row.get("author") or "Unknown"),
                "source_page_url": str(row["url"]),
                "duration_seconds": 0.0,
                "search_score": int(row.get("score") or 0),
            }
        )
    return candidates


async def search_youtube(query: str, *, limit: int = 4) -> list[dict]:
    command = [
        _yt_dlp_bin(),
        *_yt_dlp_common_args(include_cookies=False),
        "--flat-playlist",
        "--dump-json",
        "--playlist-end",
        str(limit),
        f"ytsearch{limit}:{query}",
    ]
    _, stdout, _ = await _run_command(command, timeout=90)
    candidates = []
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = row.get("webpage_url") or row.get("url")
        if not str(url).startswith("http"):
            continue
        candidates.append(
            {
                "platform": "youtube",
                "provider": "YouTube via yt-dlp",
                "provider_id": "youtube-ytdlp",
                "title": str(row.get("title") or "YouTube video"),
                "creator": str(row.get("channel") or row.get("uploader") or "Unknown"),
                "source_page_url": str(url),
                "duration_seconds": float(row.get("duration") or 0),
                "description": str(row.get("description") or "")[:500],
                "search_score": int(row.get("view_count") or 0),
            }
        )
    return candidates


def matching_script_excerpt(script: str, query: str, *, limit: int = 1200) -> str:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n|(?<=[.!?])\s+", script) if chunk.strip()]
    if not chunks:
        return script[:limit]
    terms = {word.lower() for word in WORD_RE.findall(query)}

    def score(chunk: str) -> tuple[int, int]:
        words = {word.lower() for word in WORD_RE.findall(chunk)}
        return len(terms & words), min(len(chunk), limit)

    best = max(chunks, key=score)
    return best[:limit]


def _fallback_analysis(candidate: dict, *, reason: str) -> dict:
    duration = float(candidate.get("duration_seconds") or 0)
    clip_seconds = float(config.WEB_FOOTAGE_CLIP_SECONDS)
    start = min(3.0, max(0.0, duration * 0.1)) if duration else 0.0
    end = min(duration, start + clip_seconds) if duration else start + clip_seconds
    return {
        "start_seconds": round(start, 3),
        "end_seconds": round(max(start + 1.0, end), 3),
        "confidence": 0.25,
        "reason": reason[:300],
        "analyzer": "deterministic-safe-offset",
        "status": "fallback",
    }


def _normalise_analysis(parsed: dict, candidate: dict) -> dict:
    duration = float(candidate.get("duration_seconds") or 0)
    maximum = float(config.WEB_FOOTAGE_CLIP_SECONDS)
    start = max(0.0, float(parsed.get("start_seconds") or 0))
    end = float(parsed.get("end_seconds") or (start + maximum))
    if end <= start:
        end = start + maximum
    end = min(end, start + maximum)
    if duration:
        start = min(start, max(0.0, duration - 1.0))
        end = min(duration, max(start + 1.0, end))
    confidence = min(1.0, max(0.0, float(parsed.get("confidence") or 0.5)))
    return {
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "confidence": round(confidence, 3),
        "reason": str(parsed.get("reason") or "Gemini selected this interval")[:300],
        "analyzer": "gemini-web-via-opencli",
        "status": "analyzed",
    }


async def analyze_candidate_link(candidate: dict, script_excerpt: str) -> dict:
    if not config.WEB_FOOTAGE_GEMINI_ENABLED:
        return _fallback_analysis(candidate, reason="Gemini web analysis is disabled")
    if candidate.get("platform") != "youtube":
        return _fallback_analysis(
            candidate,
            reason="Gemini Web cannot reliably open Bilibili links; local safe-offset fallback used",
        )

    duration = float(candidate.get("duration_seconds") or 0)
    prompt = (
        "You are a film editor selecting B-roll for narration. Analyze the actual visuals in "
        f"this public YouTube video: {candidate['source_page_url']}\n"
        f"Candidate duration: {duration:.1f} seconds.\n"
        f"Narration excerpt:\n{script_excerpt}\n\n"
        "Return ONLY one compact JSON object with numeric start_seconds, end_seconds, "
        "confidence (0 to 1), and a short reason. Select a visually coherent interval of at "
        f"most {config.WEB_FOOTAGE_CLIP_SECONDS} seconds. Avoid intros, logos, subtitles, "
        "talking-head filler, and end cards."
    )
    try:
        result = await run_opencli(
            [
                "gemini",
                "ask",
                prompt,
                "--new",
                "true",
                "--timeout",
                str(config.WEB_FOOTAGE_GEMINI_TIMEOUT),
            ],
            timeout=config.WEB_FOOTAGE_GEMINI_TIMEOUT + 30,
        )
        if "[NO RESPONSE]" in result.stdout:
            raise OpenCLIError("Gemini returned no response before its web timeout")
        parsed = first_json(result.stdout)
        if not isinstance(parsed, dict):
            raise OpenCLIError("Gemini trim analysis was not a JSON object")
        return _normalise_analysis(parsed, candidate)
    except Exception as exc:  # noqa: BLE001 - trim fallback must keep the scout moving
        return _fallback_analysis(candidate, reason=f"Gemini analysis fallback: {exc}")


async def _download_bilibili(candidate: dict, raw_dir: Path) -> Path:
    match = BV_RE.search(candidate["source_page_url"])
    if not match:
        raise WebFootageError("Bilibili candidate URL does not contain a BV id")
    before = {path.resolve() for path in raw_dir.glob("*") if path.is_file()}
    result = await run_opencli(
        [
            "bilibili",
            "download",
            match.group(1),
            "--quality",
            "480p",
            "--output",
            str(raw_dir),
            "-f",
            "json",
        ],
        timeout=config.WEB_FOOTAGE_DOWNLOAD_TIMEOUT,
    )
    after = [
        path for path in raw_dir.glob("*")
        if path.is_file() and path.resolve() not in before and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]
    if not after:
        raise WebFootageError(f"OpenCLI Bilibili download produced no media file: {result.stdout[-500:]}")
    media_path = max(after, key=lambda path: path.stat().st_mtime_ns)
    if media_path.stat().st_size > config.FOOTAGE_MAX_BYTES:
        raise WebFootageError(
            f"Bilibili download exceeds the {config.FOOTAGE_MAX_BYTES}-byte footage limit"
        )
    return media_path


async def _download_youtube(candidate: dict, raw_dir: Path) -> Path:
    template = raw_dir / "%(id)s.%(ext)s"
    command = [
        _yt_dlp_bin(),
        *_yt_dlp_common_args(include_cookies=False),
        "--no-playlist",
        "--max-filesize",
        str(config.FOOTAGE_MAX_BYTES),
        "-f",
        "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        str(template),
        candidate["source_page_url"],
    ]
    before = {path.resolve() for path in raw_dir.glob("*") if path.is_file()}
    await _run_command(command, timeout=config.WEB_FOOTAGE_DOWNLOAD_TIMEOUT)
    after = [
        path for path in raw_dir.glob("*")
        if path.is_file() and path.resolve() not in before and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]
    if not after:
        raise WebFootageError("yt-dlp YouTube download produced no media file")
    media_path = max(after, key=lambda path: path.stat().st_mtime_ns)
    if media_path.stat().st_size > config.FOOTAGE_MAX_BYTES:
        raise WebFootageError(
            f"YouTube download exceeds the {config.FOOTAGE_MAX_BYTES}-byte footage limit"
        )
    return media_path


async def _probe(path: Path) -> dict:
    _, stdout, _ = await _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "duration_seconds": float((data.get("format") or {}).get("duration") or 0),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


def _fit_analysis_to_media(analysis: dict, duration: float) -> dict:
    enriched = dict(analysis)
    maximum = float(config.WEB_FOOTAGE_CLIP_SECONDS)
    requested_start = float(enriched.get("start_seconds") or 0)
    requested_end = float(enriched.get("end_seconds") or (requested_start + maximum))
    requested_length = min(maximum, max(1.0, requested_end - requested_start))
    if (
        enriched.get("analyzer") == "deterministic-safe-offset"
        and requested_start == 0
        and duration > maximum * 2
    ):
        # Bilibili search rows do not expose duration before download. Once the
        # real duration is known, skip the likely channel bumper instead of
        # blindly trimming from frame zero.
        requested_start = min(max(3.0, duration * 0.1), duration - maximum)
    start = max(0.0, min(requested_start, max(0.0, duration - 1.0)))
    end = min(duration, start + requested_length)
    enriched["start_seconds"] = round(start, 3)
    enriched["end_seconds"] = round(end, 3)
    return enriched


async def _trim(raw_path: Path, destination: Path, analysis: dict, orientation: str) -> None:
    start = float(analysis["start_seconds"])
    length = max(1.0, float(analysis["end_seconds"]) - start)
    if orientation == "portrait":
        width, height = 720, 1280
    else:
        width, height = 1280, 720
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    await _run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(raw_path),
            "-t",
            f"{length:.3f}",
            "-an",
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        timeout=180,
    )


async def _evidence_frames(video_path: Path, evidence_dir: Path, duration: float) -> list[str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timestamps = sorted({0.0, max(0.0, duration / 2), max(0.0, duration - 0.15)})
    output = []
    for index, timestamp in enumerate(timestamps, start=1):
        path = evidence_dir / f"frame-{index:02d}-{timestamp:.2f}s.jpg"
        await _run_command(
            [
                "ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
                "-i", str(video_path), "-frames:v", "1", "-vf", "scale=640:-2", str(path),
            ],
            timeout=60,
        )
        output.append(path.name)
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def supplement_web_footage(
    *,
    task_dir: Path,
    manifest: dict,
    query_plan: list[dict[str, str]],
    target_total: int,
    orientation: str,
    script: str,
    log: LogCallback | None = None,
) -> dict:
    """Append rights-ledgered web clips until ``target_total`` is reached."""
    manifest_file = task_dir / "footage" / "manifest.json"
    raw_dir = task_dir / "footage" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_root = task_dir / "footage" / "evidence"
    used_sources = {str(clip.get("source_page_url") or "") for clip in manifest.get("clips", [])}

    if manifest.get("provider_id") == "opencli-web":
        manifest["provider"] = "OpenCLI Web: Bilibili + YouTube"
    else:
        manifest["provider"] = "Hybrid: Wikimedia Commons + OpenCLI Web"
        manifest["provider_id"] = "hybrid-opencli"
    manifest["requested_clip_count"] = target_total
    manifest["rights_review_required"] = True
    manifest.setdefault("publication_blockers", [])
    blocker = "Review reuse rights for every Bilibili/YouTube clip before publication"
    if blocker not in manifest["publication_blockers"]:
        manifest["publication_blockers"].append(blocker)
    manifest["status"] = "searching"
    manifest["updated_at"] = _now()
    _write_manifest(manifest_file, manifest)

    for shot_index, shot in enumerate(query_plan):
        if len(manifest.get("clips", [])) >= target_total:
            break
        query = str(shot.get("query") or "").strip()
        if not query:
            continue
        _emit(log, f"Web footage: searching Bilibili + YouTube for '{query}'")
        results: dict[str, list[dict]] = {"bilibili": [], "youtube": []}
        found = await asyncio.gather(
            search_bilibili(query), search_youtube(query), return_exceptions=True
        )
        for platform, value in zip(("bilibili", "youtube"), found, strict=True):
            if isinstance(value, Exception):
                manifest.setdefault("errors", []).append(
                    {"query": query, "stage": f"{platform}-search", "message": str(value)}
                )
            else:
                results[platform] = value

        preferred = ("bilibili", "youtube") if shot_index % 2 == 0 else ("youtube", "bilibili")
        candidate = next(
            (
                item
                for platform in preferred
                for item in results[platform]
                if item["source_page_url"] not in used_sources
            ),
            None,
        )
        if candidate is None:
            manifest.setdefault("errors", []).append(
                {"query": query, "stage": "web-selection", "message": "No unique web candidate found"}
            )
            continue

        excerpt = matching_script_excerpt(script, query)
        _emit(log, f"Web footage: asking Gemini Web to select a trim for {candidate['source_page_url']}")
        analysis = await analyze_candidate_link(candidate, excerpt)
        raw_path: Path | None = None
        try:
            if candidate["platform"] == "bilibili":
                raw_path = await _download_bilibili(candidate, raw_dir)
            else:
                raw_path = await _download_youtube(candidate, raw_dir)
            media = await _probe(raw_path)
            analysis = _fit_analysis_to_media(analysis, media["duration_seconds"])
            clip_id = f"clip-{len(manifest.get('clips', [])) + 1:02d}"
            destination = task_dir / "footage" / f"{clip_id}.mp4"
            await _trim(raw_path, destination, analysis, orientation)
            trimmed = await _probe(destination)
            evidence_names = await _evidence_frames(
                destination,
                evidence_root / clip_id,
                trimmed["duration_seconds"],
            )
        except Exception as exc:  # noqa: BLE001 - try the next query/candidate
            manifest.setdefault("errors", []).append(
                {
                    "query": query,
                    "stage": "web-download-edit",
                    "source_page_url": candidate["source_page_url"],
                    "message": str(exc),
                }
            )
            _emit(log, f"Web footage candidate failed: {exc}")
            continue

        # Keep failed downloads for diagnosis. Remove the exact raw file only
        # after the normalized derivative and evidence frames are verified.
        if raw_path and raw_path.exists():
            raw_path.unlink()

        entry = {
            "id": clip_id,
            "query": query,
            "purpose": str(shot.get("purpose") or ""),
            **candidate,
            "duration_seconds": round(trimmed["duration_seconds"], 3),
            "source_duration_seconds": round(media["duration_seconds"], 3),
            "width": trimmed["width"],
            "height": trimmed["height"],
            "bytes": destination.stat().st_size,
            "mime_type": "video/mp4",
            "sha256": _sha256(destination),
            "local_path": destination.relative_to(task_dir).as_posix(),
            "status": "downloaded_and_trimmed",
            "analysis": analysis,
            "script_excerpt": excerpt,
            "evidence_frames": [
                f"footage/evidence/{clip_id}/{name}" for name in evidence_names
            ],
            "license": "Rights not verified — human review required",
            "license_code": "rights-review-required",
            "license_url": "",
            "attribution_required": True,
            "review_required": True,
            "rights_status": "review_required",
        }
        manifest.setdefault("clips", []).append(entry)
        used_sources.add(candidate["source_page_url"])
        manifest["updated_at"] = _now()
        _write_manifest(manifest_file, manifest)
        _emit(
            log,
            f"Web footage: {clip_id} trimmed to {analysis['start_seconds']:.1f}–"
            f"{analysis['end_seconds']:.1f}s via {analysis['analyzer']}",
        )

    if raw_dir.exists() and not any(raw_dir.iterdir()):
        raw_dir.rmdir()
    acquired = len(manifest.get("clips", []))
    manifest["status"] = "ready" if acquired >= target_total else ("partial" if acquired else "no_results")
    manifest["updated_at"] = _now()
    _write_manifest(manifest_file, manifest)
    return manifest
