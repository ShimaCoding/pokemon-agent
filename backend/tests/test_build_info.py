"""build_info: the deployed commit from .git files (image) or git (local), DD_VERSION first."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import build_info

SHA = "f4bf26b0123456789abcdef0123456789abcdef0"
OTHER = "0" * 40
REPO = Path(__file__).resolve().parents[2]


def _git_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    git_dir = tmp_path / ".git"
    for name, content in files.items():
        path = git_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git_dir.mkdir(exist_ok=True)
    return git_dir


def test_loose_ref(tmp_path):
    git_dir = _git_dir(tmp_path, {"HEAD": "ref: refs/heads/development\n", "refs/heads/development": SHA + "\n"})
    assert build_info.commit_from_git_dir(git_dir) == SHA


def test_branch_with_slash(tmp_path):
    git_dir = _git_dir(tmp_path, {"HEAD": "ref: refs/heads/obs/21-x\n", "refs/heads/obs/21-x": SHA + "\n"})
    assert build_info.commit_from_git_dir(git_dir) == SHA


def test_packed_ref(tmp_path):
    packed = (
        "# pack-refs with: peeled fully-peeled sorted \n"
        f"{OTHER} refs/remotes/origin/development\n"
        f"{SHA} refs/heads/development\n"
    )
    git_dir = _git_dir(tmp_path, {"HEAD": "ref: refs/heads/development\n", "packed-refs": packed})
    assert build_info.commit_from_git_dir(git_dir) == SHA


def test_detached_head(tmp_path):
    assert build_info.commit_from_git_dir(_git_dir(tmp_path, {"HEAD": SHA + "\n"})) == SHA


def test_shallow_fallback(tmp_path):
    git_dir = _git_dir(tmp_path, {"HEAD": "ref: refs/heads/missing\n", "shallow": SHA + "\n"})
    assert build_info.commit_from_git_dir(git_dir) == SHA

    (git_dir / "shallow").write_text(f"{SHA}\n{OTHER}\n")  # several boundaries: HEAD is unknown
    assert build_info.commit_from_git_dir(git_dir) is None


@pytest.mark.parametrize("head", [None, "not a sha\n", "ref: refs/heads/missing\n", SHA[:7]])
def test_missing_or_garbage(tmp_path, head):
    git_dir = _git_dir(tmp_path, {} if head is None else {"HEAD": head})
    assert build_info.commit_from_git_dir(git_dir) is None


def test_version_prefers_env(monkeypatch):
    monkeypatch.setenv("DD_VERSION", " custom ")
    assert build_info.version() == "custom"

    monkeypatch.delenv("DD_VERSION")
    monkeypatch.setattr(build_info, "commit_sha", lambda: SHA)
    assert build_info.version() == SHA[:7]

    monkeypatch.setattr(build_info, "commit_sha", lambda: None)
    assert build_info.version() == ""


def test_commit_sha_reads_git_dir_without_git_binary(monkeypatch, tmp_path):
    _git_dir(tmp_path, {"HEAD": "ref: refs/heads/development\n", "refs/heads/development": SHA})
    monkeypatch.setattr(build_info, "_ROOT", tmp_path)
    monkeypatch.setattr(build_info, "_commit_from_git_cli", lambda root: pytest.fail("git CLI used"))
    build_info.commit_sha.cache_clear()
    try:
        assert build_info.commit_sha() == SHA
    finally:
        build_info.commit_sha.cache_clear()


def test_commit_sha_without_git_at_all(monkeypatch, tmp_path):
    """Image built from a context without .git and no git binary: version is just empty."""
    monkeypatch.setattr(build_info, "_ROOT", tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # no git binary → FileNotFoundError
    monkeypatch.delenv("DD_VERSION", raising=False)
    build_info.commit_sha.cache_clear()
    try:
        assert build_info.commit_sha() is None
        assert build_info.version() == ""
    finally:
        build_info.commit_sha.cache_clear()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_real_repo_resolves():
    build_info.commit_sha.cache_clear()
    try:
        sha = build_info.commit_sha()
    finally:
        build_info.commit_sha.cache_clear()
    assert sha and re.fullmatch(r"[0-9a-f]{40}", sha)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_dockerignore_slice_of_depth1_clone_resolves(tmp_path):
    """What the image gets: a Dokploy-style `git clone --depth 1 --branch X`, reduced to
    the paths .dockerignore re-includes, must still resolve to the cloned HEAD."""
    src = tmp_path / "src"
    src.mkdir()

    def git(*args: str, cwd: Path = src) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    (src / "a.txt").write_text("1")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    git("checkout", "-q", "-b", "obs/21-x")
    (src / "a.txt").write_text("2")
    git("commit", "-q", "-am", "two")
    head = git("rev-parse", "HEAD")

    clone = tmp_path / "code"
    git("clone", "-q", "--depth", "1", "--branch", "obs/21-x", f"file://{src}", str(clone), cwd=tmp_path)

    included = [
        line[1:].removeprefix(".git/")
        for line in (REPO / ".dockerignore").read_text().splitlines()
        if line.startswith("!.git/")
    ]
    app_git = tmp_path / "app" / ".git"
    app_git.mkdir(parents=True)
    for rel in included:
        path = clone / ".git" / rel
        if path.is_dir():
            shutil.copytree(path, app_git / rel)
        elif path.exists():
            shutil.copy(path, app_git / rel)

    assert not (app_git / "config").exists()
    assert build_info.commit_from_git_dir(app_git) == head
