"""Поиск установленных Java: типичные папки, JAVA_HOME, PATH, Java из самого Freesm."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

IS_WIN = sys.platform.startswith("win")
_NO_WINDOW = 0x08000000 if IS_WIN else 0
_VENDOR_DIRS = ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft",
                "Semeru", "AdoptOpenJDK", "Temurin", "OpenJDK", "Liberica")


@dataclass(frozen=True)
class JavaInfo:
    path: str      # путь к javaw.exe (на Windows) или java
    major: int
    version: str

    def label(self) -> str:
        return f"Java {self.major} ({self.version}) — {self.path}"


def parse_major(version_output: str) -> Optional[tuple]:
    """Из вывода `java -version` достаёт (major, строка версии)."""
    m = re.search(r'version "([^"]+)"', version_output)
    if not m:
        return None
    ver = m.group(1)
    nums = re.findall(r"\d+", ver)
    if not nums:
        return None
    major = int(nums[1]) if nums[0] == "1" and len(nums) > 1 else int(nums[0])
    return major, ver


def _query(java_exe: Path) -> Optional[JavaInfo]:
    probe = java_exe
    if IS_WIN and java_exe.name.lower() == "javaw.exe":
        sib = java_exe.with_name("java.exe")
        if sib.exists():
            probe = sib
    try:
        r = subprocess.run([str(probe), "-version"], capture_output=True, text=True,
                           timeout=10, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    parsed = parse_major((r.stderr or "") + (r.stdout or ""))
    if not parsed:
        return None
    return JavaInfo(str(java_exe), parsed[0], parsed[1])


def _candidate_roots(extra: Iterable[Path] = ()) -> List[Path]:
    roots: List[Path] = []
    env = os.environ
    if IS_WIN:
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramW6432"):
            base = env.get(var)
            if base:
                for v in _VENDOR_DIRS:
                    roots.append(Path(base) / v)
        home = Path(env.get("USERPROFILE", str(Path.home())))
        roots += [home / ".jdks", home / ".gradle" / "jdks",
                  Path(env.get("APPDATA", "")) / ".minecraft" / "runtime"]
    else:
        roots += [Path("/usr/lib/jvm"), Path.home() / ".sdkman/candidates/java"]
    roots += [Path(p) for p in extra]
    return roots


def _exes_under(root: Path) -> Iterable[Path]:
    name = "javaw.exe" if IS_WIN else "java"
    if not root.is_dir():
        return
    # root сам может быть JDK, либо содержать папки JDK, либо вложенность до 3 уровней (runtime Mojang)
    for depth, pattern in enumerate(("bin/" + name, "*/bin/" + name, "*/*/bin/" + name, "*/*/*/bin/" + name)):
        for p in root.glob(pattern):
            if p.is_file():
                yield p


def find_javas(extra_roots: Iterable[Path] = ()) -> List[JavaInfo]:
    seen, found = set(), []
    candidates: List[Path] = []
    jh = os.environ.get("JAVA_HOME")
    if jh:
        candidates += list(_exes_under(Path(jh)))
    for root in _candidate_roots(extra_roots):
        candidates += list(_exes_under(root))
    for exe_name in (("javaw", "java") if IS_WIN else ("java",)):
        w = shutil.which(exe_name)
        if w:
            candidates.append(Path(w))
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        info = _query(p)
        if info:
            found.append(info)
    found.sort(key=lambda j: (-j.major, j.path))
    return found


def best_for(javas: List[JavaInfo], required_major: int) -> Optional[JavaInfo]:
    exact = [j for j in javas if j.major == required_major]
    if exact:
        return exact[0]
    newer = sorted((j for j in javas if j.major > required_major), key=lambda j: j.major)
    return newer[0] if newer else None
