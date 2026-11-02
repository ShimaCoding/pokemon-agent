"""
Commit of the running code, used as the Datadog ``version`` tag and shown on /health.

In the image there is no git binary, only a slice of ``.git`` (HEAD and refs, see
.dockerignore and the Dockerfile), so the SHA is read straight from those files.
Locally (worktrees have a ``.git`` file, not a directory) ``git rev-parse`` is used.
``DD_VERSION`` always wins. Pure stdlib: this module must not import ddtrace.
"""

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

REPOSITORY_URL = "https://github.com/ShimaCoding/pokemon-agent"

_ROOT = Path(__file__).resolve().parent.parent  # /app in the image, repo root locally
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def commit_from_git_dir(git_dir: Path) -> str | None:
    """Full SHA of HEAD from .git files (no git binary). Handles detached HEAD,
    loose refs, packed-refs and, last, a depth-1 clone's .git/shallow."""
    head = _read(git_dir / "HEAD")
    if _SHA.match(head):
        return head
    if head.startswith("ref: "):
        ref = head[5:].strip()
        loose = _read(git_dir / ref)
        if _SHA.match(loose):
            return loose
        for line in _read(git_dir / "packed-refs").splitlines():
            sha, _, name = line.partition(" ")
            if name.strip() == ref and _SHA.match(sha):
                return sha
    shallow = _read(git_dir / "shallow").splitlines()
    if len(shallow) == 1 and _SHA.match(shallow[0]):  # depth-1 clone: the one boundary is HEAD
        return shallow[0]
    return None


def _commit_from_git_cli(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if _SHA.match(out) else None


@lru_cache(maxsize=1)
def commit_sha() -> str | None:
    """Full SHA of the running code, or None if it cannot be known."""
    git_dir = _ROOT / ".git"
    return (commit_from_git_dir(git_dir) if git_dir.is_dir() else None) or _commit_from_git_cli(_ROOT)


def version() -> str:
    """DD_VERSION if set, else the 7-char commit, else ''."""
    explicit = os.environ.get("DD_VERSION", "").strip()
    if explicit:
        return explicit
    sha = commit_sha()
    return sha[:7] if sha else ""
