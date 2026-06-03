#!/usr/bin/env python3
"""Wait for a call to finish + recording to be ready, then download.

Usage:
    python wait_and_fetch_recording.py <call_id> <output_path> [timeout_sec]

This is the "smart" companion to ``fetch_recording.py``. It opens the
SSE event stream for the call, watches for ``outcome`` (call ended)
followed by ``recording_ready`` (up to ~60s after outcome — Twilio
processing window), then invokes ``fetch_recording.py`` to download
the mp3. If the recording is still processing (exit 75) it retries up
to 3 times with a 30s gap.

Reads:
    AI_CALL_AGENT_API_KEY       — required, sent as ``X-API-Key``.
    AI_CALL_AGENT_BASE_URL      — optional, defaults to
                                  ``https://gw.vox-bot.live``.

Timeout default is 300s (5 minutes). Increase for long calls.

On success, prints one JSON line:
    {"status": "ok", "local_path": "...", "size_bytes": N,
     "outcome_type": "..."}

Exit codes match ``fetch_recording.py`` (0/1/2/4/75) plus:
    3  — outcome never arrived within ``timeout_sec``.
    5  — outcome arrived but recording_ready did not within 60s
         AND every fetch retry still got 202.

Pure stdlib — no third-party deps. The script intentionally uses
``http.client`` directly (not ``urllib.request``) because SSE needs
incremental line-by-line reads on a long-running response and
``urllib`` buffers awkwardly. The API key is NEVER printed.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import selectors
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse


_DEFAULT_BASE_URL = "https://gw.vox-bot.live"
_DEFAULT_TIMEOUT_SEC = 300
_POST_OUTCOME_WINDOW_SEC = 60
_FETCH_RETRY_COUNT = 3
_FETCH_RETRY_DELAY_SEC = 30
# Soft per-read timeout on the SSE socket. The server sends heartbeats
# every ~15s, so a missed heartbeat past this window is a dead socket.
_SSE_READ_TIMEOUT_SEC = 45

# See fetch_recording.py — same defence-in-depth pattern.
_CALL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _die(exit_code: int, message: str) -> NoReturn:
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(exit_code)


def _validate_call_id(call_id: str) -> None:
    if not _CALL_ID_RE.fullmatch(call_id):
        _die(
            2,
            f"Invalid call_id format: {call_id!r} (expected alphanumeric "
            "/ hyphen / underscore, 1-128 chars).",
        )


def _resolve_api_key() -> str:
    key = os.environ.get("AI_CALL_AGENT_API_KEY", "").strip()
    if not key:
        _die(
            1,
            "AI_CALL_AGENT_API_KEY is not set. Export the API key before "
            "running this script.",
        )
    return key


def _resolve_base_url() -> str:
    base = os.environ.get("AI_CALL_AGENT_BASE_URL", _DEFAULT_BASE_URL).strip()
    return (base or _DEFAULT_BASE_URL).rstrip("/")


def _open_sse(base_url: str, call_id: str, api_key: str) -> http.client.HTTPResponse:
    """Open the SSE stream and return the live response object.

    Caller MUST call ``.close()`` (and the underlying connection's
    ``.close()``) when done — we expose the connection via the
    ``_connection`` attribute we attach for convenience.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        _die(2, f"Unsupported base URL scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        _die(2, f"Could not extract host from base URL: {base_url!r}")
    port = parsed.port  # may be None — http.client picks default
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, port=port, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, port=port, timeout=30)

    path = f"/calls/{call_id}/events"
    headers = {
        "X-API-Key": api_key,
        "Accept": "text/event-stream",
        "User-Agent": "ai-call-agent-skill/wait_and_fetch_recording",
    }
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
    except (socket.gaierror, ConnectionError, OSError) as exc:
        _die(2, f"Failed to connect to {base_url}: {exc!s}")

    if response.status == 401:
        _die(1, "Auth failed (401). Check AI_CALL_AGENT_API_KEY.")
    if response.status == 404:
        _die(4, f"Call not found: {call_id} (404).")
    if response.status != 200:
        _die(2, f"SSE endpoint returned {response.status}.")

    # Don't monkey-patch the underlying socket here — the old
    # ``response.fp.raw._sock`` path is brittle (it differs across
    # Python versions, TLS wrappers, and chunked encoding) and a
    # silent failure to set it left the SSE read with no timeout
    # at all. Instead, ``_watch_events`` polls the response via
    # ``selectors`` with an explicit timeout per readline. This
    # works regardless of socket internals.
    response._connection = conn  # type: ignore[attr-defined]
    return response


