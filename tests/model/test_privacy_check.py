"""The privacy scanner: what it flags, what it allows, and that it never echoes matches."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_privacy as cp  # noqa: E402


def labels(text: str, denylist: list[re.Pattern[str]] | None = None) -> list[str]:
    return [f.label for f in cp.scan_text(text, "x", denylist or [])]


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("host = 10.20.30.40", "private IPv4 address"),
        ("host = 192.168.1.5", "private IPv4 address"),
        ("host = 172.16.0.9", "private IPv4 address"),
        ("path = /home/someone/project", "home directory path"),
        ("path = /Users/someone/project", "home directory path"),
        ("path = /var/lib/ha/config", "local tooling path"),
        ("path = /tmp/claude-1000/x", "local tooling path"),
        ("token = ghp_" + "a" * 30, "GitHub token"),
        ("token = github_pat_" + "a" * 30, "GitHub token"),
        ("key = AKIA" + "A" * 16, "AWS access key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
        ("mail me at someone@mail.test", "email address"),
    ],
)
def test_flags(text: str, label: str) -> None:
    assert label in labels(text)


@pytest.mark.parametrize(
    "text",
    [
        "host = 192.0.2.1",  # documentation range
        "host = 8.8.8.8",
        "version = 1.2.3.4",
        "git@github.com:owner/repo.git",
        "someone@example.com",
        "12345+name@users.noreply.github.com",
        "path = /home",
        "a perfectly ordinary line",
    ],
)
def test_allows(text: str) -> None:
    assert labels(text) == []


def test_denylist_matches_case_insensitively_and_is_numbered(tmp_path: Path) -> None:
    file = tmp_path / "deny.txt"
    file.write_text("# comment\n\nsecretword\nother\\d+\n", encoding="utf-8")
    denylist = cp.load_denylist(file)
    assert len(denylist) == 2
    assert labels("has SecretWord inside", denylist) == ["denylist entry 1"]
    assert labels("other42", denylist) == ["denylist entry 2"]
    assert labels("clean", denylist) == []
    assert cp.load_denylist(None) == []


@pytest.mark.parametrize(
    ("identity", "bad"),
    [
        ("Name <someone@mail.test>", True),
        ("Name <someone@example.com>", True),
        ("name <12345+name@users.noreply.github.com>", False),
        ("GitHub <noreply@github.com>", False),
        ("no email here", False),
    ],
)
def test_identity(identity: str, bad: bool) -> None:
    assert bool(cp.scan_identity(identity, "c")) is bad


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)  # noqa: S603, S607


def _commit(repo: Path, name: str, content: str, message: str, email: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=Tester", "-c", f"user.email={email}", "commit", "-q", "-m", message)


def test_committer_identity_is_not_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    noreply = "1+user@users.noreply.github.com"
    _commit(repo, "a.txt", "hello\n", "init", noreply)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "b.txt").write_text("fine\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Author",
        "GIT_AUTHOR_EMAIL": noreply,
        "GIT_COMMITTER_NAME": "Merger",
        "GIT_COMMITTER_EMAIL": "merger@mail.test",
    }
    _git(repo, "commit", "-q", "-m", "feat: x", env=env)
    monkeypatch.chdir(repo)
    assert cp.main(["--base", base, "--head", "HEAD"]) == 0


def test_scan_range_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    noreply = "1+user@users.noreply.github.com"
    _commit(repo, "a.txt", "hello\n", "init", noreply)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _commit(repo, "ok.txt", "nothing private\n", "feat: fine", noreply)
    monkeypatch.chdir(repo)
    assert cp.main(["--base", base, "--head", "HEAD"]) == 0

    _commit(
        repo, "bad.txt", "ip 10.1.2.3\n", "fix: with 192.168.0.1 in the message", "me@mail.test"
    )
    _commit(repo, "uv.lock", "10.9.9.9 is ignored in the lock file\n", "build: lock", noreply)
    assert cp.main(["--base", base, "--head", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "personal commit email" in out
    assert "author" in out
    assert "private IPv4 address" in out
    assert "bad.txt" in out
    assert "uv.lock" not in out  # excluded from content scanning
    # findings never echo the matched text into (public) logs
    assert "10.1.2.3" not in out
    assert "192.168.0.1" not in out
    assert "me@mail.test" not in out
