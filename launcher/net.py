"""Сеть: загрузка JSON и файлов с проверкой SHA-256 (только стандартная библиотека)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from . import __version__

UA = f"ImperialLauncher/{__version__}"
RETRY_DELAY = 1.0  # секунд; в тестах ставим 0


class Cancelled(Exception):
    """Пользователь отменил операцию."""


class DownloadError(Exception):
    """Не удалось скачать или проверить файл."""


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)


def _fatal_http(err: Exception) -> bool:
    """Ошибки 4xx (кроме 408/429) повторять бессмысленно."""
    return (
        isinstance(err, urllib.error.HTTPError)
        and 400 <= err.code < 500
        and err.code not in (408, 429)
    )


def get_bytes(url: str, timeout: float = 25, retries: int = 3) -> bytes:
    last: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with _open(url, timeout) as r:
                return r.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            if _fatal_http(e) or attempt == retries:
                break
            time.sleep(RETRY_DELAY * attempt)
    raise DownloadError(f"{url}: {last}")


def get_json(url: str, timeout: float = 25):
    return json.loads(get_bytes(url, timeout).decode("utf-8"))


def _silent_remove(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def download_file(
    url: str,
    dest: Path,
    sha256: Optional[str] = None,
    size: Optional[int] = None,
    on_bytes: Optional[Callable[[int], None]] = None,
    cancel: Optional[Event] = None,
    timeout: float = 30,
    retries: int = 3,
) -> str:
    """Скачивает во временный файл, проверяет размер и хэш, затем атомарно подменяет dest."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".imperial-part")
    last: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        h = hashlib.sha256()
        got = 0
        try:
            with _open(url, timeout) as r, open(part, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))
            if size is not None and got != size:
                raise DownloadError(f"размер {got} вместо {size}")
            if sha256 and h.hexdigest().lower() != sha256.lower():
                raise DownloadError("контрольная сумма не совпала")
            os.replace(part, dest)
            return h.hexdigest()
        except Cancelled:
            _silent_remove(part)
            raise
        except (urllib.error.URLError, OSError, DownloadError, TimeoutError) as e:
            last = e
            _silent_remove(part)
            if on_bytes and got:
                on_bytes(-got)  # откатываем счётчик прогресса неудачной попытки
            if _fatal_http(e) or attempt == retries:
                break
            time.sleep(RETRY_DELAY * attempt)
    raise DownloadError(f"{url}: {last}")