def _watch_events(
    response: http.client.HTTPResponse,
    *,
    overall_deadline: float,
) -> str:
    """Parse SSE events until ``outcome`` + ``recording_ready`` arrive.

    Returns the ``outcome_type`` string from the outcome event. Exits
    the process with exit code 3 if the overall deadline elapses
    without outcome.

    Implementation: SSE messages are blocks of ``key: value`` lines
    terminated by a blank line (per WHATWG SSE spec). We accumulate
    ``event:`` and ``data:`` lines, then process on the blank-line
    delimiter. The underlying socket has no socket-level timeout —
    we enforce per-read deadlines via ``selectors`` so a hung
    connection surfaces as a fatal error within the heartbeat
    window (the server sends a ``heartbeat`` event every ~15s).

    Order independence: a reconnect (Last-Event-ID replay) may
    deliver ``recording_ready`` before ``outcome``. We track each
    signal separately and only return once both have been observed.
    """
    event_type: str | None = None
    data_chunks: list[str] = []
    outcome_type: str | None = None
    recording_ready_seen = False
    post_outcome_deadline: float | None = None

    sel = selectors.DefaultSelector()
    try:
        sel.register(response.fileno(), selectors.EVENT_READ)
    except (ValueError, OSError) as exc:
        _die(2, f"SSE response is not selectable: {exc!s}")

    try:
        while True:
            now = time.monotonic()
            if now >= overall_deadline:
                _die(
                    3,
                    "Timed out waiting for the call outcome event. The "
                    "call may still be in progress; re-run with a "
                    "larger timeout.",
                )
            if (
                post_outcome_deadline is not None
                and now >= post_outcome_deadline
            ):
                # Outcome arrived, recording_ready did not within
                # _POST_OUTCOME_WINDOW_SEC. Fall through to caller —
                # it will still try fetch_recording.py (Twilio may
                # have the file even if our SSE signal never came).
                return outcome_type or ""

            # How long to wait for the next byte. Capped by whichever
            # deadline (overall vs post-outcome) is closer, but never
            # longer than the heartbeat window — past that we treat
            # the connection as dead.
            wait_budget = overall_deadline - now
            if post_outcome_deadline is not None:
                wait_budget = min(wait_budget, post_outcome_deadline - now)
            wait_budget = min(wait_budget, _SSE_READ_TIMEOUT_SEC)
            if wait_budget <= 0:
                # Loop top will handle the deadline branches.
                continue

            ready = sel.select(timeout=wait_budget)
            if not ready:
                # No bytes within the heartbeat window — connection
                # is dead. Caller can re-run.
                _die(
                    2,
                    "SSE socket idle past heartbeat window "
                    "— connection lost.",
                )

            try:
                line = response.readline()
            except (TimeoutError, socket.timeout):
                # socket.timeout is NOT TimeoutError on Python <= 3.10
                # — must catch both for forward + backward compat.
                _die(
                    2,
                    "SSE socket idle past heartbeat window "
                    "— connection lost.",
                )
            except (ConnectionError, OSError) as exc:
                _die(2, f"SSE connection error: {exc!s}")

            if not line:
                # Server closed the stream. If we already have an
                # outcome we can still try the fetch.
                if outcome_type is not None:
                    return outcome_type
                _die(2, "SSE stream closed before outcome arrived.")

            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded == "":
                # End of message — flush.
                if event_type and data_chunks:
                    payload_raw = "\n".join(data_chunks)
                    payload: dict[str, object] = {}
                    if payload_raw:
                        try:
                            parsed = json.loads(payload_raw)
                        except json.JSONDecodeError as jde:
                            # A malformed payload USED to silently
                            # drop the event — meaning a truncated
                            # outcome write would make the script
                            # hang to timeout instead of detecting
                            # the event. Log + still treat the event
                            # as received with an empty payload.
                            sys.stderr.write(
                                f"Warning: malformed JSON in SSE "
                                f"{event_type!r} event: {jde!s}\n"
                            )
                        else:
                            if isinstance(parsed, dict):
                                payload = parsed
                    if event_type == "outcome" and outcome_type is None:
                        outcome_type = str(
                            payload.get("outcome_type") or "unknown"
                        )
                        post_outcome_deadline = (
                            time.monotonic() + _POST_OUTCOME_WINDOW_SEC
                        )
                        if recording_ready_seen:
                            return outcome_type
                    elif event_type == "recording_ready":
                        recording_ready_seen = True
                        if outcome_type is not None:
                            return outcome_type
                    # Other events (status_change, ask_user, heartbeat,
                    # answered) are no-ops for this script.
                event_type = None
                data_chunks = []
                continue

            if decoded.startswith(":"):
                # SSE comment (typically keepalive) — ignore.
                continue
            if decoded.startswith("event:"):
                event_type = decoded[len("event:"):].strip()
            elif decoded.startswith("data:"):
                data_chunks.append(decoded[len("data:"):].lstrip(" "))
            # ``id:`` and ``retry:`` lines are ignored — we don't reconnect.
    finally:
        try:
            sel.close()
        except OSError:
            pass


