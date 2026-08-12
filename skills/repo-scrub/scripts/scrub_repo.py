#!/usr/bin/env python3
"""Scrub a repo of leaked secrets/env keys and fix IP hygiene before it is shared.

Two safety tiers:
  - Default (scan): report only. Secrets (redacted), .env files, tracked-secret history hits,
    LICENSE presence, and AI/non-employee committers.
  - --fix: the SAFE, reversible actions — untrack .env (git rm --cached) + add to .gitignore +
    write a values-blanked .env.example; write a LICENSE (with --license/--owner). Code-embedded
    secrets are redacted in the WORKING TREE only when you also pass --redact-code.

NEVER rewrites git history automatically (destructive). If a secret is found in committed history
it is reported with the exact git-filter-repo command for you to run deliberately. Rotating a
leaked key is a human step this tool cannot do — it only stops the bleeding in the tree.

Usage:
  python3 scrub_repo.py /path/to/repo                          # scan/report
  python3 scrub_repo.py /path/to/repo --fix --license Proprietary --owner "Your Company, Inc."
  python3 scrub_repo.py /path/to/repo --fix --redact-code      # also redact secrets in tracked code
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

SECRET_RES = [
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("api_key_assign", re.compile(r"(?i)\b([A-Z0-9_]*(?:API|SECRET|ACCESS|TOKEN|KEY)[A-Z0-9_]*)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{16,})['\"]")),
    ("password_assign", re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"]([^'\"]{6,})['\"]")),
    ("env_value", re.compile(r"(?m)^\s*([A-Z0-9_]{3,})\s*=\s*(\S{12,})\s*$")),  # for .env files
]
VENDOR = {"node_modules", "vendor", ".git", "dist", "build", "__pycache__", ".venv", "venv"}
LICENSES = {
    "Proprietary": "Copyright (c) {year} {owner}. All rights reserved.\nProprietary and confidential. No license is granted to use, copy, modify, or distribute this software without the express written permission of the copyright holder.\n",
    "MIT": "MIT License\n\nCopyright (c) {year} {owner}\n\nPermission is hereby granted, free of charge, to any person obtaining a copy...\n(full MIT text — fill in)\n",
    "Apache-2.0": "Copyright {year} {owner}\n\nLicensed under the Apache License, Version 2.0 (the \"License\");\nyou may not use this file except in compliance with the License.\nYou may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0\n",
}


def git(repo: Path, *a: str, check=False):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=check)


def redact(val: str) -> str:
    return (val[:3] + "…REDACTED…" + val[-2:]) if len(val) > 6 else "REDACTED"


def scan(repo: Path) -> dict:
    tracked = [l for l in git(repo, "ls-files").stdout.splitlines()]
    findings = []
    env_files = []
    for rel in tracked:
        p = Path(rel)
        if any(x in p.parts for x in VENDOR):
            continue
        is_env = p.name == ".env" or p.name.startswith(".env.") and not p.name.endswith(".example")
        f = repo / rel
        try:
            if f.stat().st_size > 1_000_000:
                continue
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if is_env:
            env_files.append(rel)
        for kind, rx in SECRET_RES:
            if kind == "env_value" and not is_env:
                continue
            for m in rx.finditer(text):
                val = m.group(m.lastindex) if m.lastindex else m.group(0)
                line = text[: m.start()].count("\n") + 1
                findings.append({"file": rel, "line": line, "kind": kind, "value": redact(val)})
    # untracked .env in the working tree (common case: .env present but gitignored)
    for f in repo.rglob(".env"):
        if not any(x in f.parts for x in VENDOR):
            rel = str(f.relative_to(repo))
            tracked_now = rel in tracked
            env_files.append(rel + ("" if tracked_now else " (untracked)"))
    # history hits (report only)
    hist = {}
    for kind in ("AKIA", "PRIVATE KEY", "API_KEY", "SECRET", "TOKEN"):
        r = git(repo, "log", "--all", "-S", kind, "--oneline")
        n = len([x for x in r.stdout.splitlines() if x.strip()])
        if n:
            hist[kind] = n
    license_present = any((repo / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE"))
    committers = sorted({l.split("\t")[-1] for l in git(repo, "shortlog", "-sne", "HEAD").stdout.splitlines() if "\t" in l})
    ai_committers = [c for c in committers if re.search(r"(?i)devin|cursor|copilot|bot\b|test@example|noreply", c)]
    return {"findings": findings, "env_files": sorted(set(env_files)), "history_hits": hist,
            "license_present": license_present, "committers": committers, "ai_or_suspect_committers": ai_committers}


def do_fix(repo: Path, s: dict, args) -> list[str]:
    done = []
    # 1. untrack + ignore .env; write blanked .env.example
    gitignore = repo / ".gitignore"
    ign = gitignore.read_text() if gitignore.exists() else ""
    for pat in (".env", ".env.*"):
        if pat not in ign:
            ign += ("\n" if ign and not ign.endswith("\n") else "") + pat + "\n"
    gitignore.write_text(ign)
    done.append("ensured .env/.env.* in .gitignore")
    for rel in [e.replace(" (untracked)", "") for e in s["env_files"] if not e.endswith(".example")]:
        p = repo / rel
        if p.name.endswith(".example"):
            continue
        r = git(repo, "rm", "--cached", rel)
        if r.returncode == 0:
            done.append(f"git rm --cached {rel}")
        if p.exists():
            ex = p.parent / (p.name + ".example")
            blanked = re.sub(r"(?m)^(\s*[A-Za-z0-9_]+\s*=).*$", r"\1", p.read_text(errors="replace"))
            ex.write_text(blanked)
            done.append(f"wrote {ex.relative_to(repo)} (values blanked)")
    # 2. LICENSE
    if not s["license_present"] and args.license:
        body = LICENSES.get(args.license, LICENSES["Proprietary"]).format(year="2026", owner=args.owner or "the owner")
        (repo / "LICENSE").write_text(body)
        done.append(f"wrote LICENSE ({args.license})")
    # 3. redact secrets in tracked code (opt-in)
    if args.redact_code:
        for fnd in s["findings"]:
            if fnd["kind"] == "env_value":
                continue
            p = repo / fnd["file"]
            if not p.exists():
                continue
            text = p.read_text(errors="replace")
            for kind, rx in SECRET_RES:
                if kind == "env_value":
                    continue
                def _r(m):
                    if m.lastindex:
                        return m.group(0).replace(m.group(m.lastindex), "REDACTED_SECRET")
                    return "REDACTED_SECRET"
                text = rx.sub(_r, text)
            p.write_text(text)
        done.append("redacted secrets in tracked code (working tree)")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--redact-code", action="store_true")
    ap.add_argument("--license", choices=list(LICENSES))
    ap.add_argument("--owner")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"not a git repo: {repo}")

    s = scan(repo)
    print(f"=== scrub scan: {repo.name} ===")
    print(f"secrets in tracked files : {len(s['findings'])}")
    for f in s["findings"][:30]:
        print(f"   {f['file']}:{f['line']}  [{f['kind']}] {f['value']}")
    print(f".env files               : {s['env_files'] or 'none'}")
    print(f"history hits (report only): {s['history_hits'] or 'none'}")
    print(f"LICENSE present          : {s['license_present']}")
    print(f"AI/suspect committers    : {s['ai_or_suspect_committers'] or 'none'}")
    if s["history_hits"]:
        print("\n!! secrets found in git HISTORY. Rotate the keys, then (deliberately, destructive):")
        print("   git filter-repo --replace-text <(echo 'LEAKED==>REDACTED')   # requires git-filter-repo; force-push after")

    if args.fix:
        print("\n=== applying safe fixes ===")
        for d in do_fix(repo, s, args):
            print("  ✓", d)
        print("\nManual still required: ROTATE any leaked key; resolve AI/non-employee authorship (IP assignment); scrub history if hits above.")


if __name__ == "__main__":
    main()
