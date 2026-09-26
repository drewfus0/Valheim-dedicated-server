import asyncio
import html
import os
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional, Tuple

from core.paths import GAME_LOG


def read_log_tail(num_lines: int = 250, chunk_size: int = 8192) -> str:
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
    """SSE generator streaming live log chunks to HTMX."""
    # Send initial tail if available
    initial_text = read_log_tail(num_lines=initial_lines)
    if initial_text:
        escaped_chunk = html.escape(initial_text)
        # Format as SSE event
        data_lines = escaped_chunk.replace("\n", "&#10;")
        yield f"event: log-chunk\ndata: {data_lines}\n\n"

    # Track file pointer for streaming new lines
    last_pos = 0
    if GAME_LOG.exists():
        last_pos = GAME_LOG.stat().st_size

    while True:
        if request:
            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

        await asyncio.sleep(0.5)
        if not GAME_LOG.exists():
            continue

        try:
            curr_size = GAME_LOG.stat().st_size
            if curr_size < last_pos:
                # File was rotated/truncated
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
