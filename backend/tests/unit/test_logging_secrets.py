from __future__ import annotations

import pytest

from app.core.logging import configure_logging, get_logger

_SECRET = "hunter2-session-token-value"


def _fail_with_secret_in_a_local() -> None:
    password = _SECRET  # noqa: F841 - the point is that it sits in a frame local
    raise ValueError("login failed")


@pytest.mark.parametrize("json_output", [True, False])
def test_logged_exceptions_never_include_frame_locals(
    capsys: pytest.CaptureFixture[str], json_output: bool
) -> None:
    configure_logging("INFO", json_output=json_output)
    try:
        _fail_with_secret_in_a_local()
    except ValueError as exc:
        get_logger("test").error("operation_failed", exc_info=exc)
    out = capsys.readouterr().out
    assert "login failed" in out
    assert "_fail_with_secret_in_a_local" in out
    assert _SECRET not in out
