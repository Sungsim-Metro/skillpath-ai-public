"""Password verifiers and server-side sessions for saved learning profiles."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

_ITERATIONS = 600_000
_METHOD = "pbkdf2_sha256"
_SESSION_KEY = secrets.token_bytes(32)
_INVALID_CREDENTIALS = "닉네임 또는 비밀번호를 확인해 주세요."


class ProfileAuthError(ValueError):
    """An authentication or registration failure suitable for display."""


@dataclass(frozen=True, slots=True)
class ProfileSession:
    """Server-side proof of sign-in, bound to a nickname and its current verifier."""

    nickname: str
    _proof: bytes = field(repr=False)


def nickname_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip().encode()) > 2048:
        raise ProfileAuthError("닉네임을 입력해 주세요. 최대 길이는 UTF-8 기준 2048바이트입니다.")
    return value.strip()


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str) or not password:
        raise ProfileAuthError("비밀번호를 입력해 주세요.")
    return password.encode("utf-8")


def new_credential(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", _password_bytes(password), salt, _ITERATIONS)
    return f"{_METHOD}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def login_password_bytes(password: str) -> bytes:
    """Reject invalid login input before accessing storage."""
    try:
        return _password_bytes(password)
    except ProfileAuthError:
        raise ProfileAuthError(_INVALID_CREDENTIALS) from None


def verify_password(encoded: bytes, credential: str | None) -> None:
    """Perform the same derivation for unknown, legacy and registered accounts."""
    salt, expected = bytes(16), bytes(32)
    valid = False
    if isinstance(credential, str):
        try:
            method, rounds, raw_salt, raw_digest = credential.split("$")
            decoded_salt = bytes.fromhex(raw_salt)
            decoded_digest = bytes.fromhex(raw_digest)
            if (method == _METHOD and rounds == str(_ITERATIONS)
                    and len(decoded_salt) == 16 and len(decoded_digest) == 32):
                salt, expected, valid = decoded_salt, decoded_digest, True
        except ValueError:
            pass
    actual = hashlib.pbkdf2_hmac("sha256", encoded, salt, _ITERATIONS)
    if not hmac.compare_digest(actual, expected) or not valid:
        raise ProfileAuthError(_INVALID_CREDENTIALS)


def _proof(nickname: str, credential: str) -> bytes:
    return hmac.digest(_SESSION_KEY, nickname.encode() + b"\x00" + credential.encode("ascii"), "sha256")


def issue_session(nickname: str, credential: str) -> ProfileSession:
    return ProfileSession(nickname, _proof(nickname, credential))


def require_session(session: ProfileSession) -> None:
    if not isinstance(session, ProfileSession) or not isinstance(session._proof, bytes):
        raise ProfileAuthError("로그인한 뒤 프로필을 이용해 주세요.")


def check_credential(session: ProfileSession, credential: str | None) -> None:
    if not isinstance(credential, str) or not hmac.compare_digest(
        session._proof, _proof(session.nickname, credential)
    ):
        raise ProfileAuthError("다시 로그인해 주세요.")
