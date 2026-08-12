# repo-* skills

Three self-contained skills for analysing a git repository. Each is a `SKILL.md` (instructions an
agent reads) plus a stdlib-only Python script you can run directly.

| Skill | What it does |
|---|---|
| **repo-report** | Measures the codebase — LoC, language mix, merged PRs and complexity tiers, test-to-code ratio, untested files, functions/classes, issue references, CI/reproducibility signals, license — and pulls representative material: real code excerpts and up to 3 sample PRs with diffs. Flags secrets and PII. Writes `repo_report.json`, `metrics.csv`, `report.md`. |
| **repo-verify** | Confirms a fix PR contains a genuine regression test, by running it: checks out the pre-PR commit in a throwaway worktree, applies only the PR's tests (expects FAIL), then the source fix (expects PASS). Reports `verified: true/false` **with the reason**. |
| **repo-scrub** | Finds leaked secrets and env keys (tracked files and git history), a missing LICENSE, and bot/unclear authorship. `--fix` untracks `.env`, writes a blanked `.env.example`, scaffolds a LICENSE. Never rewrites history — prints the command instead. |

## Quick start

```bash
# scan for secrets and licence problems
python3 repo-scrub/scripts/scrub_repo.py /path/to/repo

# measure the repository
python3 repo-report/scripts/repo_report.py /path/to/repo --out ./report

# confirm one fix PR has a real fail→pass test
python3 repo-verify/scripts/verify_env.py /path/to/repo --pr 1234
```

Python 3.9+ and `git`. `repo-verify` also needs the repo's test runner (pytest / vitest / jest /
go test) and network access to install dependencies.

## Design

- **A candidate is never called verified.** `repo-verify` must observe FAIL then PASS. A build
  that won't reproduce is a legitimate `verified: false`, recorded with its reason.
- **Measurement is separate from judgement.** `repo-report` has no scoring, so running it on your
  own code doesn't hand you a verdict about it.
- **Limits are printed, not buried.** `% Rich PRs` is a lower bound (review threads aren't in
  git); function and class counts are regex estimates. Both say so in the output.

## What leaves your machine

Nothing — everything runs locally. But a **report** contains real code by default (excerpts and PR
diffs). Secret-shaped values are redacted and the JSON is marked `contains_code: true`; read the
report before sharing it, or use `--no-excerpts`.
