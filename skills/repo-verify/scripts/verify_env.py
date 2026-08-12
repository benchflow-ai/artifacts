#!/usr/bin/env python3
"""EXECUTE a base->gold fail->pass check for a fix PR. Turns a heuristic "verifiable candidate"
into a proven regression test (or honestly marks it unreproducible).

The check (SWE-bench-style):
  1. base = commit before the PR; head = the PR's merged state.
  2. Split the PR diff into TEST files and SOURCE files.
  3. In an isolated git worktree at base: apply ONLY the PR's tests -> run -> expect FAIL.
  4. Apply the PR's source too -> run -> expect PASS.
A real fail->pass transition (FAIL then PASS) => verified. Anything else (build fails, tests
already pass at base, e2e/DB-backed) => NOT verified, recorded with the reason. No inflation.

Dynamic + online is fine (installs deps). Runs in a throwaway worktree; never touches your checkout.

Usage:
  python3 verify_env.py /path/to/repo --pr 22
  python3 verify_env.py /path/to/repo --base <sha> --head <sha>
Requires: git, and the repo's test runner (pytest / vitest|jest / go). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TEST_PATH = re.compile(r"(^|/)(tests?|testing|spec|specs|__tests__|e2e|cypress|playwright)(/|$)|([._-](test|spec)\.)|((^|/)test_)|(_test\.(go|py|rb|js|ts)$)", re.IGNORECASE)
E2E = re.compile(r"(e2e|cypress|playwright|\.spec\.(t|j)sx?$|integration)", re.IGNORECASE)
CODE = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".rs", ".php", ".c", ".cc", ".cpp"}


def git(cwd, *a, timeout=300):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, timeout=timeout)


def run(cwd, args, timeout=900):
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout[-2000:] + p.stderr[-2000:])
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError as e:
        return 127, f"missing tool: {e}"


def resolve_pr(repo: Path, pr: int) -> tuple[str, str] | None:
    # try the merge commit in local history: "...#<pr>..." on a merge
    log = git(repo, "log", "--merges", "--grep", f"#{pr}\\b", "-E", "--format=%H %P", "-n", "1").stdout.strip()
    if log:
        parts = log.split()
        if len(parts) >= 3:
            return parts[1], parts[0]  # (base=first parent, head=merge commit)
    # fallback: gh
    slug = git(repo, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/.]+)", slug)
    if m:
        r = subprocess.run(["gh", "pr", "view", str(pr), "--repo", m.group(1),
                            "--json", "baseRefOid,mergeCommit,headRefOid"], capture_output=True, text=True)
        if r.returncode == 0:
            d = json.loads(r.stdout)
            base = d.get("baseRefOid")
            head = (d.get("mergeCommit") or {}).get("oid") or d.get("headRefOid")
            if base and head:
                return base, head
    return None


def _nearest(wt: Path, test_rel: str, names) -> Path:
    """Walk up from a test file to wt, return the first dir containing one of `names`."""
    if isinstance(names, str):
        names = [names]
    d = (wt / test_rel).parent
    while True:
        if any((d / n).exists() for n in names):
            return d
        if d == wt or wt not in d.parents:
            return wt
        d = d.parent


def resolve_runner(wt: Path, tests: list[str]) -> tuple[str, Path]:
    """Pick the runner from the TEST FILE extension (not repo-root files), and the working dir
    = nearest manifest above the tests (a monorepo's package.json may be in a subdir)."""
    exts = {Path(t).suffix.lower() for t in tests}
    if exts & {".ts", ".tsx", ".js", ".jsx"}:
        d = _nearest(wt, tests[0], "package.json")
        pj = d / "package.json"
        txt = pj.read_text(errors="replace") if pj.exists() else ""
        return ("vitest" if "vitest" in txt else "jest" if "jest" in txt else "vitest"), d
    if ".py" in exts:
        d = _nearest(wt, tests[0], ["pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "conftest.py"])
        return "pytest", d
    if ".go" in exts:
        return "go", _nearest(wt, tests[0], "go.mod")
    return "unknown", wt


def install(wt: Path, runner: str) -> str:
    if runner in ("vitest", "jest"):
        for cmd in (["npm", "ci", "--no-audit", "--no-fund"], ["npm", "install", "--no-audit", "--no-fund"]):
            rc, _ = run(wt, cmd, timeout=600)
            if rc == 0:
                return "npm ok"
        return "npm failed"
    if runner == "pytest":
        for cmd in (["uv", "sync", "--frozen"], ["python3", "-m", "pip", "install", "-e", ".", "-q"]):
            rc, _ = run(wt, cmd, timeout=600)
            if rc == 0:
                return "py deps ok"
        return "py deps best-effort"
    if runner == "go":
        run(wt, ["go", "mod", "download"], timeout=300)
        return "go mod ok"
    return "n/a"


def test_cmd(runner: str, tests: list[str]) -> list[str]:
    if runner == "pytest":
        return ["python3", "-m", "pytest", "-x", "-q", *tests]
    if runner == "vitest":
        return ["npx", "vitest", "run", *tests]
    if runner == "jest":
        return ["npx", "jest", *tests]
    if runner == "go":
        pkgs = sorted({"./" + str(Path(t).parent) for t in tests})
        return ["go", "test", *pkgs]
    return ["false"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--out")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    base, head = (args.base, args.head)
    if args.pr:
        r = resolve_pr(repo, args.pr)
        if not r:
            raise SystemExit(f"could not resolve PR #{args.pr} to base/head SHAs; pass --base/--head")
        base, head = r
    if not (base and head):
        raise SystemExit("provide --pr or both --base and --head")

    changed = git(repo, "diff", "--name-only", base, head).stdout.splitlines()
    tests = [f for f in changed if TEST_PATH.search(f) and Path(f).suffix in CODE]
    srcs = [f for f in changed if f not in tests and Path(f).suffix in CODE]
    result = {"repo": repo.name, "base": base[:12], "head": head[:12],
              "changed": len(changed), "test_files": tests, "src_files": srcs}
    if not tests:
        result.update(verified=False, reason="PR adds/changes no test files -> no fail->pass to check")
        _emit(result, args); return
    if any(E2E.search(t) for t in tests):
        result["warning"] = "e2e/integration tests present; hermetic reproduction may need a live server/DB"

    wt = Path(tempfile.mkdtemp(prefix="repo-verify-"))
    try:
        rc, err = run(repo.parent, ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), base])
        if rc != 0:
            result.update(verified=False, reason=f"worktree/base checkout failed: {err[:200]}"); _emit(result, args); return
        runner, workdir = resolve_runner(wt, tests)
        result["runner"] = runner
        result["workdir"] = str(workdir.relative_to(wt)) or "."
        if runner == "unknown":
            result.update(verified=False, reason="could not detect a test runner from the test file extensions"); return
        result["install"] = install(workdir, runner)
        rel_tests = [os.path.relpath(wt / t, workdir) for t in tests]

        # Step A: apply ONLY the PR's tests onto base -> expect FAIL
        git(wt, "checkout", head, "--", *tests)
        rc_a, out_a = run(workdir, test_cmd(runner, rel_tests))
        # Step B: apply the PR's source too -> expect PASS
        if srcs:
            git(wt, "checkout", head, "--", *srcs)
        rc_b, out_b = run(workdir, test_cmd(runner, rel_tests))

        fail_then_pass = (rc_a != 0) and (rc_b == 0)
        result.update(
            base_with_tests_rc=rc_a, gold_rc=rc_b,
            verified=fail_then_pass,
            reason=("real fail->pass" if fail_then_pass else
                    "tests already PASS at base (no bug transition)" if rc_a == 0 else
                    "still FAIL after gold applied (build/env issue or non-hermetic)"),
            base_tail=out_a[-600:], gold_tail=out_b[-600:],
        )
    finally:
        git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)
    _emit(result, args)


def _emit(result, args):
    out = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    print(f"\n=> verified={result.get('verified')}  ({result.get('reason')})")


if __name__ == "__main__":
    main()