def _invoke_fetch(call_id: str, output_path: str) -> dict:
    """Run ``fetch_recording.py``; retry 3× on exit 75.

    Returns the parsed stdout JSON on success. Exits the process with
    the appropriate exit code on terminal failure.
    """
    script = Path(__file__).with_name("fetch_recording.py")
    if not script.exists():
        _die(
            2,
            f"Sibling script not found: {script!s}. "
            "Both scripts must ship in the same scripts/ directory.",
        )

    for attempt in range(1, _FETCH_RETRY_COUNT + 1):
        result = subprocess.run(
            [sys.executable, str(script), call_id, output_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                _die(
                    2,
                    "fetch_recording.py reported success but stdout was "
                    f"not parseable JSON: {result.stdout!r}",
                )
        if result.returncode == 75 and attempt < _FETCH_RETRY_COUNT:
            sys.stderr.write(
                f"Recording still processing (attempt {attempt}/"
                f"{_FETCH_RETRY_COUNT}); waiting "
                f"{_FETCH_RETRY_DELAY_SEC}s.\n"
            )
            time.sleep(_FETCH_RETRY_DELAY_SEC)
            continue
        # Any other exit OR the final 75: forward the sub-script's
        # stderr so the caller sees why it failed. The previous
        # version skipped stderr forwarding on the final 75 path,
        # leaving the user with bare exit 5 and zero diagnostic.
        sys.stderr.write(result.stderr)
        if result.returncode == 75:
            sys.exit(5)
        sys.exit(result.returncode)

    sys.exit(5)  # unreachable; keeps the type checker happy


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        _die(
            2,
            "Usage: wait_and_fetch_recording.py "
            "<call_id> <output_path> [timeout_sec]",
        )
    call_id = argv[1].strip()
    output_path = argv[2].strip()
    if not call_id or not output_path:
        _die(2, "call_id and output_path must both be non-empty.")
    _validate_call_id(call_id)
    try:
        timeout_sec = int(argv[3]) if len(argv) == 4 else _DEFAULT_TIMEOUT_SEC
    except ValueError:
        _die(2, f"timeout_sec must be an integer, got {argv[3]!r}.")
    if timeout_sec <= 0:
        _die(2, "timeout_sec must be positive.")

    base = _resolve_base_url()
    key = _resolve_api_key()

    overall_deadline = time.monotonic() + timeout_sec
    response = _open_sse(base, call_id, key)
    try:
        outcome_type = _watch_events(
            response, overall_deadline=overall_deadline,
        )
    finally:
        # Narrow exception: only OS-level close errors are silently
        # swallowed (sockets sometimes raise OSError when the peer
        # has already closed). Anything broader would hide real
        # logic bugs like AttributeError on a half-built response.
        try:
            response.close()
        except OSError:
            pass
        conn = getattr(response, "_connection", None)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    result = _invoke_fetch(call_id, output_path)
    result["outcome_type"] = outcome_type
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
