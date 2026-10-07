"""Cross-platform task runner (Windows/macOS/Linux). `make <task>` delegates here.

Usage: python scripts/tasks.py <task> [extra args passed to the underlying command]
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
BIN = VENV / ("Scripts" if os.name == "nt" else "bin")
PY = BIN / ("python.exe" if os.name == "nt" else "python")
WEB = ROOT / "web"
NPM = "npm.cmd" if os.name == "nt" else "npm"


def _py() -> str:
    return str(PY) if PY.exists() else sys.executable


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> int:
    """Run a command, streaming output; returns its exit code."""
    sys.stderr.write(f"$ {' '.join(cmd)}\n")
    return subprocess.call(cmd, cwd=cwd, env={**os.environ, **(env or {})})


def setup(extra: list[str]) -> int:
    if not PY.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    code = run([_py(), "-m", "pip", "install", "--upgrade", "pip"])
    return code or run([_py(), "-m", "pip", "install", "-r", "requirements-dev.txt", *extra])


TASKS: dict[str, list[list[str]] | None] = {
    "audit": [["{py}", "-m", "app.audit"]],
    "ingest": [["{py}", "-m", "app.ingest", "--embedder", "openai"]],
    "ingest-openai": [["{py}", "-m", "app.ingest", "--embedder", "openai"]],
    "check-openai": [["{py}", "-m", "app.providers.check"]],
    "lint": [
        ["{py}", "-m", "ruff", "check", "app", "eval", "tests", "scripts"],
        ["{py}", "-m", "ruff", "format", "--check", "app", "eval", "tests", "scripts"],
    ],
    "format": [
        ["{py}", "-m", "ruff", "format", "app", "eval", "tests", "scripts"],
        ["{py}", "-m", "ruff", "check", "--fix", "app", "eval", "tests", "scripts"],
    ],
    "typecheck": [["{py}", "-m", "mypy"]],
    "test": [["{py}", "-m", "pytest", "--cov=app", "--cov-report=term-missing:skip-covered"]],
    "eval": [["{py}", "-m", "eval.run"]],
    "eval-retrieval": [["{py}", "-m", "eval.run", "--retrieval-only"]],
    "calibrate": [["{py}", "-m", "eval.calibrate"]],
    "dev-api": [["{py}", "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"]],
    "dev-web": [[NPM, "run", "dev"]],
    "web-check": [
        [NPM, "run", "lint"],
        [NPM, "run", "typecheck"],
        [NPM, "run", "test"],
        [NPM, "run", "build"],
    ],
    "setup": None,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in TASKS:
        sys.stderr.write(f"usage: python scripts/tasks.py <{'|'.join(TASKS)}> [args]\n")
        return 2
    task, extra = argv[0], argv[1:]
    if task == "setup":
        return setup(extra)
    commands = TASKS[task] or []
    for i, template in enumerate(commands):
        cmd = [_py() if part == "{py}" else part for part in template]
        if i == len(commands) - 1:
            cmd += extra
        cwd = WEB if cmd[0] == NPM else ROOT
        code = run(cmd, cwd=cwd)
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
