"""Shared test isolation.

Two problems made the suite flaky:

* ``enqueue_generation`` starts a real render in the background. Inside a test that
  render keeps a SQLite connection and races the next test, which then fails while
  creating tables.
* Tests that only assert the *queueing* contract should not pay for ffmpeg.

The autouse fixture replaces the background runner with a no-op, leaving the job
row behind so status endpoints remain testable. Tests that need a real render call
``run_generation`` directly.
"""

from __future__ import annotations

import pytest

from app.services import jobs


@pytest.fixture(autouse=True)
def _no_background_render(monkeypatch):
    async def _noop(job_id: int, language: str | None) -> None:
        return None

    monkeypatch.setattr(jobs, "_run", _noop)
    yield
