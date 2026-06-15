import asyncio
import logging
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path


ProcessLogCallback = Callable[[str], None]


def command_to_string(command: str | Sequence[object]) -> str:
    if isinstance(command, str):
        return command.strip()
    return shlex.join(str(part) for part in command)


def log_process_result(
    *,
    name: str,
    command: str | Sequence[object],
    returncode: int | None,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    logger: logging.Logger,
    log: ProcessLogCallback | None = None,
    cwd: str | Path | None = None,
    stdout_log_limit: int | None = None,
    stderr_log_limit: int | None = None,
):
    def decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value

    stdout_text = decode(stdout).rstrip()
    stderr_text = decode(stderr).rstrip()

    def truncate(value: str, limit: int | None) -> str:
        if limit is None or len(value) <= limit:
            return value
        omitted = len(value) - limit
        return f"{value[:limit]}\n... <truncated {omitted} chars>"

    stdout_text = truncate(stdout_text, stdout_log_limit)
    stderr_text = truncate(stderr_text, stderr_log_limit)
    lines = [
        f"{name} command: {command_to_string(command)}",
    ]
    if cwd:
        lines.append(f"{name} cwd: {cwd}")
    if returncode is not None:
        lines.append(f"{name} exit code: {returncode}")
    if stdout_text:
        lines.append(f"{name} stdout:")
        lines.extend(stdout_text.splitlines())
    if stderr_text:
        lines.append(f"{name} stderr:")
        lines.extend(stderr_text.splitlines())

    message = "\n".join(lines)
    if returncode == 0:
        logger.info(message)
    else:
        logger.error(message)

    if log:
        for line in lines:
            log(line)


def run_capture_logged(
    *,
    name: str,
    command: Sequence[object],
    logger: logging.Logger,
    log: ProcessLogCallback | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    text: bool = True,
    stdout_log_limit: int | None = None,
    stderr_log_limit: int | None = None,
) -> subprocess.CompletedProcess:
    """Run a short subprocess whose output must be captured for parsing, while
    still logging the result. Callers can cap logged stdout/stderr for noisy
    commands without changing the returned ``CompletedProcess``.

    Unlike a bare ``subprocess.run``, a timeout does not swallow the output: the
    partial stdout/stderr captured before the process was killed is logged
    (which is exactly what you need to diagnose a hang) before the
    ``TimeoutExpired`` is re-raised.
    """
    cmd = [str(part) for part in command]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        log_process_result(
            name=f"{name} (TIMEOUT after {timeout}s)",
            command=command,
            returncode=None,
            stdout=exc.stdout,
            stderr=exc.stderr,
            logger=logger,
            log=log,
            cwd=cwd,
            stdout_log_limit=stdout_log_limit,
            stderr_log_limit=stderr_log_limit,
        )
        raise
    log_process_result(
        name=name,
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        logger=logger,
        log=log,
        cwd=cwd,
        stdout_log_limit=stdout_log_limit,
        stderr_log_limit=stderr_log_limit,
    )
    return result


async def stream_subprocess(
    *,
    name: str,
    command: Sequence[object],
    logger: logging.Logger,
    log: ProcessLogCallback | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    env: dict | None = None,
) -> tuple[int | None, str]:
    """Run a long subprocess, streaming its merged stdout/stderr line-by-line to
    the logs *as it is produced* instead of dumping everything after it exits.

    This is what lets you watch TTS synthesis and video rendering progress live
    (in both the start.sh log and the in-app LogPanel) rather than staring at a
    silent terminal for minutes. It is ``async`` so the event loop — and the SSE
    log stream feeding the UI — stays responsive while the child runs.

    Splitting on both ``\\n`` and ``\\r`` means carriage-return progress bars
    (tqdm and friends) stream live too. Returns ``(returncode, captured_text)``.
    On timeout the child is killed and ``TimeoutError`` is raised; everything it
    printed up to that point has already been logged.
    """

    def emit(message: str):
        # Prefer the task callback when present: it already mirrors the line to
        # the root logger (start.sh log) with the task id, the per-task
        # pipeline.log, and the SSE stream. Falling through to the module logger
        # would double-log every line to start.sh.
        if log is not None:
            log(message)
        else:
            logger.info(message)

    cmd = [str(part) for part in command]
    emit(f"{name} command: {command_to_string(command)}")
    if cwd:
        emit(f"{name} cwd: {cwd}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        env=env,
    )

    captured: list[str] = []

    async def pump():
        assert proc.stdout is not None
        buffer = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            parts = re.split(rb"[\r\n]", buffer)
            buffer = parts.pop()
            for part in parts:
                line = part.decode(errors="replace").rstrip()
                if line:
                    captured.append(line)
                    emit(f"{name}| {line}")
        tail = buffer.decode(errors="replace").rstrip()
        if tail:
            captured.append(tail)
            emit(f"{name}| {tail}")

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        emit(f"{name} TIMEOUT after {timeout}s — process killed (partial output above)")
        raise TimeoutError(f"{name} timed out after {timeout}s") from None

    emit(f"{name} exit code: {proc.returncode}")
    return proc.returncode, "\n".join(captured)
