"""Robust file downloader with resume, retry, and progress.

Replaces raw curl calls with a Python stdlib implementation
that handles the failure modes of large artifact downloads:
  - Resume interrupted downloads (HTTP Range headers)
  - Retry with exponential backoff on transient errors
  - Terminal progress bar (no external deps)
  - SHA256 checksum on completion
  - GitHub auth token forwarded when available
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Retry configuration
MAX_RETRIES = 5
INITIAL_BACKOFF_S = 2.0
BACKOFF_MULTIPLIER = 2.0
CHUNK_SIZE = 256 * 1024  # 256 KB

# HTTP status codes that are worth retrying
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def _gh_token() -> str | None:
    """Get GitHub auth token from gh CLI if available."""
    try:
        r = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _build_request(
    url: str,
    *,
    resume_from: int = 0,
    token: str | None = None,
) -> urllib.request.Request:
    """Build a urllib Request with optional Range and auth."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ltvm/1.0")
    if resume_from > 0:
        req.add_header("Range", f"bytes={resume_from}-")
    if token and "github" in url.lower():
        req.add_header("Authorization", f"token {token}")
    return req


def _format_size(nbytes: int) -> str:
    """Format byte count as human-readable string."""
    if nbytes < 1024:
        return f"{nbytes} B"
    for unit in ("KB", "MB", "GB"):
        nbytes /= 1024  # type: ignore[assignment]
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}"
    return f"{nbytes:.1f} GB"  # unreachable but satisfies mypy


def _progress_bar(
    downloaded: int,
    total: int | None,
    start_time: float,
    width: int = 40,
) -> str:
    """Render a progress bar string."""
    elapsed = time.monotonic() - start_time
    rate = downloaded / elapsed if elapsed > 0 else 0
    rate_str = f"{_format_size(int(rate))}/s"

    if total and total > 0:
        pct = min(downloaded / total, 1.0)
        filled = int(width * pct)
        bar = "#" * filled + "-" * (width - filled)
        return (
            f"\r  [{bar}] {pct:5.1%}  "
            f"{_format_size(downloaded)}/{_format_size(total)}  "
            f"{rate_str}"
        )
    else:
        return f"\r  {_format_size(downloaded)}  {rate_str}"


def download(
    url: str,
    dest: str | Path,
    *,
    expected_sha256: str | None = None,
    show_progress: bool = True,
) -> str:
    """Download a file with resume, retry, and progress.

    Args:
        url: URL to download.
        dest: Local path to write to.  If a partial file exists,
              resume from where it left off.
        expected_sha256: If provided, verify after download.
        show_progress: Show a terminal progress bar.

    Returns:
        SHA256 hex digest of the downloaded file.

    Raises:
        RuntimeError: On download failure after all retries,
                      or checksum mismatch.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    token = _gh_token()
    is_tty = show_progress and sys.stderr.isatty()

    for attempt in range(1, MAX_RETRIES + 1):
        # Check for partial file to resume
        resume_from = 0
        if dest.exists():
            resume_from = dest.stat().st_size

        req = _build_request(url, resume_from=resume_from, token=token)

        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code == 416:
                # Range not satisfiable — file is already complete
                # (or server doesn't know the size). Assume complete.
                break
            if e.code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_S * (
                    BACKOFF_MULTIPLIER ** (attempt - 1)
                )
                print(
                    f"  HTTP {e.code}, retrying in {backoff:.0f}s "
                    f"(attempt {attempt}/{MAX_RETRIES})...",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue
            raise RuntimeError(
                f"Download failed: HTTP {e.code} {e.reason}\n"
                f"  URL: {url}"
            ) from e
        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_S * (
                    BACKOFF_MULTIPLIER ** (attempt - 1)
                )
                print(
                    f"  Connection error: {e.reason}, retrying in "
                    f"{backoff:.0f}s "
                    f"(attempt {attempt}/{MAX_RETRIES})...",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue
            raise RuntimeError(
                f"Download failed: {e.reason}\n  URL: {url}"
            ) from e

        # Determine total size
        content_length = resp.headers.get("Content-Length")
        total: int | None = None
        if content_length:
            total = resume_from + int(content_length)

        if resume_from > 0 and resp.status == 206:
            if is_tty:
                print(
                    f"  Resuming from {_format_size(resume_from)}",
                    file=sys.stderr,
                )
            mode = "ab"
        else:
            # Server doesn't support Range, or fresh download
            if resume_from > 0:
                # Server ignored Range — start over
                resume_from = 0
            mode = "wb"
            if content_length:
                total = int(content_length)

        downloaded = resume_from
        start_time = time.monotonic()

        try:
            with open(dest, mode) as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if is_tty:
                        print(
                            _progress_bar(
                                downloaded, total, start_time
                            ),
                            end="",
                            file=sys.stderr,
                        )
        except (
            ConnectionError,
            TimeoutError,
            urllib.error.URLError,
        ) as e:
            if is_tty:
                print(file=sys.stderr)  # newline after progress
            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_S * (
                    BACKOFF_MULTIPLIER ** (attempt - 1)
                )
                print(
                    f"  Transfer interrupted at "
                    f"{_format_size(downloaded)}, "
                    f"retrying in {backoff:.0f}s "
                    f"(attempt {attempt}/{MAX_RETRIES})...",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue
            raise RuntimeError(
                f"Download interrupted after {MAX_RETRIES} attempts "
                f"at {_format_size(downloaded)}\n  URL: {url}"
            ) from e

        if is_tty:
            print(file=sys.stderr)  # newline after progress bar

        # Download completed successfully
        break

    # Compute SHA256
    sha256 = sha256_file(dest)

    if expected_sha256 and sha256 != expected_sha256:
        dest.unlink()
        raise RuntimeError(
            f"Checksum mismatch for {dest.name}\n"
            f"  expected: {expected_sha256}\n"
            f"  got:      {sha256}"
        )

    return sha256


def sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def github_api_json(
    endpoint: str,
    repo: str | None = None,
) -> dict:
    """Fetch a GitHub API endpoint and return parsed JSON.

    endpoint: full URL, or a path like /releases/latest
              (requires repo).
    repo: GitHub repo in owner/name format.  If endpoint
          is a relative path, this is prepended.
    """
    if endpoint.startswith("http"):
        url = endpoint
    elif repo:
        url = f"https://api.github.com/repos/{repo}/{endpoint.lstrip('/')}"
    else:
        raise ValueError(
            "Relative endpoint requires repo parameter"
        )

    token = _gh_token()
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ltvm/1.0")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"token {token}")

    try:
        resp = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"GitHub API request failed: HTTP {e.code} {e.reason}"
            f"\n  URL: {url}"
        ) from e

    return json.loads(resp.read())
