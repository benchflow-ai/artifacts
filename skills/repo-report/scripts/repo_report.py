#!/usr/bin/env python3
"""Generate a metrics report for a git repository.

Measures the codebase (size, languages, tests, PR history, CI/reproducibility signals, license)
and pulls representative material: real code excerpts and up to 3 sample pull requests with
diffs. Flags secrets and PII found in tracked files.

It measures and describes; it does not score or grade. Reads the local working tree and git
history only — nothing is uploaded.

Usage:
    python3 repo_report.py /path/to/repo [--out ./report] [--no-excerpts]

Outputs (in --out):
    repo_report.json   full metrics + highlights + excerpts + sample PRs + flags
    metrics.csv        one flat row, for spreadsheets
    report.md          readable report

NOTE: by default the report CONTAINS REAL CODE (excerpts and diff hunks) with secret-shaped
values redacted. Review before sharing, or pass --no-excerpts.

Requires: Python 3.9+, git on PATH. Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants

CODE_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".go": "Go", ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin",
    ".swift": "Swift", ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++",
    ".hpp": "C/C++ header", ".cs": "C#", ".php": "PHP", ".rs": "Rust", ".scala": "Scala",
    ".m": "Objective-C", ".mm": "Objective-C++", ".dart": "Dart", ".ex": "Elixir",
    ".exs": "Elixir", ".erl": "Erlang", ".clj": "Clojure", ".lua": "Lua", ".r": "R",
    ".jl": "Julia", ".sql": "SQL", ".sh": "Shell", ".vue": "Vue", ".svelte": "Svelte",
}
COMMENT_PREFIX = {
    "Python": "#", "Ruby": "#", "Shell": "#", "R": "#", "Elixir": "#", "Julia": "#",
    "JavaScript": "//", "TypeScript": "//", "Go": "//", "Java": "//", "Kotlin": "//",
    "Swift": "//", "C": "//", "C++": "//", "C/C++ header": "//", "C#": "//", "PHP": "//",
    "Rust": "//", "Scala": "//", "Objective-C": "//", "Objective-C++": "//", "Dart": "//",
    "SQL": "--", "Lua": "--",
}
VENDOR_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", "third_party", "bower_components",
    "venv", ".venv", "__pycache__", "target", "Pods", ".next", ".nuxt", "coverage",
    "site-packages", "migrations",
}
TEST_PATH = re.compile(r"(^|/)(tests?|testing|spec|specs|__tests__|e2e|integration[-_]?tests?|cypress|playwright)(/|$)|([._-](test|spec)\.)|((^|/)test_)|((^|/)spec_)|(_test\.(go|py|rb|js|ts|java|c|cpp|rs)$)", re.IGNORECASE)
CI_FILES = [".github/workflows", ".gitlab-ci.yml", ".circleci", "Jenkinsfile", "azure-pipelines.yml", ".travis.yml", "bitbucket-pipelines.yml", ".buildkite"]
REPRO_FILES = ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", ".devcontainer", "flake.nix", "shell.nix", "Vagrantfile"]
LOCK_FILES = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "mix.lock"]
ISSUE_REF = re.compile(r"(#\d{1,6}\b)|([A-Z][A-Z0-9]{1,9}-\d{1,6}\b)|(\b(clos|fix|resolv)(e|es|ed|ing)?\b[:\s]+#?\d+)", re.IGNORECASE)
COPYLEFT = re.compile(r"\b(GPL|AGPL|LGPL|SSPL|EUPL|CC-BY-SA|OSL)\b", re.IGNORECASE)
PERMISSIVE = re.compile(r"\b(MIT|Apache|BSD|ISC|MPL|Unlicense|Zlib|CC0)\b", re.IGNORECASE)
FUNC_RE = {
    "Python": re.compile(r"^\s*(async\s+)?def\s+\w+", re.M),
    "JavaScript": re.compile(r"(\bfunction\s+\w+)|(\w+\s*[:=]\s*(async\s*)?\([^)]*\)\s*=>)", re.M),
    "TypeScript": re.compile(r"(\bfunction\s+\w+)|(\w+\s*[:=]\s*(async\s*)?\([^)]*\)\s*=>)", re.M),
    "Go": re.compile(r"^func\s+(\(\w+\s+\*?\w+\)\s+)?\w+", re.M),
    "Ruby": re.compile(r"^\s*def\s+\w+", re.M),
    "Java": re.compile(r"^\s*(public|private|protected|static|\s)+[\w<>\[\]]+\s+\w+\s*\([^)]*\)\s*(\{|throws)", re.M),
    "Rust": re.compile(r"^\s*(pub\s+)?(async\s+)?fn\s+\w+", re.M),
    "C": re.compile(r"^\w[\w\s\*]+\s+\**\w+\s*\([^;)]*\)\s*\{", re.M),
    "C++": re.compile(r"^\w[\w\s\*:<>,~]+\s+\**[\w:]+\s*\([^;)]*\)\s*(const\s*)?\{", re.M),
    "PHP": re.compile(r"\bfunction\s+\w+", re.M),
    "C#": re.compile(r"^\s*(public|private|protected|internal|static|\s)+[\w<>\[\]]+\s+\w+\s*\([^)]*\)", re.M),
}
CLASS_RE = re.compile(r"^\s*(export\s+)?(abstract\s+)?(public\s+|private\s+|internal\s+)?(class|interface|struct|trait|enum)\s+\w+", re.M)
# secret / PII patterns — count matches ONLY; never record the values
SECRET_RES = [
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("generic_api_key", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
    ("password_assign", re.compile(r"(?i)\bpassword\b\s*[:=]\s*['\"][^'\"]{6,}['\"]")),
]
PII_HINT = re.compile(r"(?i)(\bssn\b|social[_-]?security|date[_-]?of[_-]?birth|\bdob\b|credit[_-]?card|card[_-]?number)")
MAX_FILE_BYTES = 2_000_000


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}...: {r.stderr.strip()[:200]}")
    return r.stdout


def iter_code_files(repo: Path):
    for line in git(repo, "ls-files").splitlines():
        p = Path(line)
        if any(part in VENDOR_DIRS for part in p.parts):
            continue
        if p.name.endswith(".min.js") or p.name.endswith(".min.css"):
            continue
        lang = CODE_EXT.get(p.suffix.lower())
        if lang:
            yield p, lang


def analyze_files(repo: Path) -> dict:
    lang_loc: Counter = Counter()
    total_loc = code_loc = comment_loc = blank_loc = test_loc = 0
    n_files = n_test_files = n_functions = n_classes = pii_files = 0
    secrets: Counter = Counter()
    src_files = []

    for rel, lang in iter_code_files(repo):
        f = repo / rel
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text(errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        n = len(lines)
        is_test = bool(TEST_PATH.search(str(rel)))
        n_files += 1
        total_loc += n
        lang_loc[lang] += n
        prefix = COMMENT_PREFIX.get(lang)
        for ln in lines:
            s = ln.strip()
            if not s:
                blank_loc += 1
            elif prefix and s.startswith(prefix):
                comment_loc += 1
            else:
                code_loc += 1
        if is_test:
            n_test_files += 1
            test_loc += n
        else:
            src_files.append(rel)
        fr = FUNC_RE.get(lang)
        if fr:
            n_functions += len(fr.findall(text))
        n_classes += len(CLASS_RE.findall(text))
        for name, sr in SECRET_RES:
            hits = len(sr.findall(text))
            if hits:
                secrets[name] += hits
        if PII_HINT.search(text):
            pii_files += 1

    test_stems = " ".join(str(p).lower() for p, _ in iter_code_files(repo) if TEST_PATH.search(str(p)))
    untested = sum(1 for rel in src_files if rel.stem.lower() not in test_stems) if test_stems else len(src_files)

    return {
        "files": n_files,
        "test_files": n_test_files,
        "total_loc": total_loc,
        "code_loc": code_loc,
        "comment_loc": comment_loc,
        "blank_loc": blank_loc,
        "comment_ratio_pct": round(100 * comment_loc / max(code_loc + comment_loc, 1), 1),
        "test_loc": test_loc,
        "test_to_code_ratio_pct": round(100 * test_loc / max(total_loc - test_loc, 1), 1),
        "pct_untested_files_heuristic": round(100 * untested / max(len(src_files), 1), 1),
        "functions_est": n_functions,
        "classes_est": n_classes,
        "languages_by_loc": dict(lang_loc.most_common(8)),
        "primary_language": (lang_loc.most_common(1)[0][0] if lang_loc else None),
        "secret_pattern_hits": dict(secrets),
        "files_with_pii_keywords": pii_files,
    }


def analyze_history(repo: Path, merge_sample: int) -> dict:
    n_commits = int(git(repo, "rev-list", "--count", "HEAD").strip() or 0)
    contributors = [ln for ln in git(repo, "shortlog", "-sne", "HEAD").splitlines() if "\t" in ln]
    # earliest commit = the root commit(s); `git log --reverse -n1` wrongly returns the newest.
    root_dates = []
    for rt in git(repo, "rev-list", "--max-parents=0", "HEAD").split():
        try:
            root_dates.append(git(repo, "show", "-s", "--format=%aI", rt).strip()[:10])
        except RuntimeError:
            pass
    first = min(root_dates) if root_dates else ""
    last = git(repo, "log", "-1", "--format=%aI").strip()[:10]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    commits_last_year = int(git(repo, "rev-list", "--count", "--since", cutoff, "HEAD").strip() or 0)

    merges = [m.split("\x00") for m in git(repo, "log", "--merges", "--format=%H%x00%s").splitlines() if "\x00" in m]
    pr_count = len(merges)
    sample = merges[:merge_sample]
    tiers = Counter()
    linked = 0
    loc_per_pr = []
    for sha, _subject in sample:
        try:
            stat = git(repo, "show", "--first-parent", "--numstat", "--format=", sha)
        except RuntimeError:
            continue
        files, adds = 0, 0
        touches_tests = False
        for row in stat.splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            pp = Path(path)
            if any(part in VENDOR_DIRS for part in pp.parts) or pp.suffix.lower() not in CODE_EXT:
                continue
            files += 1
            adds += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
            if TEST_PATH.search(path):
                touches_tests = True
        body = git(repo, "log", "-1", "--format=%B", sha)
        has_ref = bool(ISSUE_REF.search(body))
        if has_ref:
            linked += 1
        if has_ref and (touches_tests or files >= 3):
            tiers["rich"] += 1
        elif files <= 2 and not has_ref:
            tiers["simple"] += 1
        else:
            tiers["standard"] += 1
        if files:
            loc_per_pr.append(adds)

    n = max(len(sample), 1)
    return {
        "commits": n_commits,
        "contributors": len(contributors),
        "first_commit": first,
        "last_commit": last,
        "commits_last_365d": commits_last_year,
        "merged_prs_est": pr_count,
        "pr_sample_analyzed": len(sample),
        "avg_loc_per_pr": round(sum(loc_per_pr) / max(len(loc_per_pr), 1), 1),
        "median_loc_per_pr": (sorted(loc_per_pr)[len(loc_per_pr) // 2] if loc_per_pr else 0),
        "pct_simple_prs": round(100 * tiers["simple"] / n, 1),
        "pct_standard_prs": round(100 * tiers["standard"] / n, 1),
        "pct_rich_prs": round(100 * tiers["rich"] / n, 1),
        "pct_prs_with_issue_ref": round(100 * linked / n, 1),
    }


def analyze_environment(repo: Path) -> dict:
    def any_exists(names):
        return [n for n in names if (repo / n).exists()]
    lic_text = ""
    for name in ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE"]:
        p = repo / name
        if p.exists():
            lic_text = p.read_text(errors="replace")[:4000]
            break
    lic = "none-found"
    if lic_text:
        # Name match first, then canonical phrasing — BSD/MIT license TEXT frequently never
        # contains the words "BSD"/"MIT", which would otherwise misread as proprietary.
        PERMISSIVE_TEXT = (
            "redistribution and use in source and binary forms",   # BSD family
            "permission is hereby granted, free of charge",        # MIT / ISC
            "licensed under the apache license",                   # Apache-2.0
            "mozilla public license",                              # MPL
        )
        low = lic_text.lower()
        if COPYLEFT.search(lic_text):
            lic = "copyleft"
        elif PERMISSIVE.search(lic_text) or any(p in low for p in PERMISSIVE_TEXT):
            lic = "permissive"
        else:
            lic = "custom-or-unclear"
    copyleft_dep_hits = 0
    for mf in ["package.json", "requirements.txt", "pyproject.toml", "Gemfile", "go.mod", "Cargo.toml", "composer.json", "pom.xml"]:
        p = repo / mf
        if p.exists():
            copyleft_dep_hits += len(COPYLEFT.findall(p.read_text(errors="replace")[:100_000]))
    return {
        "ci_configs": any_exists(CI_FILES),
        "reproducibility_files": any_exists(REPRO_FILES),
        "lockfiles": any_exists(LOCK_FILES),
        "repo_license": lic,
        "copyleft_mentions_in_manifests": copyleft_dep_hits,
    }


def build_highlights(files: dict, hist: dict, env: dict) -> list[str]:
    """Factual headline points — descriptive only, no scoring."""
    h = []
    langs = ", ".join(f"{k} {round(100*v/max(files['total_loc'],1))}%" for k, v in files["languages_by_loc"].items())
    h.append(f"{files['total_loc']:,} lines across {files['files']} code files. Primary language {files['primary_language']}. Mix: {langs}.")
    h.append(f"{hist['commits']:,} commits by {hist['contributors']} contributors, {hist['first_commit']} to {hist['last_commit']}.")
    h.append(f"{hist['commits_last_365d']:,} commits in the last 12 months (freshness / activity signal).")
    h.append(f"{hist['merged_prs_est']:,} merged PRs; sampled mix {hist['pct_simple_prs']}% simple / {hist['pct_standard_prs']}% standard / {hist['pct_rich_prs']}% rich.")
    h.append(f"{hist['pct_prs_with_issue_ref']}% of sampled PRs reference an issue in the commit body.")
    h.append(f"Test-to-code ratio {files['test_to_code_ratio_pct']}% ({files['test_files']} test files); ~{files['pct_untested_files_heuristic']}% of source files have no matching test.")
    parts = []
    if env["ci_configs"]:
        parts.append("CI configured (" + ", ".join(env["ci_configs"]) + ")")
    if env["reproducibility_files"]:
        parts.append("containerized/reproducible build (" + ", ".join(env["reproducibility_files"]) + ")")
    if env["lockfiles"]:
        parts.append("dependency lockfiles present")
    if parts:
        h.append("Build: " + "; ".join(parts) + ".")
    h.append(f"License: {env['repo_license']}" + (f"; {env['copyleft_mentions_in_manifests']} copyleft mention(s) in manifests." if env["copyleft_mentions_in_manifests"] else "."))
    h.append(f"~{files['functions_est']:,} functions and ~{files['classes_est']:,} classes/types (regex estimate).")
    return h


BRANCHY = re.compile(r"\b(if|else|elif|for|while|try|except|catch|switch|case|await|async|raise|throw)\b|&&|\|\||\?\?")
DEF_LINE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|internal|static|final|pub|async|def|func|function|fn)\s+)+.*?[\w>]\s*\(|"
    r"^\s*(?:def|func|function|fn)\s+\w+\s*\(",
)


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace any secret-looking value in an excerpt. Excerpts are shared, so never leak a value."""
    n = 0
    for _name, rx in SECRET_RES:
        def _sub(m):
            nonlocal n
            n += 1
            if m.lastindex:
                return m.group(0).replace(m.group(m.lastindex), "«REDACTED»")
            return "«REDACTED»"
        text = rx.sub(_sub, text)
    return text, n


