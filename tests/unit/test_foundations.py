from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from infer_platform.common.schemas import RequestMessage
from infer_platform.common.settings import Settings
from infer_platform.gateway.auth import Authenticator


def test_auth_hashes_and_checks_keys():
    auth = Authenticator([SecretStr("first"), SecretStr("second")])
    assert auth.authenticate("second")
    assert auth.authenticate("first") != "first"
    assert auth.authenticate("bad") is None
    assert auth.authenticate(None) is None
    with pytest.raises(ValueError):
        Authenticator([SecretStr("")])


def test_message_serialization():
    message = RequestMessage(
        request_id="id",
        reply_to="inbox:gw",
        payload='{"input":"a"}',
        timeout_ms=10,
        key_id="hash",
        trace_id="trace",
    )
    assert RequestMessage.model_validate(message.fields()) == message
    assert all(isinstance(value, str) for value in message.fields().values())
    with pytest.raises(ValidationError):
        message.model_copy(update={"timeout_ms": 0}).model_validate(
            {**message.fields(), "timeout_ms": "0"}
        )


def test_settings_documented():
    docs = Path("docs/CONFIG.md").read_text()
    for name in Settings.model_fields:
        assert f"INFER_{name.upper()}" in docs
