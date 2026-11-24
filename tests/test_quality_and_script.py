from app.services.ai_providers import local_script
from app.services.quality import evaluate


def test_local_script_has_hook_and_titles():
    draft = local_script("public domain space photos", "science", "en")
    assert draft.hook
    assert len(draft.titles) == 3
    assert "shorts" in " ".join(draft.hashtags).lower() or "#shorts" in draft.hashtags


def test_quality_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"no")
    result = evaluate(str(empty), 20, True, "short text", "hi")
    assert result["passed"] is False


def test_quality_accepts_reasonable_script(tmp_path):
    path = tmp_path / "ok.mp4"
    path.write_bytes(b"0" * 5000)
    script = "Stop scrolling. Public domain space photos are legal to reuse. NASA images are a documented source. Keep claims checkable."
    result = evaluate(str(path), 24, True, script, "Stop scrolling now")
    assert result["score"] >= 70
    assert result["passed"] is True
