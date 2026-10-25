"""Block personal or private information from reaching this public repository.

Scans everything a range of commits adds (file names, added lines, commit messages and
author/committer identities) for:

* private network addresses, home directory paths and local tooling paths;
* email addresses other than GitHub noreply or documentation ones;
* credentials (GitHub tokens, AWS keys, private keys);
* anything matching an optional denylist file of extra regular expressions, one per line
  (``#`` comments allowed). Keep personal names and places in such a file OUTSIDE the
  repository, never in it.

Findings print the file and line and a label, never the matched text, because CI logs of a
public repository are public too.

Usage::

    python3 scripts/check_privacy.py --base origin/main --head HEAD [--denylist FILE]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GENERIC: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private IPv4 address",
        re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
            r"|192\.168(?:\.\d{1,3}){2})\b"
        ),
    ),
    ("home directory path", re.compile(r"(?:/home/|/Users/)[A-Za-z0-9._-]+")),
    ("local tooling path", re.compile(r"/var/lib/ha\b|/tmp/claude-")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Domains that are public by design; "git@github.com" style remotes land here too.
EMAIL_DOMAINS_OK = (
    "users.noreply.github.com",
    "noreply.github.com",
    "github.com",
    "example.com",
    "example.org",
    "example.net",
    "anthropic.com",
)
IDENTITY_DOMAINS_OK = ("users.noreply.github.com",)
IDENTITY_ADDRESSES_OK = ("noreply@github.com",)  # the committer GitHub records for web merges
# The lock file is generated; the scanner and its tests contain synthetic examples on purpose.
EXCLUDED_FILES = ("uv.lock", "scripts/check_privacy.py", "tests/model/test_privacy_check.py")


@dataclass(frozen=True)
class Finding:
    """One problem: where it is and what kind, never the matched text."""

    where: str
    label: str


def load_denylist(path: Path | None) -> list[re.Pattern[str]]:
    """Extra patterns from a file kept outside the repository."""
    if path is None:
        return []
    patterns: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(line, re.IGNORECASE))
    return patterns


def scan_text(text: str, where: str, denylist: list[re.Pattern[str]]) -> list[Finding]:
    """Findings in one piece of text."""
    found: list[Finding] = []
    for label, pattern in GENERIC:
        if pattern.search(text):
            found.append(Finding(where, label))
    for match in EMAIL.finditer(text):
        domain = match.group(1).lower()
        if not any(domain == ok or domain.endswith("." + ok) for ok in EMAIL_DOMAINS_OK):
            found.append(Finding(where, "email address"))
    for index, pattern in enumerate(denylist, start=1):
        if pattern.search(text):
            found.append(Finding(where, f"denylist entry {index}"))
    return found


def scan_identity(name_and_email: str, where: str) -> list[Finding]:
    """Commit author and committer emails must be GitHub noreply addresses."""
    match = EMAIL.search(name_and_email)
    if match is None:
        return []
    domain = match.group(1).lower()
    if match.group(0).lower() in IDENTITY_ADDRESSES_OK:
        return []
    if any(domain == ok or domain.endswith("." + ok) for ok in IDENTITY_DOMAINS_OK):
        return []
    return [Finding(where, "personal commit email")]


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 (fixed git argv, no shell)
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def scan_range(base: str, head: str, denylist: list[re.Pattern[str]]) -> list[Finding]:
    """Scan every commit in ``base..head``."""
    findings: list[Finding] = []
    for commit in _git("rev-list", f"{base}..{head}").split():
        short = commit[:8]
        identities = _git("log", "-1", "--format=%an <%ae>%n%cn <%ce>", commit).splitlines()
        for ident in identities:
            findings += scan_identity(ident, f"commit {short} identity")
        message = _git("log", "-1", "--format=%B", commit)
        findings += scan_text(message, f"commit {short} message", denylist)
        files = _git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        for name in files:
            findings += scan_text(name, f"commit {short} file name", denylist)
        for name in files:
            if name in EXCLUDED_FILES:
                continue
            diff = _git("show", "--format=", "--unified=0", commit, "--", name)
            for number, line in enumerate(diff.splitlines(), start=1):
                if line.startswith("+") and not line.startswith("+++"):
                    findings += scan_text(
                        line[1:], f"{name} (commit {short}, diff line {number})", denylist
                    )
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exits 1 when anything is found."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="exclusive start of the range")
    parser.add_argument("--head", default="HEAD", help="inclusive end of the range")
    parser.add_argument("--denylist", type=Path, help="file of extra regexes, one per line")
    args = parser.parse_args(argv)
    denylist = load_denylist(args.denylist)
    findings = scan_range(args.base, args.head, denylist)
    for finding in findings:
        print(f"{finding.where}: {finding.label}")
    if findings:
        print(f"{len(findings)} privacy finding(s); nothing is printed of the matched text.")
        return 1
    print("No personal or private information found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
