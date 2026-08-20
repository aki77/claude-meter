import getpass
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
EXPIRY_MARGIN_SECONDS = 5 * 60


@dataclass
class Credentials:
    access_token: str
    expires_at: float | None = None

    def is_expired(self, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        now = time.time() if now is None else now
        return self.expires_at - EXPIRY_MARGIN_SECONDS <= now


def _parse_credentials(blob: str) -> Credentials | None:
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        return None
    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        expires = expires_at / 1000.0
    else:
        expires = None
    return Credentials(access_token=token, expires_at=expires)


def _read_keychain() -> Credentials | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", getpass.getuser(), "-w"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return _parse_credentials(result.stdout)


def _read_file() -> Credentials | None:
    try:
        return _parse_credentials(CREDENTIALS_PATH.read_text())
    except OSError:
        return None


def get_credentials() -> Credentials | None:
    if sys.platform == "darwin":
        return _read_keychain()
    return _read_file()
