# Metrics reference

How each field in `repo_report.json` is computed, and what it does and doesn't tell you.

## Files (`files`)

| Field | Meaning |
|---|---|
| `total_loc`, `files` | Lines and file count across tracked source files. Vendored directories (`node_modules`, `vendor`, `dist`, `.venv`, …), minified files, and files over 2 MB are excluded. |
| `languages_by_loc`, `primary_language` | Line counts per language, by file extension. |
| `code_loc` / `comment_loc` / `blank_loc` | Line classification. Comments are detected by leading line-comment prefix only — block comments count as code. |
| `comment_ratio_pct` | `comment / (code + comment)`. |
| `test_files`, `test_loc`, `test_to_code_ratio_pct` | Test files are matched by path convention (`tests/`, `spec/`, `__tests__/`, `*_test.go`, `test_*.py`, `*.spec.ts`, …). The ratio is test lines over non-test lines, so it can exceed 100%. |
| `pct_untested_files_heuristic` | Share of source files whose basename never appears in any test filename. A **crude** proxy — it does not read imports or run coverage. |
| `functions_est`, `classes_est` | Regex counts per language. **Estimates**: they miss unusual formatting and over-count some patterns. |
| `secret_pattern_hits`, `files_with_pii_keywords` | Counts only — values are never recorded. See `cleanup_flags`. |

## History (`history`)

| Field | Meaning |
|---|---|
| `commits`, `contributors` | From `git rev-list` and `git shortlog -sne`. Contributors counts distinct author identities, so one person with two emails counts twice. |
| `first_commit`, `last_commit` | Root commit date and most recent commit date. |
| `commits_last_365d` | Activity/freshness signal. |
| `merged_prs_est` | Merge-commit count. Repositories that squash-merge or rebase will report **0** even with a busy PR history. |
| `avg_loc_per_pr`, `median_loc_per_pr` | Added + deleted lines across code files per merge. Prefer the median — one bulk merge skews the mean badly. |
| `pct_prs_with_issue_ref` | Share of sampled merges whose subject or body references an issue (`#123`, `PROJ-456`, `fixes #789`). |

### PR complexity tiers

Sampled merges are bucketed to describe the shape of the change history:

- **Simple** — ≤2 code files and no issue reference. Config edits, dependency bumps, typo fixes.
- **Standard** — a moderate change, usually 3–10 files, often touching tests.
- **Rich** — an issue reference plus either test changes or a multi-file change.

`% Rich` is a **lower bound**: PR review discussion lives on the code host, not in git, so a
change that was extensively reviewed looks the same locally as one that was merged unread.

## Environment (`environment`)

CI config files, reproducibility files (Dockerfile, compose, devcontainer, nix), lockfiles, and a
license class (`permissive` / `copyleft` / `custom-or-unclear` / `none-found`) detected from the
LICENSE file by name and by canonical licence phrasing.

## Samples

- `code_excerpts` — ~40 lines from each of the 3 most substantive files, chosen by control-flow
  density (branching, error handling, async) so the excerpt shows logic rather than boilerplate.
- `sample_prs` — up to 3 merges ranked to show the nature of the work: an issue reference and test
  changes score highest; dependency and translation churn is penalised. Diffs are restricted to
  code files (tests first) so they don't open on generated files or documentation.

Both are passed through the secret redactor before they appear in the report.
