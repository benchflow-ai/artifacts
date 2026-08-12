# artifacts

Tools for analysing a git repository: measure it, confirm its fixes are covered by real regression
tests, and check it's safe to share. Stdlib Python + `git`. No install.

```bash
git clone https://github.com/benchflow-ai/artifacts.git
```

## [`skills/`](skills)

| Skill | Answers | Run |
|---|---|---|
| [repo-report](skills/repo-report) | What is this codebase, in numbers? | `python3 skills/repo-report/scripts/repo_report.py REPO --out ./report` |
| [repo-verify](skills/repo-verify) | Does this fix PR have a real regression test? | `python3 skills/repo-verify/scripts/verify_env.py REPO --pr 1234` |
| [repo-scrub](skills/repo-scrub) | Is it safe to share? | `python3 skills/repo-scrub/scripts/scrub_repo.py REPO [--fix]` |

Each is a `SKILL.md` for agents plus a script you can run directly.

**repo-report** → a deliverable zip: **`report.pdf`** (with charts), **`metrics.xlsx`** (6 sheets),
plus JSON, CSV, markdown and standalone SVGs. Measures LoC, language mix, merged PRs and complexity
tiers, test-to-code ratio, issue references, functions/classes, CI signals and license; charts
commits, merged PRs and active contributors per month; and pulls code excerpts and sample PRs with
diffs. `--no-bundle` for JSON + CSV + markdown only.

**repo-verify** → checks out the pre-PR commit, applies only the PR's tests (expects FAIL), then
the fix (expects PASS):

```json
{ "runner": "pytest", "base_with_tests_rc": 1, "gold_rc": 0,
  "verified": true, "reason": "real fail->pass" }
```

Anything else is `verified: false` with the reason. A build that won't reproduce is a legitimate
false, not something to hide.

**repo-scrub** → leaked keys (tracked files + history), missing LICENSE, bot/unclear authorship.
`--fix` untracks `.env`, writes a blanked `.env.example`, scaffolds a LICENSE. Never rewrites
history — it prints the command instead.

## Caveats

- Reports **contain real code** by default (excerpts + PR diffs). Secret-shaped values are
  redacted; a customer name in a comment is not. Read before sharing, or use `--no-excerpts`.
- `% Rich PRs` is a lower bound — review threads live on the code host, not in git.
- `merged_prs` counts merge commits, so squash-merge repos report 0.
- Function and class counts are regex estimates.
- Nothing is uploaded. `repo-verify` needs network to install deps; the others are offline.
- PDF rendering uses any installed Chrome/Chromium/Edge, weasyprint or wkhtmltopdf. With none
  installed you still get `report.html` — open and print it.

Python 3.9+, `git`. [AGPL-3.0](LICENSE).
