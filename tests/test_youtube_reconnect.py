"""Expired Google refresh tokens must surface as an explicit reconnect request.

An OAuth app left in Testing status issues refresh tokens that expire after seven
days, so this path runs in normal operation, not only on revocation.
"""

from app.services.youtube import ReconnectRequired, is_reconnect_error

INVALID_GRANT = '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
OTHER_400 = '{"error": "invalid_request", "error_description": "Missing parameter: code"}'


def test_invalid_grant_is_a_reconnect():
    assert is_reconnect_error(400, INVALID_GRANT) is True


def test_invalid_grant_on_401_is_a_reconnect():
    assert is_reconnect_error(401, INVALID_GRANT) is True


def test_other_400_is_not_a_reconnect():
    assert is_reconnect_error(400, OTHER_400) is False


def test_server_error_is_not_a_reconnect():
    assert is_reconnect_error(500, INVALID_GRANT) is False


def test_empty_body_is_not_a_reconnect():
    assert is_reconnect_error(400, "") is False


def test_reconnect_required_is_a_runtime_error():
    assert issubclass(ReconnectRequired, RuntimeError)
