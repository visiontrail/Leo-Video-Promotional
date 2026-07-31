"""Assemble the render project: spine, scene files, vendored assets, lint.

The spine (``index.html``) is generated, never authored by a model. It owns the
three things a wrong answer would silently break — the audio offset, the scene
mount times, and the HyperFrames runtime contract — so a creative failure in a
scene file costs one bad-looking scene instead of a blank 12-minute video.

Everything the earlier renders got wrong is enforced here:

* the root composition carries ``data-start="0"`` and a registered timeline;
* every timed element has ``class="clip"``, an ``id``, and a track index;
* clips on one track never overlap (each layer gets its own track);
* fonts and GSAP are local files, so nothing depends on the network at capture.
"""

from __future__ import annotations

import html
import logging
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from backend import config
from backend.pipeline import scene_kit
from backend.pipeline.process_logging import run_capture_logged

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# Track layout. Same-track clips may not overlap, so each concern gets its own
# track and adjacent scenes alternate between two tracks — that way a scene may
# hold a frame or two past its neighbour's start without tripping the linter.
TRACK_SCENE_A = 0
TRACK_SCENE_B = 1
TRACK_CAPTION = 4
TRACK_CHROME = 5
TRACK_CHARACTER = 6

LINT_TIMEOUT = 180
# `inspect` drives the composition in headless Chrome and samples the timeline,
# so it costs real time on a long episode — but it is the only check that sees
# text overflowing a card or two blocks landing on top of each other.
INSPECT_TIMEOUT = 900


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def vendor_assets(task_dir: Path, *, include_lottie: bool = False) -> None:
    """Copy the runtime libraries into the project so capture never hits a CDN.

    HyperFrames sandboxes asset access to the project directory, and the render
    host may be offline; a `<script src="https://...">` that fails leaves every
    timeline unregistered, which is exactly the failure mode that produced blank
    frames before.
    """
    vendor = task_dir / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)

    gsap_src = config.HYPERFRAME_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    gsap_dst = vendor / "gsap.min.js"
    if gsap_src.exists():
        shutil.copyfile(gsap_src, gsap_dst)
    elif not gsap_dst.exists():
        raise FileNotFoundError(
            f"GSAP not found at {gsap_src}. Run `npm install gsap` in {config.HYPERFRAME_DIR}."
        )

    if include_lottie:
        lottie_src = (
            config.HYPERFRAME_DIR / "node_modules" / "lottie-web" / "build" / "player" / "lottie.min.js"
        )
        if lottie_src.exists():
            shutil.copyfile(lottie_src, vendor / "lottie.min.js")


def stage_character(task_dir: Path) -> str | None:
    """Copy the host-character Lottie into the project; return its relative path.

    Returns None when the asset or the player is missing, so the caller can drop
    the overlay rather than emit a broken reference.
    """
    source = config.PROJECT_ROOT / "assets" / "lottie" / "podcast_host.json"
    if not source.exists():
        return None
    if not (task_dir / "vendor" / "lottie.min.js").exists():
        return None
    dest = task_dir / "assets" / "podcast_host.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return "assets/podcast_host.json"


def write_scene_files(task_dir: Path, plans: Sequence[scene_kit.ScenePlan]) -> list[Path]:
    """Render every scene plan to ``compositions/<scene-id>.html``."""
    comps = task_dir / "compositions"
    comps.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for plan in plans:
        path = comps / f"{plan.id}.html"
        path.write_text(scene_kit.render_scene(plan), encoding="utf-8")
        written.append(path)
    return written


def _caption_clips(storyboard: dict) -> list[str]:
    """One caption clip per spoken line, clamped so neighbours never overlap."""
    lines: list[dict] = []
    for scene in storyboard.get("scenes", []):
        lines.extend(scene.get("lines", []))
    lines.sort(key=lambda line: line["start"])

    out: list[str] = []
    for i, line in enumerate(lines):
        start = round(float(line["start"]), 2)
        next_start = round(float(lines[i + 1]["start"]), 2) if i + 1 < len(lines) else None
        duration = float(line["duration"])
        if next_start is not None:
            # Round-off in the silence map can leave a caption ending a
            # hundredth of a second after its successor starts, which the
            # linter reports as an overlap. Clamp instead of trusting the map.
            duration = min(duration, max(0.2, next_start - start - 0.02))
        duration = round(max(0.2, duration), 2)
        text = _esc(line["text"])
        out.append(
            f'      <div id="cap-{i:03d}" class="clip caption" data-start="{start}" '
            f'data-duration="{duration}" data-track-index="{TRACK_CAPTION}">'
            f'<span class="caption-inner">{text}</span></div>'
        )
    return out


