"""Limit repeated password checks for one nickname across sessions in this worker."""
from collections import OrderedDict
from threading import Lock
from time import monotonic

MAX_ATTEMPTS = 10
WINDOW_SECONDS = 60
_MAX_NICKNAMES = 4096
_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_lock = Lock()


def reserve_login_attempt(nickname: str) -> bool:
    """Reserve an attempt before checking the password, including concurrent submissions."""
    with _lock:
        now = monotonic()
        deadline, count = _attempts.get(nickname, (now, 0))
        if now >= deadline:
            deadline, count = now + WINDOW_SECONDS, 0
        if count >= MAX_ATTEMPTS:
            return False
        _attempts[nickname] = (deadline, count + 1)
        _attempts.move_to_end(nickname)
        if len(_attempts) > _MAX_NICKNAMES:
            _attempts.popitem(last=False)
        return True


def clear_login_attempts(nickname: str) -> None:
    with _lock:
        _attempts.pop(nickname, None)
