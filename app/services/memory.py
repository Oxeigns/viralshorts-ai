"""Memory guard for small containers.

Rendering a 1080x1920 Short with ffmpeg alongside the API process can exceed the
RAM of a small managed instance. When the kernel OOM-kills the process the reverse
proxy answers 502, so generation is skipped (with a notice) instead of crashing the
web service.
"""

from __future__ import annotations

import os

DEFAULT_BUDGET_MB = 420


def rss_mb() -> float:
    """Resident set size of this process in MiB. Returns 0.0 when unavailable."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        return 0.0


def budget_mb() -> float:
    raw = (os.environ.get("RENDER_MEMORY_BUDGET_MB") or "").strip()
    try:
        return float(raw) if raw else float(DEFAULT_BUDGET_MB)
    except ValueError:
        return float(DEFAULT_BUDGET_MB)


def over_budget(extra_mb: float = 0.0) -> bool:
    current = rss_mb()
    if current <= 0:
        return False
    return current + extra_mb > budget_mb()
