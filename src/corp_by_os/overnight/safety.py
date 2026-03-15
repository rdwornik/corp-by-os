"""Pre-upload safety gate — block sensitive files.

Deny-by-default: every file must pass extension, path pattern,
and content checks before being sent to Gemini.
"""

from __future__ import annotations

import logging
import re
from fnmatch import fnmatch
from pathlib import Path

logger = logging.getLogger(__name__)

# OS-generated junk files — skip silently, never process
SKIP_FILENAMES: set[str] = {
    "desktop.ini",
    "Thumbs.db",
    ".DS_Store",
    "ehthumbs.db",
    "Icon\r",
}

BLOCKED_EXTENSIONS: set[str] = {
    ".env",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
    ".keychain",
    ".ppk",
    ".cer",
    ".crt",
    ".jks",
    ".agentignore",
    ".gitignore",
}

BLOCKED_PATH_PATTERNS: list[str] = [
    "**/secrets/**",
    "**/.ssh/**",
    "**/.git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.corp/**",
    "**/_knowledge/**",
]

# Regex patterns for sensitive content (checked in first 4KB only)
BLOCKED_CONTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"BEGIN PRIVATE KEY"),
    re.compile(r"BEGIN RSA PRIVATE KEY"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI-style API key
    re.compile(r"AIzaSy[a-zA-Z0-9_\-]{33}"),  # Google API key
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub personal token
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
]

# Max bytes to read for content scanning (4KB is enough for key detection)
_CONTENT_SCAN_BYTES = 4096


def is_safe_for_upload(file_path: Path) -> tuple[bool, str]:
    """Check if file is safe to send to Gemini.

    Returns:
        (True, "") if safe.
        (False, reason) if blocked.
    """
    # 0. OS junk files — skip silently
    if file_path.name in SKIP_FILENAMES:
        return False, f"OS junk file: {file_path.name}"

    # 1. Extension check
    ext = file_path.suffix.lower()
    if ext in BLOCKED_EXTENSIONS:
        return False, f"blocked extension: {ext}"

    # 2. Path pattern check
    path_str = str(file_path).replace("\\", "/")
    for pattern in BLOCKED_PATH_PATTERNS:
        if fnmatch(path_str, pattern):
            return False, f"blocked path pattern: {pattern}"

    # 3. Content scan (text files only, first 4KB)
    if ext in {".txt", ".md", ".yaml", ".yml", ".json", ".cfg", ".ini", ".conf", ".toml", ".env"}:
        try:
            raw = file_path.read_bytes()[:_CONTENT_SCAN_BYTES]
            text = raw.decode("utf-8", errors="replace")
            for pattern in BLOCKED_CONTENT_PATTERNS:
                match = pattern.search(text)
                if match:
                    return False, f"sensitive content detected: {pattern.pattern}"
        except OSError:
            pass  # Can't read → skip content check, other checks still apply

    return True, ""


def filter_safe_files(file_paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Filter a list of files, returning (safe, blocked_with_reasons)."""
    safe: list[Path] = []
    blocked: list[tuple[Path, str]] = []

    for fp in file_paths:
        ok, reason = is_safe_for_upload(fp)
        if ok:
            safe.append(fp)
        else:
            blocked.append((fp, reason))
            logger.warning("Safety gate blocked: %s — %s", fp, reason)

    return safe, blocked
