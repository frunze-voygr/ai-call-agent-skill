#!/usr/bin/env python3
"""Download an AI Call Agent call recording to local disk.

Usage:
    python fetch_recording.py <call_id> <output_path>

Reads the API key from the env var ``AI_CALL_AGENT_API_KEY``.
The gateway base URL defaults to ``https://gw.vox-bot.live`` but
can be overridden with ``AI_CALL_AGENT_BASE_URL`` (e.g. to point at
a staging gateway). The API key is NEVER printed or echoed.

Exit codes:
    0  — success; mp3 saved to ``output_path``.
    1  — auth failure (401).
    2  — other error (network failure, 5xx, malformed response).
    4  — recording not found (404 — call id unknown or admin denied).
   75  — recording still being processed by Twilio (202).
         Matches BSD ``EX_TEMPFAIL`` so wrapper scripts can retry.

On success, prints a single JSON line to stdout:
    {"status": "ok", "local_path": "...", "size_bytes": N}
On non-zero exits, a short reason is written to stderr.

Pure stdlib (``urllib``, ``json``, ``os``, ``sys``, ``pathlib``) so
this runs on Windows / macOS / Linux without ``pip install``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn


_DEFAULT_BASE_URL = "https://gw.vox-bot.live"

# Defence-in-depth against path traversal in the URL: a hostile
# call_id like ``../admin/other`` would interpolate into
# ``{base}/calls/../admin/other/recording`` and hit a different
# endpoint on the gateway. The real call_id is a short alphanumeric
# / hyphen / underscore string; refuse anything else before we
# build the URL.
_CALL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _die(exit_code: int, message: str) -> NoReturn:
    """Print *message* to stderr and exit with *exit_code*.

    Typed as :data:`NoReturn` so static checkers (mypy, pyright)
    know that callers don't fall through after a ``_die(...)`` call
    — every helper below relies on that to satisfy "function
    declares it returns ``str`` but the early-out branch never
    returns" diagnostics.
    """
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(exit_code)


def _validate_call_id(call_id: str) -> None:
    """Reject hostile / malformed call_ids before they reach the URL."""
    if not _CALL_ID_RE.fullmatch(call_id):
        _die(
            2,
            f"Invalid call_id format: {call_id!r} (expected alphanumeric "
            "/ hyphen / underscore, 1-128 chars).",
        )


def _resolve_api_key() -> str:
    """Return the API key from env, never logging the value itself."""
    key = os.environ.get("AI_CALL_AGENT_API_KEY", "").strip()
    if not key:
        _die(
            1,
            "AI_CALL_AGENT_API_KEY is not set. Export the API key before "
            "running this script.",
        )
    return key


def _resolve_base_url() -> str:
    """Return the gateway base URL with no trailing slash."""
    base = os.environ.get("AI_CALL_AGENT_BASE_URL", _DEFAULT_BASE_URL).strip()
    if not base:
        base = _DEFAULT_BASE_URL
    return base.rstrip("/")


def fetch_recording(call_id: str, output_path: str) -> int:
    """Fetch the recording for *call_id* and save it to *output_path*.

    Returns the exit code to pass to ``sys.exit``. Side effects:
    writes the mp3 bytes to disk on success and prints one JSON line
    to stdout; writes a short reason to stderr on failure.
    """
    _validate_call_id(call_id)
    base = _resolve_base_url()
    key = _resolve_api_key()
    url = f"{base}/calls/{call_id}/recording"
    # ``Accept`` is permissive — the server replies with audio/mpeg on
    # the 200 path and application/json on the 202 path; we handle
    # both. ``User-Agent`` makes our requests easy to grep in gateway
    # logs.
    request = urllib.request.Request(
        url,
        headers={
            "X-API-Key": key,
            "Accept": "audio/mpeg, application/json",
            "User-Agent": "ai-call-agent-skill/fetch_recording",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        # urllib treats every non-2xx as an exception. Distinguish
        # 202 (the "still processing" signal — NOT an error) from
        # real failures.
        status = exc.code
        try:
            body = exc.read()
        except OSError as read_exc:
            # Narrow exception: only socket / IO errors on the
            # error-body read are silently downgraded. Anything else
            # (TypeError, AttributeError, ...) is a programming bug
            # and should NOT be swallowed.
            sys.stderr.write(
                f"Warning: could not read error response body: "
                f"{read_exc!s}\n"
            )
            body = b""
        if status == 202:
            _die(75, "Recording is still being processed by Twilio.")
        if status == 401:
            _die(1, "Auth failed (401). Check AI_CALL_AGENT_API_KEY.")
        if status == 404:
            _die(
                4,
                f"Recording not found for call_id={call_id} (404).",
            )
        _die(2, f"HTTP {status} from gateway: {body[:200]!r}")
    except urllib.error.URLError as exc:
        _die(2, f"Network error contacting gateway: {exc.reason!r}")
    except TimeoutError:
        _die(2, "Gateway timed out (60s).")

    if status != 200:
        _die(2, f"Unexpected status {status} from gateway.")

    # 200 path — body is the mp3 bytes. Persist to disk.
    out = Path(output_path).expanduser()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
    except OSError as exc:
        _die(2, f"Failed to write {out!s}: {exc!s}")

    print(json.dumps({
        "status": "ok",
        "local_path": str(out.resolve()),
        "size_bytes": len(body),
    }))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        _die(2, "Usage: fetch_recording.py <call_id> <output_path>")
    call_id = argv[1].strip()
    output_path = argv[2].strip()
    if not call_id:
        _die(2, "call_id must be non-empty.")
    if not output_path:
        _die(2, "output_path must be non-empty.")
    return fetch_recording(call_id, output_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
