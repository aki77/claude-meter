import json

import pytest

from claude_meter import auth
from claude_meter.auth import EXPIRY_MARGIN_SECONDS, Credentials, _parse_credentials, get_credentials

REAL_BLOB = {
    "mcpOAuthClientConfig": {"https://example.com": {"clientId": "x"}},
    "mcpOAuth": {"server:acct": {"accessToken": "mcp-token-should-be-ignored"}},
    "claudeAiOauth": {
        "accessToken": "sk-ant-oat01-real",
        "refreshToken": "sk-ant-ort01-real",
        "expiresAt": 1787235329073,
        "scopes": ["user:inference"],
        "subscriptionType": "team",
    },
    "organizationUuid": "uuid",
}


def test_reads_claude_ai_oauth_token():
    creds = _parse_credentials(json.dumps(REAL_BLOB))
    assert creds is not None
    assert creds.access_token == "sk-ant-oat01-real"
    assert creds.expires_at == 1787235329073 / 1000.0


def test_ignores_mcp_oauth_token():
    blob = {
        "mcpOAuth": {"server:acct": {"accessToken": "mcp-token-should-be-ignored"}},
        "organizationUuid": "uuid",
    }
    assert _parse_credentials(json.dumps(blob)) is None


def test_empty_access_token_is_absent():
    blob = {"claudeAiOauth": {"accessToken": "", "expiresAt": 0}}
    assert _parse_credentials(json.dumps(blob)) is None


def test_whitespace_access_token_is_absent():
    blob = {"claudeAiOauth": {"accessToken": "   "}}
    assert _parse_credentials(json.dumps(blob)) is None


@pytest.mark.parametrize("blob", ["", "   ", "not json", "[]", '"bare-string"'])
def test_unparseable_blob(blob):
    assert _parse_credentials(blob) is None


def test_missing_expires_at_is_unknown():
    creds = _parse_credentials(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    assert creds is not None
    assert creds.expires_at is None
    assert creds.is_expired(now=1_000_000_000) is False


def test_expires_at_zero_not_expired():
    creds = _parse_credentials(json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": 0}}))
    assert creds is not None
    assert creds.expires_at is None
    assert creds.is_expired(now=1_000_000_000) is False


NOW = 1_000_000.0


@pytest.mark.parametrize(
    "expires_at,expected",
    [
        (NOW + EXPIRY_MARGIN_SECONDS + 1, False),
        (NOW + EXPIRY_MARGIN_SECONDS, True),
        (NOW + EXPIRY_MARGIN_SECONDS - 1, True),
        (NOW, True),
        (NOW - 3600, True),
    ],
)
def test_expiry_margin_boundary(expires_at, expected):
    assert Credentials(access_token="tok", expires_at=expires_at).is_expired(now=NOW) is expected


def test_get_credentials_uses_file_off_darwin(monkeypatch, tmp_path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps(REAL_BLOB))
    monkeypatch.setattr(auth.sys, "platform", "linux")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", path)
    creds = get_credentials()
    assert creds is not None
    assert creds.access_token == "sk-ant-oat01-real"


def test_get_credentials_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.sys, "platform", "linux")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", tmp_path / "nope.json")
    assert get_credentials() is None


def test_get_credentials_keychain(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setattr(auth, "_read_keychain", lambda: Credentials(access_token="kc"))
    creds = get_credentials()
    assert creds is not None
    assert creds.access_token == "kc"


def test_read_keychain_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(auth.subprocess, "run", boom)
    assert auth._read_keychain() is None
