import asyncio
import html
import os
import subprocess
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

from core.events import EVENT_BUS
from core.paths import GAME_LOG

VALHEIM_SERVICE_UNIT = "valheim.service"


def _read_file_tail(num_lines: int = 250, chunk_size: int = 8192) -> str:
    """Efficiently read the last `num_lines` of the log file using reverse-seeking."""
    if not GAME_LOG.exists():
        return "Log file not found."

    try:
        with open(GAME_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size == 0:
                return "Log file is currently empty."

            buffer = bytearray()
            lines_found = 0
            offset = file_size

            while offset > 0 and lines_found <= num_lines:
                read_len = min(chunk_size, offset)
                offset -= read_len
                f.seek(offset)
                chunk = f.read(read_len)
                buffer = chunk + buffer
                lines_found = buffer.count(b"\n")

            text = buffer.decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            return "".join(lines[-num_lines:])
    except Exception as e:
        return f"Error reading log file: {e}"


def read_journalctl_tail(num_lines: int = 250, unit: str = VALHEIM_SERVICE_UNIT) -> Optional[str]:
    """Fetch the latest `num_lines` of output from `journalctl --user -u valheim.service`."""
    try:
        res = subprocess.run(
            [
                "journalctl",
                "--user",
                "-u",
                unit,
                "--invocation=0",
                "-n",
                str(num_lines),
                "-o",
                "cat",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode != 0:
            res = subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    unit,
                    "-n",
                    str(num_lines),
                    "-o",
                    "cat",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )

        if res.returncode == 0 and res.stdout:
            stripped = res.stdout.strip()
            if stripped and not stripped.startswith("-- No entries --"):
                return res.stdout if res.stdout.endswith("\n") else res.stdout + "\n"
    except Exception:
        pass
    return None


def read_log_tail(num_lines: int = 250, chunk_size: int = 8192) -> str:
    """Fetch the latest `num_lines` of server logs via journalctl, with fallback to GAME_LOG."""
    journal_text = read_journalctl_tail(num_lines=num_lines)
    if journal_text:
        return journal_text
    return _read_file_tail(num_lines=num_lines, chunk_size=chunk_size)


def parse_players() -> Tuple[int, List[str]]:
    """Parse connected player count and character names using PLAYER_TRACKER."""
    try:
        from core.players import PLAYER_TRACKER

        summary = PLAYER_TRACKER.get_summary()
        return summary["online_count"], summary["online_players"]
    except Exception:
        return 0, []


async def log_stream_generator(
    initial_lines: int = 100,
    request: Optional[Any] = None,
) -> AsyncGenerator[str, None]:
    """SSE generator streaming live log chunks via `journalctl --user -u valheim.service -f`."""
    # Send initial tail if available
    initial_text = read_log_tail(num_lines=initial_lines)
    if initial_text:
        escaped_chunk = html.escape(initial_text)
        data_lines = escaped_chunk.replace("\n", "&#10;")
        yield f"event: log-chunk\ndata: {data_lines}\n\n"

    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "--user",
            "-u",
            VALHEIM_SERVICE_UNIT,
            "-f",
            "-n",
            "0",
            "-o",
            "cat",
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        while not EVENT_BUS.is_shutting_down():
            if request:
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    break

            if proc.stdout is None:
                break

            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue

            if not line_bytes:
                break

            new_text = line_bytes.decode("utf-8", errors="replace")
            if new_text:
                escaped_chunk = html.escape(new_text)
                data_lines = escaped_chunk.replace("\n", "&#10;")
                yield f"event: log-chunk\ndata: {data_lines}\n\n"
        return
    except Exception:
        pass
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # Fallback file-polling loop if journalctl subprocess is unavailable
    last_pos = 0
    if GAME_LOG.exists():
        last_pos = GAME_LOG.stat().st_size

    while not EVENT_BUS.is_shutting_down():
        if request:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

        await asyncio.sleep(0.5)
        if EVENT_BUS.is_shutting_down():
            break
        if not GAME_LOG.exists():
            continue

        try:
            curr_size = GAME_LOG.stat().st_size
            if curr_size < last_pos:
                last_pos = 0

            if curr_size > last_pos:
                with open(GAME_LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_pos)
                    new_text = f.read()
                    last_pos = f.tell()

                if new_text:
                    escaped_chunk = html.escape(new_text)
                    data_lines = escaped_chunk.replace("\n", "&#10;")
                    yield f"event: log-chunk\ndata: {data_lines}\n\n"
        except Exception:
            pass
