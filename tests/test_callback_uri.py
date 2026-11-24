"""Regression tests for YouTube redirect URI construction.

A full callback URL pasted into APP_BASE_URL used to produce a doubled path,
which Google rejects as redirect_uri_mismatch.
"""

from app.services.youtube import CALLBACK_PATH, callback_uri, normalize_base_url

HOST = "https://viralshorts-ai-7bai.onrender.com"
EXPECTED = f"{HOST}{CALLBACK_PATH}"


def test_bare_origin_is_unchanged():
    assert normalize_base_url(HOST) == HOST
    assert callback_uri(HOST) == EXPECTED


def test_trailing_slash_is_stripped():
    assert normalize_base_url(f"{HOST}/") == HOST
    assert callback_uri(f"{HOST}/") == EXPECTED


def test_full_callback_url_does_not_double():
    pasted = f"{HOST}{CALLBACK_PATH}"
    assert normalize_base_url(pasted) == HOST
    assert callback_uri(pasted) == EXPECTED


def test_repeated_callback_suffixes_do_not_double():
    pasted = f"{HOST}{CALLBACK_PATH}{CALLBACK_PATH}{CALLBACK_PATH}"
    assert normalize_base_url(pasted) == HOST
    assert callback_uri(pasted) == EXPECTED


def test_missing_scheme_gets_https():
    assert normalize_base_url("viralshorts-ai-7bai.onrender.com") == HOST


def test_subpath_is_preserved_once():
    assert normalize_base_url(f"{HOST}/app") == f"{HOST}/app"
    assert callback_uri(f"{HOST}/app") == f"{HOST}/app{CALLBACK_PATH}"


def test_empty_and_garbage_fall_back_to_default():
    assert normalize_base_url("") == ""
    assert normalize_base_url("   ") == ""
    assert callback_uri("") == f"http://127.0.0.1:8000{CALLBACK_PATH}"
