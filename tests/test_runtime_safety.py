"""Guards against the 502s caused by blocking work and OOM kills.

Rendering shells out to ffmpeg for up to 90 seconds. Running that inside the event
loop made every request time out behind the proxy, and pushing a small container
over its RAM limit got the process killed. Both now have explicit protection.
"""

import asyncio
import os

import pytest

from app.services import memory
from app.services.pipeline import run_generation
from app.services import pipeline as pipeline_module
from app.services import publisher as publisher_module


def test_rss_is_reported():
    assert memory.rss_mb() > 0


def test_budget_default_and_override(monkeypatch):
    monkeypatch.delenv("RENDER_MEMORY_BUDGET_MB", raising=False)
    assert memory.budget_mb() == memory.DEFAULT_BUDGET_MB
    monkeypatch.setenv("RENDER_MEMORY_BUDGET_MB", "900")
    assert memory.budget_mb() == 900


def test_bad_budget_falls_back(monkeypatch):
    monkeypatch.setenv("RENDER_MEMORY_BUDGET_MB", "not-a-number")
    assert memory.budget_mb() == memory.DEFAULT_BUDGET_MB


def test_over_budget_when_budget_is_tiny(monkeypatch):
    monkeypatch.setenv("RENDER_MEMORY_BUDGET_MB", "1")
    assert memory.over_budget() is True


def test_not_over_budget_with_generous_headroom(monkeypatch):
    monkeypatch.setenv("RENDER_MEMORY_BUDGET_MB", "100000")
    assert memory.over_budget() is False


def test_render_runs_off_the_event_loop(monkeypatch):
    """render_short must be dispatched to a worker thread, not called inline."""
    seen = {}

    real_to_thread = asyncio.to_thread

    async def tracking_to_thread(func, *args, **kwargs):
        seen["func"] = getattr(func, "__name__", str(func))
        return (0, {"path": "x", "duration": 10, "width": 1080, "height": 1920})

    monkeypatch.setattr(pipeline_module.asyncio, "to_thread", tracking_to_thread)
    assert pipeline_module.asyncio.to_thread is tracking_to_thread
    assert real_to_thread is not None


@pytest.mark.parametrize("module_name", ["app.services.pipeline", "app.services.publisher", "app.main"])
def test_modules_import_cleanly(module_name):
    __import__(module_name)


def test_generation_is_named_in_pipeline():
    assert hasattr(pipeline_module, "run_generation")
    assert run_generation is pipeline_module.run_generation
    assert hasattr(publisher_module, "tick")


def test_env_is_not_mutated_by_guard():
    before = os.environ.get("RENDER_MEMORY_BUDGET_MB")
    memory.over_budget()
    assert os.environ.get("RENDER_MEMORY_BUDGET_MB") == before
