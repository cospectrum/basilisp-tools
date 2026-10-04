"""Fetch the pinned public Basilisp corpus without changing existing checkouts.

Run: uv run python scripts/fetch_public_projects.py /tmp/blt-public-corpus
"""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).with_name("public_projects.json")


def git(*args):
    return subprocess.run(
        ["git", *map(str, args)],
        check=True,
        text=True,
        capture_output=True,
        timeout=180,
    ).stdout.strip()


def fetch(entry, corpus):
    repo, sha = entry["repo"], entry["sha"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
        part in {".", ".."} for part in repo.split("/")
    ):
        raise ValueError(f"Invalid repository: {repo}")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"Invalid revision for {repo}: {sha}")
    target = corpus / repo.split("/")[1]
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not (target / ".git").is_dir():
            raise ValueError(f"Refusing to replace {target}")
        actual = git("-C", target, "rev-parse", "HEAD")
        if actual != sha or git(
            "-C", target, "status", "--porcelain", "--untracked-files=no"
        ):
            raise ValueError(f"Existing checkout differs from the manifest: {target}")
        print(f"Verified {repo}", flush=True)
        return
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=corpus))
    try:
        git("init", "--quiet", staging)
        git("-C", staging, "remote", "add", "origin", f"https://github.com/{repo}.git")
        git("-C", staging, "fetch", "--quiet", "--depth", "1", "origin", sha)
        git("-C", staging, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        if git("-C", staging, "rev-parse", "HEAD") != sha:
            raise ValueError(f"Fetched revision does not match {repo}")
        staging.rename(target)
        print(f"Fetched {repo}", flush=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))
        names = [entry["repo"].split("/")[-1] for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("Manifest contains colliding checkout directory names")
        args.corpus.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            fetch(entry, args.corpus)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        detail = (
            error.stderr
            if isinstance(error, subprocess.CalledProcessError)
            else str(error)
        )
        parser.exit(1, f"{detail}\n")


if __name__ == "__main__":
    main()