def best_excerpt(text: str, max_lines: int) -> tuple[str, int]:
    """Pick the most substantive function-like block: the one with the most control flow.

    An excerpt is meant to show what the code actually does, so prefer density of
    branching/async/error-handling over sheer length.
    """
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if DEF_LINE.match(ln)]
    if not starts:
        head = [i for i, ln in enumerate(lines) if ln.strip() and not ln.lstrip().startswith(("#", "//", "/*", "*"))]
        s = head[0] if head else 0
        return "\n".join(lines[s : s + max_lines]), s + 1
    best, best_score = starts[0], -1
    for s in starts:
        block = lines[s : s + max_lines]
        score = len(BRANCHY.findall("\n".join(block))) + sum(1 for b in block if b.strip())
        if score > best_score:
            best, best_score = s, score
    return "\n".join(lines[best : best + max_lines]), best + 1


def extract_excerpts(repo: Path, samples: list[dict], n_files: int, max_lines: int) -> list[dict]:
    """Real code excerpts for the top sample files — secrets redacted before they leave the machine."""
    out = []
    for s in samples[:n_files]:
        f = repo / s["path"]
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        snippet, start = best_excerpt(text, max_lines)
        snippet, nred = redact_secrets(snippet)
        out.append({
            "path": s["path"], "language": s["language"],
            "starts_at_line": start, "lines_shown": len(snippet.splitlines()),
            "file_loc": s["loc"], "secrets_redacted": nred,
            "excerpt": snippet,
        })
    return out