def _scene_mounts(mounts: Sequence[dict]) -> list[str]:
    """Mount tags for every scene, alternating tracks so neighbours can abut.

    Adjacent scenes share a boundary time. Putting them on the same track makes
    a one-hundredth-of-a-second rounding overlap a lint error, so odd and even
    scenes alternate between two tracks — visual layering is unaffected because
    HyperFrames uses CSS z-index for that, not the track index.
    """
    tags: list[str] = []
    for i, mount in enumerate(mounts):
        track = TRACK_SCENE_A if i % 2 == 0 else TRACK_SCENE_B
        tags.append(
            f'      <div id="mount-{mount["id"]}" class="clip scene-mount"\n'
            f'           data-composition-id="{mount["id"]}"\n'
            f'           data-composition-src="compositions/{mount["id"]}.html"\n'
            f'           data-start="{round(float(mount["start"]), 2)}" '
            f'data-duration="{round(float(mount["duration"]), 2)}"\n'
            f'           data-track-index="{track}"></div>'
        )
    return tags


def _spine_css(theme: scene_kit.Theme) -> str:
    return f"""
      * {{ margin:0; padding:0; box-sizing:border-box; }}
      html, body {{ width:1920px; height:1080px; overflow:hidden; background:{theme.bg}; }}
      .scene-mount {{ position:absolute; inset:0; }}

      .caption {{
        position:absolute; left:210px; right:210px; bottom:96px;
        display:flex; justify-content:center; align-items:flex-end;
        text-align:center; pointer-events:none;
      }}
      .caption-inner {{
        font:500 34px {scene_kit.SANS}; line-height:1.36;
        color:{theme.caption_ink};
        background:{theme.caption_bg};
        padding:14px 30px; border-radius:14px;
        box-shadow:0 12px 44px rgba(0,0,0,.45);
        max-width:1360px;
      }}

      .chrome {{ position:absolute; inset:0; pointer-events:none; }}
      .progress-track {{
        position:absolute; left:0; right:0; bottom:0; height:6px;
        background:rgba(128,128,128,.18);
      }}
      .progress-fill {{
        height:100%; width:100%; transform-origin:0 50%;
        background:linear-gradient(90deg, {scene_kit.ACCENTS["amber"]}, {scene_kit.ACCENTS["coral"]});
      }}
      .character {{ position:absolute; right:64px; bottom:132px; width:210px; height:210px;
        pointer-events:none; }}
      .character #character-stage {{ width:100%; height:100%; }}
      .brand {{
        position:absolute; left:56px; bottom:36px;
        font:600 22px {scene_kit.SANS}; letter-spacing:.18em; text-transform:uppercase;
        color:{scene_kit._rgba(theme.ink, 0.45)};
      }}
"""


def build_spine(
    storyboard: dict,
    *,
    audio_src: str,
    mounts: Sequence[dict],
    brand: str = "",
    theme: scene_kit.Theme = scene_kit.DEFAULT_THEME,
    character_src: str | None = None,
) -> str:
    """The root composition: mounts, captions, audio, progress chrome.

    ``mounts`` is the full cover of the timeline — title card, every storyboard
    scene, and the outro — as ``{"id", "start", "duration"}`` records. Any gap
    left here renders as a black frame, so the composer builds the list to span
    0 to ``total_duration`` with no holes.
    """
    total = float(storyboard["total_duration"])
    content_start = float(storyboard["content_start"])
    audio_duration = float(storyboard["audio_duration"])

    mount_tags = _scene_mounts(mounts)
    captions = _caption_clips(storyboard)

    brand_html = (
        f'        <div class="brand">{_esc(brand)}</div>\n' if brand else ""
    )

    # Optional animated host, driven by the HyperFrames lottie adapter: the
    # player is registered on window.__hfLottie and seeked by the runtime, so it
    # must be created with autoplay off.
    character_html = ""
    character_script = ""
    if character_src:
        character_html = (
            f'      <div id="character" class="clip character" data-start="{content_start}" '
            f'data-duration="{audio_duration}" data-track-index="{TRACK_CHARACTER}">'
            f'<div id="character-stage"></div></div>\n'
        )
        character_script = f"""
    <script src="vendor/lottie.min.js"></script>
    <script>
      window.__hfLottie = window.__hfLottie || [];
      window.__hfLottie.push(lottie.loadAnimation({{
        container: document.getElementById("character-stage"),
        renderer: "svg", loop: true, autoplay: false,
        path: "{_esc(character_src)}",
      }}));
    </script>"""

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <style>{_spine_css(theme)}    </style>
  </head>
  <body>
    <div id="root" data-composition-id="root" data-start="0" data-duration="{total}"
         data-width="1920" data-height="1080"
         style="position:relative;width:1920px;height:1080px;overflow:hidden;">

