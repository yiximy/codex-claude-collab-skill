"""Classify codex CLI output: ok / quota_exhausted / banned / auth_invalid / unknown."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_QUOTA_ERROR_RE = re.compile(
    r"\b(429|usage[_\s-]?limit|rate[_\s-]?limit|quota[_\s-]?exceeded|"
    r"usage[_\s-]?exceeded|too many requests|"
    r"you('?ve| have) (reached your usage|hit your usage))\b",
    re.IGNORECASE,
)
_BAN_ERROR_RE = re.compile(
    r"\b(402|deactivated[_\s-]?workspace|workspace[_\s-]?deactivated|"
    r"account[_\s-]?disabled|workspace[_\s-]?disabled|account[_\s-]?banned|"
    r"account[_\s-]?terminated|payment[_\s-]?required)\b",
    re.IGNORECASE,
)
_AUTH_INVALID_RE = re.compile(
    r"(refresh[_\s-]?token[_\s-]?invalidated|refresh token (has been |was )?(invalidated|revoked)|"
    r"please (log ?out|sign) (and )?(sign|log) in again|"
    r"access token could not be refreshed|token_revoked|sign in again)",
    re.IGNORECASE,
)
_AUTH_FAIL_RE = re.compile(
    r"\b(401|403|unauthorized|missing bearer|authentication[_\s-]?failed|"
    r"invalid[_\s-]?api[_\s-]?key|login[_\s-]?required|unauthenticated)\b",
    re.IGNORECASE,
)
_QUOTA_RESET_RE = re.compile(
    r"try again in\s*"
    r"(?:(\d+)\s*(?:days?|d)\b)?\s*"
    r"(?:(\d+)\s*(?:hours?|hrs?|h)\b)?\s*"
    r"(?:(\d+)\s*(?:minutes?|mins?|m)\b)?",
    re.IGNORECASE,
)


def classify_codex_result(rc: int, stderr: str, stdout: str) -> str:
    """Order: banned > auth_invalid > quota_exhausted > soft auth_fail > unknown."""
    text = f"{stderr or ''}\n{stdout or ''}"
    if rc == 0 and not (
        _BAN_ERROR_RE.search(text)
        or _AUTH_INVALID_RE.search(text)
        or _QUOTA_ERROR_RE.search(text)
    ):
        return "ok"
    if _BAN_ERROR_RE.search(text):
        return "banned"
    if _AUTH_INVALID_RE.search(text):
        return "auth_invalid"
    if _QUOTA_ERROR_RE.search(text):
        return "quota_exhausted"
    if _AUTH_FAIL_RE.search(text):
        return "auth_invalid"
    return "unknown"


def parse_quota_reset(text: str) -> datetime | None:
    """Extract 'try again in X days Y hours Z minutes' into an absolute UTC datetime."""
    if not text:
        return None
    m = _QUOTA_RESET_RE.search(text)
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    if days == 0 and hours == 0 and minutes == 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days, hours=hours, minutes=minutes)