def sample_prs(repo: Path, limit: int, max_diff_lines: int, known: set[str] | None = None) -> list[dict]:
    """1-3 representative merged PRs, chosen to show the NATURE of the work, not the biggest diff.

    Prefers merges that reference an issue and touch tests (a real, reviewed change with a
    verifiable outcome) over dependency bumps and mechanical churn.
    """
    merges = [m.split("\x00") for m in git(repo, "log", "--merges", "--format=%H%x00%s%x00%aI").splitlines() if "\x00" in m]
    scored = []
    for sha, subject, when in merges[:120]:
        try:
            stat = git(repo, "show", "--first-parent", "--numstat", "--format=", sha)
            body = git(repo, "log", "-1", "--format=%B", sha)
        except RuntimeError:
            continue
        files, adds, dels, tests = [], 0, 0, False
        for row in stat.splitlines():
            p = row.split("\t")
            if len(p) != 3:
                continue
            a, d, path = p
            pp = Path(path)
            if any(x in pp.parts for x in VENDOR_DIRS) or pp.suffix.lower() not in CODE_EXT:
                continue
            files.append(path)
            adds += int(a) if a.isdigit() else 0
            dels += int(d) if d.isdigit() else 0
            if TEST_PATH.search(path):
                tests = True
        if not files:
            continue
        linked = bool(ISSUE_REF.search(subject + "\n" + body))
        churn = bool(re.search(r"(?i)\b(bump|dependabot|upgrade deps|lockfile|translation|i18n)\b", subject))
        score = (3 if linked else 0) + (3 if tests else 0) + (2 if 2 <= len(files) <= 12 else 0) - (5 if churn else 0)
        scored.append((score, {
            "subject": subject.strip()[:160], "merged_at": when[:10],
            "files_changed": len(files), "additions": adds, "deletions": dels,
            "touches_tests": tests, "references_issue": linked,
            "files": files[:8], "sha": sha[:10],
        }))
    scored.sort(key=lambda x: -x[0])
    picked = [s[1] for s in scored[:limit]]
    for pr in picked:
        # Restrict the diff to CODE files (and lead with the test file when there is one) — an
        # unrestricted diff often opens on docs/generated files, which hides the actual engineering.
        paths = sorted(pr["files"], key=lambda p: (not TEST_PATH.search(p), p))
        try:
            diff = git(repo, "show", "--first-parent", "--format=", "--unified=2", pr["sha"], "--", *paths)
        except RuntimeError:
            continue
        kept = [ln for ln in diff.splitlines() if not ln.startswith(("index ", "new file", "deleted file", "similarity"))]
        snippet, nred = redact_secrets("\n".join(kept[:max_diff_lines]))
        pr["diff_excerpt"] = snippet
        pr["diff_truncated"] = len(kept) > max_diff_lines
        pr["secrets_redacted"] = nred
    return picked