{chr(10).join(mount_tags)}

{chr(10).join(captions)}

{character_html}      <div id="chrome" class="clip chrome" data-start="0" data-duration="{total}"
           data-track-index="{TRACK_CHROME}">
{brand_html}        <div class="progress-track"><div id="progress-fill" class="progress-fill"></div></div>
      </div>

      <audio id="narration" src="{_esc(audio_src)}" data-start="{content_start}"
             data-duration="{audio_duration}" data-track-index="9" data-volume="1"></audio>
    </div>

    <script src="vendor/gsap.min.js"></script>
    <script>
      window.__timelines = window.__timelines || {{}};
      (function () {{
        const tl = gsap.timeline({{ paused: true }});
        tl.fromTo("#progress-fill", {{ scaleX: 0 }}, {{ scaleX: 1, duration: {total}, ease: "none" }}, 0);
        window.__timelines["root"] = tl;
      }})();
    </script>{character_script}
  </body>
</html>
"""


def write_spine(task_dir: Path, html_text: str) -> Path:
    path = task_dir / "index.html"
    path.write_text(html_text, encoding="utf-8")
    # A stale entry file from an older run trips the multiple_root_compositions
    # check and can duplicate the audio track.
    stale = task_dir / "composition.html"
    if stale.exists():
        stale.unlink()
    return path


def _hyperframes_bin() -> list[str]:
    local = config.HYPERFRAME_DIR / "node_modules" / ".bin" / "hyperframes"
    if local.exists():
        return [str(local)]
    return ["npx", "--yes", f"hyperframes@{config.HYPERFRAMES_VERSION}"]


def lint_project(task_dir: Path, log: LogCallback | None = None) -> tuple[bool, str]:
    """Run ``hyperframes lint``; return (clean, output).

    Warnings are tolerated — errors are not. A composition that lints with an
    error is exactly the composition that renders blank, so the caller treats a
    False here as a repair trigger rather than a cosmetic note.
    """
    result = run_capture_logged(
        name="HyperFrames lint",
        command=_hyperframes_bin() + ["lint", str(task_dir)],
        logger=logger,
        log=None,
        timeout=LINT_TIMEOUT,
    )
    output = f"{result.stdout}\n{result.stderr}".strip()
    errors = [line.strip() for line in output.splitlines() if "✗" in line]
    clean = not errors
    if log:
        if clean:
            log(f"Lint: clean ({output.count('⚠')} warning(s))")
        else:
            log(f"Lint: {len(errors)} error(s)")
            for line in errors[:12]:
                log(f"  {line}")
    return clean, output


def inspect_project(task_dir: Path, log: LogCallback | None = None) -> tuple[bool, list[str]]:
    """Run ``hyperframes inspect``; return (clean, error findings).

    Unlike lint, these are *layout* failures — overlapping text, content spilling
    out of a card. They do not stop the render, but they are what makes an
    otherwise correct scene look broken, so the director gets a chance to fix
    them before capture starts.
    """
    result = run_capture_logged(
        name="HyperFrames inspect",
        command=_hyperframes_bin() + ["inspect", str(task_dir)],
        logger=logger,
        log=None,
        timeout=INSPECT_TIMEOUT,
    )
    output = f"{result.stdout}\n{result.stderr}".strip()
    findings = [line.strip() for line in output.splitlines() if "✗" in line]
    if log:
        if findings:
            log(f"Inspect: {len(findings)} layout issue(s)")
            for line in findings[:8]:
                log(f"  {line}")
        else:
            log("Inspect: layout clean")
    return (not findings), findings
