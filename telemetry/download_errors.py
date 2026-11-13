"""Actionable YouTube errors without exposing downloader output in the UI."""
from __future__ import annotations

from pathlib import Path
import re


LOG_TAIL_BYTES = 32 * 1024
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_LINE = re.compile(r"^\s*ERROR:\s*(.*)", re.IGNORECASE)


class YouTubeDownloadError(ValueError):
    """A fixed diagnostic message whose local log path should remain complete."""


_MESSAGES = {
    "verification": (
        "YouTube requested sign-in or bot verification for this connection. "
        "The app downloads anonymously and does not use your browser's signed-in session. "
        "Try again later or import a local copy."
    ),
    "account": (
        "YouTube reports that this recording requires account access, such as a private, "
        "age-restricted, or members-only video. Choose a public recording or import a local copy. "
        "Signing in to a browser does not sign in the app."
    ),
    "unavailable": (
        "YouTube reports that this recording is unavailable, removed, or restricted in your region. "
        "Check the video in your browser, choose another reference, or import a local copy."
    ),
    "rate_limit": (
        "YouTube is rate-limiting downloads (HTTP 429). Wait before retrying, "
        "or import a local copy."
    ),
    "forbidden": (
        "YouTube refused the download request (HTTP 403). Retry with the latest app version; "
        "if the request is still refused, choose another reference or import a local copy."
    ),
    "tls": (
        "The secure connection to YouTube could not be verified. Check your computer's clock "
        "and network or certificate settings, then retry with the latest app version."
    ),
    "network": (
        "The connection to YouTube failed or timed out. Check your Internet connection "
        "and proxy settings, then retry or import a local copy."
    ),
    "runtime": (
        "A required YouTube download component or JavaScript challenge solver failed. "
        "Update to the latest app version and retry. If you run from source, check the "
        "yt-dlp, yt-dlp-ejs, and JavaScript runtime setup. A local copy can also be imported."
    ),
    "formats": (
        "YouTube did not return a supported video or audio format. Retry with the latest "
        "app version, choose another reference, or import a local copy."
    ),
    "storage": (
        "The downloaded media could not be saved. Check free disk space and write access "
        "to the app's data folder, then retry."
    ),
    "unknown": (
        "The downloader could not finish. Retry with the latest app version, "
        "try another public video, or import a local copy."
    ),
}


def _read_tail(path: Path) -> str:
    """Keep diagnostics bounded even after a long-running download."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - LOG_TAIL_BYTES))
            return stream.read(LOG_TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _category(text: str) -> str:
    def has(*phrases):
        return any(phrase in text for phrase in phrases)

    if has("confirm you're not a bot", "confirm you are not a bot", "verify you are not a bot",
           "bot verification", "bot detection", "automated queries", "unusual traffic", "captcha"):
        return "verification"
    if has("private video", "video is private", "age-restricted", "age restricted", "confirm your age",
           "members-only", "members only", "join this channel", "available to subscribers",
           "login required", "authentication required", "sign in", "sign-in", "log in"):
        return "account"
    if has("no space left", "disk full", "errno 28", "errno 13", "permission denied",
           "read-only file system", "winerror 5", "access is denied", "unable to open for writing",
           "file name too long"):
        return "storage"
    if has("http error 429", "http status 429", "too many requests"):
        return "rate_limit"
    if has("http error 403", "http status 403", "403 forbidden"):
        return "forbidden"
    if has("certificate_verify_failed", "certificate verify failed", "certificate verification",
           "sslerror", "ssl error", "tls handshake", "unable to get local issuer certificate"):
        return "tls"
    if has("timed out", "timeout", "connection refused", "connection reset", "network is unreachable",
           "temporary failure in name resolution", "name or service not known", "getaddrinfo failed",
           "nodename nor servname", "unable to resolve", "could not resolve", "remote end closed"):
        return "network"
    if has("javascript runtime", "js runtime", "challenge solver", "challenge solving failed",
           "signature extraction failed", "signature solving failed", "nsig extraction failed", "error running node",
           "no module named 'yt_dlp",
           "no module named yt_dlp", "yt_dlp_ejs", "yt-dlp-ejs"):
        return "runtime"
    if has("requested format is not available", "requested format not available", "no video formats",
           "only images are available", "no downloadable formats", "no formats found"):
        return "formats"
    if has("video unavailable", "video is unavailable", "video has been removed", "video has been deleted",
           "video is not available", "not available in your country", "blocked in your country",
           "not made this video available", "geo restricted", "geographic restriction"):
        return "unavailable"
    return "unknown"


def _failure(log_path: Path) -> tuple[str, str]:
    text = _ANSI_ESCAPE.sub("", _read_tail(Path(log_path))).lower().replace("\u2019", "'")
    errors = [match.group(1) for line in text.splitlines() if (match := _ERROR_LINE.match(line))]
    category = _category(errors[-1] if errors else text)
    if category == "formats":
        warnings = (line for line in text.splitlines() if "warning:" in line)
        if any(_category(warning) == "runtime" for warning in warnings):
            category = "runtime"
    return category, errors[-1] if errors else ""


def youtube_download_failure_kind(log_path: Path) -> str:
    """Return a fixed failure category, never arbitrary downloader text."""
    return _failure(log_path)[0]


def youtube_media_forbidden(log_path: Path) -> bool:
    """Identify a fatal media HTTP 403, excluding page/auth/verification failures."""
    category, fatal = _failure(log_path)
    return category == "forbidden" and "unable to download video data" in fatal


def youtube_download_error(log_path: Path) -> str:
    """Summarize a failed anonymous download; never return raw log content.

    The last fatal error takes precedence over incidental extractor warnings.
    Missing-format errors are the exception: an earlier solver warning can
    explain why no usable formats remained.
    """
    log_path = Path(log_path)
    category = youtube_download_failure_kind(log_path)
    return f"YouTube download failed. {_MESSAGES[category]} Details: {log_path.resolve()}"
