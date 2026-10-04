"""Publish this project to GitHub with the REST API — no git install required.

Creates the repository if needed, uploads every tracked file as a blob, and makes
one commit. Existing repositories get a normal fast-forward commit on top.

    python tools/publish_github.py --repo deepseek-pet --public --dry-run
    python tools/publish_github.py --repo deepseek-pet --public --token-file C:/Users/KF/gh_token.txt

Auth: pass --token-file (recommended: keeps the token out of your shell history
and this chat) or set GITHUB_TOKEN. A classic token needs the `repo` scope; a
fine-grained token needs Contents:write plus Administration:write to create a repo.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
SKIP_FILES = {"pet_config.json", "gh_token.txt"}
SKIP_SUFFIX = (".pyc", ".pyo")
# The character came from a third-party screenshot, so the reference image and
# the cutout it was traced from are not redistributed.
DEFAULT_EXCLUDES = ["assets/source/**"]

MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def api(method: str, path: str, token: str, payload: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path,
        method=method,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "deepseek-pet-publisher",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"! {method} {path} -> HTTP {exc.code}\n{detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"! cannot reach GitHub: {exc.reason}") from None


def collect(excludes: list[str]) -> list[Path]:
    patterns = DEFAULT_EXCLUDES + excludes
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name in SKIP_FILES or rel.name.endswith(SKIP_SUFFIX):
            continue
        if any(rel.match(pattern) for pattern in patterns):
            continue
        files.append(path)
    return files


def ensure_license(holder: str) -> None:
    path = ROOT / "LICENSE"
    if path.exists():
        print("LICENSE already present")
        return
    path.write_text(MIT.format(year=time.localtime().tm_year, holder=holder), encoding="utf-8")
    print(f"wrote MIT LICENSE for {holder}")


def ensure_initialized(owner: str, repo: str, token: str, branch: str) -> str:
    """Make sure the repository has at least one commit.

    The Git Data API answers `409 Git Repository is empty` when you try to create
    a blob in a repo that has never had a commit, so an empty repo gets bootstrapped
    through the Contents API first (which is allowed to create the first commit).
    Returns the branch that actually exists.
    """
    for candidate in (branch, "main", "master"):
        try:
            api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{candidate}", token)
            return candidate
        except SystemExit:
            continue
    print(f"empty repository — bootstrapping an initial commit on {branch}")
    api("PUT", f"/repos/{owner}/{repo}/contents/README.md", token, {
        "message": "chore: initialize repository",
        "content": base64.b64encode("# deepseek-pet\n".encode("utf-8")).decode("ascii"),
        "branch": branch,
    })
    return branch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="repository name, e.g. deepseek-pet")
    ap.add_argument("--owner", default="", help="defaults to the token's own account")
    ap.add_argument("--description", default="DeepSeek 大肥鱼桌宠 — 透明置顶桌宠，对话直接接入 DeepSeek Harness")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--public", action="store_true", help="create as public (default: private)")
    ap.add_argument("--token-file", default="", help="file containing the token")
    ap.add_argument("--message", default="DeepSeek 大肥鱼桌宠：表情矩阵、Harness 对话接入、可选大小")
    ap.add_argument("--exclude", action="append", default=[], help="glob of paths to skip (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        files = collect(args.exclude)
        total = sum(f.stat().st_size for f in files)
        print(f"{len(files)} files, {total / 1024 / 1024:.2f} MB")
        for f in files:
            print(f"   {f.relative_to(ROOT)}  ({f.stat().st_size / 1024:.0f} KB)")
        return

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("no token: pass --token-file or set GITHUB_TOKEN")

    who = api("GET", "/user", token)
    owner = args.owner or who["login"]
    print(f"authenticated as {who['login']}")
    ensure_license(who.get("name") or who["login"])

    files = collect(args.exclude)
    total = sum(f.stat().st_size for f in files)
    print(f"uploading {len(files)} files ({total / 1024 / 1024:.2f} MB)")

    try:
        repo = api("GET", f"/repos/{owner}/{args.repo}", token)
        print(f"repository {owner}/{args.repo} already exists")
    except SystemExit:
        repo = api("POST", "/user/repos", token, {
            "name": args.repo,
            "description": args.description,
            "private": not args.public,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        })
        print(f"created {'public' if args.public else 'private'} repository {owner}/{args.repo}")

    branch = ensure_initialized(owner, args.repo, token, repo.get("default_branch") or args.branch)
    print(f"branch: {branch}")

    print("uploading blobs …")
    tree = []
    for i, path in enumerate(files, 1):
        rel = path.relative_to(ROOT).as_posix()
        blob = api("POST", f"/repos/{owner}/{args.repo}/git/blobs", token, {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        })
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"   [{i}/{len(files)}] {rel}")

    new_tree = api("POST", f"/repos/{owner}/{args.repo}/git/trees", token, {"tree": tree})

    parents = []
    try:
        ref = api("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{branch}", token)
        parents = [ref["object"]["sha"]]
        print(f"building on existing {branch} ({parents[0][:7]})")
    except SystemExit:
        print(f"{branch} does not exist yet — first commit")

    commit = api("POST", f"/repos/{owner}/{args.repo}/git/commits", token, {
        "message": args.message,
        "tree": new_tree["sha"],
        "parents": parents,
    })

    if parents:
        api("PATCH", f"/repos/{owner}/{args.repo}/git/refs/heads/{branch}", token,
            {"sha": commit["sha"], "force": False})
    else:
        api("POST", f"/repos/{owner}/{args.repo}/git/refs", token,
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})

    print()
    print(f"pushed commit {commit['sha'][:7]}")
    print(f"https://github.com/{owner}/{args.repo}")


if __name__ == "__main__":
    main()
