#!/usr/bin/env python3
"""Run the full Booktunes backend test suite from one command.

Examples:
    python scripts/run_backend_checks.py
    python scripts/run_backend_checks.py --pytest-args "-k auth"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_python() -> str:
    """Prefer the project virtualenv interpreter when it exists."""
    candidates: list[Path] = []

    if os.environ.get("VIRTUAL_ENV"):
        venv_dir = Path(os.environ["VIRTUAL_ENV"])
        candidates.append(venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    venv_python = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_python.exists():
        candidates.append(venv_python)

    candidates.append(Path(sys.executable))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return str(sys.executable)


def prepare_environment() -> None:
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("SECRET_KEY", "test-secret-key")
    os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/booktunes_test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def run_tests(py_executable: str, extra_args: list[str]) -> int:
    cmd = [py_executable, "-m", "pytest", "-q", "tests", *extra_args]
    print(f"Running backend checks with: {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=os.environ.copy())
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Booktunes backend test suite")
    parser.add_argument(
        "--pytest-args",
        nargs="*",
        default=[],
        help="Additional arguments passed to pytest",
    )
    args = parser.parse_args()

    prepare_environment()
    py_executable = find_python()

    print("Booktunes backend verification")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python interpreter: {py_executable}")

    return run_tests(py_executable, args.pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
