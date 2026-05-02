from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REEXEC_FLAG = "STORAGEMAN_REEXEC"


def _venv_python() -> Path | None:
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def _maybe_reexec_in_venv() -> None:
    venv_python = _venv_python()
    if not venv_python:
        return

    current = Path(sys.executable).resolve()
    target = venv_python.resolve()

    if current == target:
        return

    if os.environ.get(REEXEC_FLAG) == "1":
        return

    env = os.environ.copy()
    env[REEXEC_FLAG] = "1"
    cmd = [str(target), str(__file__), *sys.argv[1:]]
    completed = subprocess.run(cmd, env=env, cwd=str(ROOT), check=False)
    raise SystemExit(completed.returncode)


def _install_requirements() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=str(ROOT),
    )


def _run_app() -> int:
    completed = subprocess.run([sys.executable, "-m", "app.main"], cwd=str(ROOT), check=False)
    return completed.returncode


def main() -> int:
    _maybe_reexec_in_venv()
    _install_requirements()
    return _run_app()


if __name__ == "__main__":
    raise SystemExit(main())
