"""Thin `git` wrapper: one place for the environment and the error type."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from fpgatools.cli import FpgatoolsError


class GitError(FpgatoolsError):
    """A git command exited non-zero (and `check` was requested)."""

    def __init__(self, args: Sequence[str], cwd: Path, result: subprocess.CompletedProcess):
        self.args_ = list(args)
        self.cwd = cwd
        self.returncode = result.returncode
        self.stderr = (result.stderr or "").strip()
        detail = f": {self.stderr}" if self.stderr else ""
        super().__init__(
            f"git {' '.join(self.args_)} failed in {cwd} (exit {self.returncode}){detail}"
        )


def run_git(
    args: Sequence[str],
    cwd: Path,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run `git <args>` in `cwd`.

    Output is captured (and stripped by callers) unless `capture=False`, in
    which case it goes straight to the terminal. Interactive prompts are
    disabled and messages come out in English so they can be parsed.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C.UTF-8")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=capture,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise FpgatoolsError("git is not installed or not on PATH") from None
    if check and result.returncode != 0:
        raise GitError(args, Path(cwd), result)
    return result


def git_output(args: Sequence[str], cwd: Path) -> str:
    """`run_git` with the stripped stdout returned."""
    return run_git(args, cwd).stdout.strip()
