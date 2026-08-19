"""Project-local OpenCLI subprocess adapter.

Only the repository wrapper configured by :mod:`backend.config` is executed.
This module never installs a global npm package and never writes Claude skills
outside ``.claude/skills``.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend import config
from backend.pipeline.opencli_rate_limit import (
    is_rate_limited_command,
    wait_for_opencli_web_slot,
)


class OpenCLIError(RuntimeError):
    """An OpenCLI command could not produce a usable result."""


@dataclass(frozen=True)
class OpenCLIResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    path_parts = [
        str(config.PROJECT_ROOT / ".venv" / "bin"),
        str(config.PROJECT_ROOT / "tools" / "opencli" / "node_modules" / ".bin"),
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(part for part in path_parts if part)
    env.setdefault("OPENCLI_BROWSER_CONNECT_TIMEOUT", "20")
    env.setdefault("OPENCLI_BROWSER_COMMAND_TIMEOUT", str(config.OPENCLI_TIMEOUT))
    if config.OPENCLI_PROFILE:
        env["OPENCLI_PROFILE"] = config.OPENCLI_PROFILE
    return env


async def run_opencli(
    args: list[str],
    *,
    timeout: int | None = None,
    check: bool = True,
) -> OpenCLIResult:
    binary = Path(config.OPENCLI_BIN)
    if not binary.is_file():
        raise OpenCLIError(
            f"Project-local OpenCLI wrapper is missing at {binary}. "
            f"Run npm install --prefix {config.PROJECT_ROOT / 'tools' / 'opencli'}"
        )

    command = [str(binary), *[str(arg) for arg in args]]
    env = _environment()
    if is_rate_limited_command(args):
        # Pace before starting the subprocess so the provider-command timeout
        # measures the web operation, not time intentionally spent in queue.
        await asyncio.to_thread(
            wait_for_opencli_web_slot,
            str(args[0]).lower(),
            interval=config.OPENCLI_WEB_REQUEST_INTERVAL_SECONDS,
        )
        env["OPENCLI_WEB_REQUEST_SLOT_RESERVED"] = "1"
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(config.PROJECT_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout or config.OPENCLI_TIMEOUT
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise OpenCLIError(
            f"OpenCLI command timed out after {timeout or config.OPENCLI_TIMEOUT}s: "
            f"{' '.join(args[:3])}"
        ) from exc

    result = OpenCLIResult(
        args=tuple(args),
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr=stderr_bytes.decode("utf-8", errors="replace").strip(),
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown OpenCLI failure")[-1200:]
        raise OpenCLIError(
            f"OpenCLI {' '.join(args[:2])} failed with exit {result.returncode}: {detail}"
        )
    return result


def first_json(value: str) -> Any:
    """Recover the first JSON object/array from CLI prose or fenced output."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    raise OpenCLIError("Command output did not contain valid JSON")


async def browser_bridge_ready() -> bool:
    result = await run_opencli(["doctor"], timeout=30, check=False)
    combined = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "Everything looks good" in combined
