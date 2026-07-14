from __future__ import annotations

import os
import sys
from typing import Mapping, Optional, TextIO


ANSI = {
    "heading": "\x1b[1;36m",
    "success": "\x1b[32m",
    "warning": "\x1b[33m",
    "error": "\x1b[1;31m",
    "accent": "\x1b[36m",
    "id": "\x1b[35m",
    "muted": "\x1b[2m",
}
RESET = "\x1b[0m"


def color_enabled(
    stream: Optional[TextIO] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> bool:
    output = stream or sys.stdout
    env = os.environ if environment is None else environment
    try:
        terminal = bool(output.isatty())
    except (AttributeError, OSError):
        terminal = False
    return terminal and "NO_COLOR" not in env and env.get("TERM", "") != "dumb"


def style(text: str, role: str, *, enabled: bool) -> str:
    code = ANSI.get(role)
    if not enabled or code is None:
        return text
    return f"{code}{text}{RESET}"


def colorize_help(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    section_names = {
        "START HERE",
        "BOOK",
        "VIEW",
        "MANAGE",
        "JOBS AND USAGE",
        "AUTOMATION",
        "ADMINISTRATION (current administrator only)",
        "TIME AND POLICY",
    }
    lines = []
    for line in text.splitlines():
        if line in section_names:
            line = style(line, "heading", enabled=True)
        elif line.startswith("GPUBK -"):
            line = style(line, "accent", enabled=True)
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