def pick_samples(repo: Path, limit: int = 6) -> list[dict]:
    """Candidate representative source files to excerpt.

    Selects non-test, non-vendor source files of moderate size, ranked by density of
    functions/classes (a proxy for 'meaty, representative logic'). Returns PATHS and counts
    only; excerpts are extracted separately and redacted before they appear in the report.
    """
    cands = []
    for rel, lang in iter_code_files(repo):
        if TEST_PATH.search(str(rel)):
            continue
        f = repo / rel
        try:
            if not (500 < f.stat().st_size < 60_000):
                continue
            text = f.read_text(errors="replace")
        except OSError:
            continue
        loc = text.count("\n") + 1
        if not (30 <= loc <= 500):
            continue
        fr = FUNC_RE.get(lang)
        funcs = len(fr.findall(text)) if fr else 0
        classes = len(CLASS_RE.findall(text))
        if funcs + classes == 0:
            continue
        cands.append({"path": str(rel), "language": lang, "loc": loc, "functions_est": funcs, "classes_est": classes})
    cands.sort(key=lambda c: (c["functions_est"] + c["classes_est"], c["loc"]), reverse=True)
    return cands[:limit]



def write_markdown(out: Path, result: dict) -> None:
    """Assemble the readable report.md."""
    f, h, e = result["files"], result["history"], result["environment"]
    L = [f"# Repository report — {result['repo_name']}", "",
         f"*Generated {result['generated_at'][:10]} from the local git checkout.*", "",
         "## Highlights", ""]
    L += [f"- {x}" for x in result["highlights"]]
    L += ["", "## Code metrics", "", "| Metric | Value |", "|---|---|",
          f"| Primary language | {f['primary_language']} |",
          f"| Total LoC | {f['total_loc']:,} across {f['files']} code files |",
          f"| Merged PRs / Commits | {h['merged_prs_est']:,} / {h['commits']:,} |",
          f"| PR mix (Simple/Standard/Rich) | {h['pct_simple_prs']}% / {h['pct_standard_prs']}% / {h['pct_rich_prs']}% |",
          f"| Avg / median LoC per PR | {h['avg_loc_per_pr']:,.0f} / {h['median_loc_per_pr']:,} |",
          f"| % PRs referencing an issue | {h['pct_prs_with_issue_ref']}% |",
          f"| Test-to-code / % untested files | {f['test_to_code_ratio_pct']}% / {f['pct_untested_files_heuristic']}% |",
          f"| Functions / Classes (est.) | {f['functions_est']:,} / {f['classes_est']:,} |",
          f"| Contributors | {h['contributors']} |",
          f"| History | {h['first_commit']} → {h['last_commit']} |",
          f"| License | {e['repo_license']} |",
          "",
          ""]
    if result.get("code_excerpts"):
        L += ["## Representative code", ""]
        for c in result["code_excerpts"]:
            L += [f"**`{c['path']}`** — {c['language']}, lines {c['starts_at_line']}–"
                  f"{c['starts_at_line']+c['lines_shown']-1} of {c['file_loc']}"
                  + (f" · {c['secrets_redacted']} value(s) redacted" if c['secrets_redacted'] else ""),
                  "", "```" + (c['language'] or "").lower().split("/")[0], c["excerpt"], "```", ""]
    if result.get("sample_prs"):
        L += ["## Sample pull requests", ""]
        for p_ in result["sample_prs"]:
            flags = []
            if p_.get("references_issue"): flags.append("linked issue")
            if p_.get("touches_tests"): flags.append("touches tests")
            L += [f"**{p_['subject']}**", "",
                  f"- {p_['merged_at']} · {p_['files_changed']} files · +{p_['additions']}/-{p_['deletions']}"
                  + (" · " + ", ".join(flags) if flags else ""),
                  f"- files: " + ", ".join(f"`{x}`" for x in p_["files"][:5]), ""]
            if p_.get("diff_excerpt"):
                L += ["```diff", p_["diff_excerpt"],
                      "```" if not p_.get("diff_truncated") else "\n… diff truncated …\n```", ""]
    cf = result["cleanup_flags"]
    sec = cf["secret_pattern_hits"]
    L += ["## Cleanup before sharing", ""]
    if sec or cf["files_with_pii_keywords"]:
        kinds = ", ".join(f"{k.replace('_',' ')} ×{v}" for k, v in sec.items())
        L += [f"**Action required.** Secret-pattern hits: {kinds or 'none'}; "
              f"PII-keyword files: {cf['files_with_pii_keywords']}. Remove from the working tree "
              "AND git history, and rotate any live key before transfer.", ""]
    else:
        L += ["**Clean.** No secret patterns or PII keywords detected in tracked files.", ""]
    L += ["*Contains real code excerpts and diff hunks (secret-shaped values redacted). "
          "% Rich PRs is a lower bound — review threads live on the code host, not in git. "
          "Function and class counts are regex estimates.*"]
    (out / "report.md").write_text("\n".join(L))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="path to a local git repository")
    ap.add_argument("--out", default="./report")
    ap.add_argument("--merge-sample", type=int, default=200)
    ap.add_argument("--no-excerpts", action="store_true",
                    help="metrics only — omit code excerpts and PR diffs")
    ap.add_argument("--excerpt-files", type=int, default=3, help="how many files to excerpt")
    ap.add_argument("--excerpt-lines", type=int, default=40, help="max lines per code excerpt")
    ap.add_argument("--sample-prs", type=int, default=3, help="how many representative PRs to include")
    ap.add_argument("--diff-lines", type=int, default=60, help="max diff lines per sample PR")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        sys.exit(f"not a git repository: {repo}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] scanning files in {repo.name} ...", file=sys.stderr)
    files = analyze_files(repo)
    print(f"[2/4] analyzing history ({args.merge_sample}-merge sample) ...", file=sys.stderr)
    hist = analyze_history(repo, args.merge_sample)
    print("[3/4] environment, license & sample selection ...", file=sys.stderr)
    env = analyze_environment(repo)
    samples = pick_samples(repo)
    excerpts, prs = [], []
    if not args.no_excerpts:
        print("[4/5] extracting code excerpts + sample PRs ...", file=sys.stderr)
        excerpts = extract_excerpts(repo, samples, args.excerpt_files, args.excerpt_lines)
        prs = sample_prs(repo, args.sample_prs, args.diff_lines)
    print("[5/5] writing report ...", file=sys.stderr)

    if args.no_excerpts:
        note = ("Metrics only (--no-excerpts): no source code or diffs included.")
    else:
        note = ("Metrics, highlights, code excerpts and sample PRs. CONTAINS REAL CODE (excerpts and "
                "diff hunks) with secret-shaped values redacted \u2014 review before sharing, or re-run "
                "with --no-excerpts for a metrics-only report.")
    result = {
        "repo_name": repo.name,
        "generated_at": datetime.now(timezone.utc).isoformat()[:19] + "Z",
        "note": note,
        "contains_code": bool(excerpts or prs),
        "highlights": build_highlights(files, hist, env),
        "files": files,
        "history": hist,
        "environment": env,
        "representative_sample_candidates": samples,
        "code_excerpts": excerpts,
        "sample_prs": prs,
        "cleanup_flags": {
            "secret_pattern_hits": files["secret_pattern_hits"],
            "files_with_pii_keywords": files["files_with_pii_keywords"],
            "action": "Remove secrets and any customer PII from the working tree AND git history before sharing.",
        },
    }
    (out / "repo_report.json").write_text(json.dumps(result, indent=2))
    write_markdown(out, result)

    row = {
        "repo": repo.name,
        "primary_language": files["primary_language"],
        "total_loc": files["total_loc"],
        "code_files": files["files"],
        "merged_prs": hist["merged_prs_est"],
        "commits": hist["commits"],
        "contributors": hist["contributors"],
        "avg_loc_per_pr": hist["avg_loc_per_pr"],
        "median_loc_per_pr": hist["median_loc_per_pr"],
        "pct_simple_prs": hist["pct_simple_prs"],
        "pct_standard_prs": hist["pct_standard_prs"],
        "pct_rich_prs": hist["pct_rich_prs"],
        "pct_prs_with_issue_ref": hist["pct_prs_with_issue_ref"],
        "functions_est": files["functions_est"],
        "classes_est": files["classes_est"],
        "test_to_code_ratio_pct": files["test_to_code_ratio_pct"],
        "pct_untested_files": files["pct_untested_files_heuristic"],
        "comment_ratio_pct": files["comment_ratio_pct"],
        "license": env["repo_license"],
        "first_commit": hist["first_commit"],
        "last_commit": hist["last_commit"],
        "sample_files": "; ".join(s["path"] for s in samples[:3]),
    }
    with (out / "metrics.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)

    flagged = sum(files["secret_pattern_hits"].values()) + files["files_with_pii_keywords"]
    print(json.dumps({"highlights": len(result["highlights"]), "sample_candidates": len(samples), "cleanup_flags": flagged, "out": str(out)}, indent=2))


if __name__ == "__main__":
    main()
