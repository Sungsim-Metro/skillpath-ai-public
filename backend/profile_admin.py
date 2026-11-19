"""Enroll a verified owner of an existing passwordless profile from an operator terminal."""
import argparse
import sys
import warnings
from getpass import GetPassWarning, getpass
from pathlib import Path

from dotenv import load_dotenv

from backend.api import is_mock
from backend.chat_profiles import ProfileAuthError, set_legacy_password


def enroll() -> None:
    if not sys.stdin.isatty():
        raise SystemExit("비밀번호를 숨길 수 있는 대화형 터미널에서 실행하세요.")
    nickname = input("소유자를 확인한 기존 닉네임: ").strip()
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            password = getpass("새 비밀번호: ")
            confirmation = getpass("새 비밀번호 확인: ")
        except GetPassWarning:
            raise SystemExit("비밀번호 입력을 숨길 수 없어 중단했습니다.") from None
    if password != confirmation:
        raise SystemExit("입력한 비밀번호가 다릅니다.")
    try:
        set_legacy_password(nickname, password)
    except ProfileAuthError as exc:
        raise SystemExit(str(exc)) from None
    print("기존 프로필에 비밀번호를 설정했습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["enroll-legacy"])
    parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    print("대상 저장소:", "로컬 SQLite" if is_mock() else "DynamoDB")
    enroll()


if __name__ == "__main__":
    main()
