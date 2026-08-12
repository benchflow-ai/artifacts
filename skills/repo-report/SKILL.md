---
name: repo-report
description: "Generate a metrics report and a shareable deliverable for a git repository: lines of code, language mix, merged PRs and their complexity tiers, test-to-code ratio, untested files, functions/classes, commits, contributors, issue references, CI and reproducibility signals, and license class — plus charts over time (commits, merged PRs and active contributors per month), representative code excerpts, up to 3 sample PRs with diffs, and flags for secrets or PII. Runs offline on a local checkout and packages a PDF, an Excel workbook, JSON, CSV and SVG charts into a single zip. Use when asked to profile, summarise, or document a codebase, produce repo statistics, assess test coverage signals, or pull representative code and PR samples. Trigger phrases: 'generate a repo report', 'analyse this codebase', 'repo stats', 'summarise this repository', 'what does this codebase look like', 'pull sample code and PRs'."
---

# Repo report

Profile a git repository: metrics, representative code, sample pull requests, and hygiene flags.
It measures and describes — it does not score, rank, or grade.

## Workflow

1. Confirm a local git checkout. Without git history most metrics are unavailable — say so and
   still run the file scan.

2. Run (stdlib only, no packages):
   ```bash
   python3 scripts/repo_report.py /path/to/repo --out ./report
   ```
   Produces a deliverable zip, `<repo>-report.zip`, containing:

   | File | What |
   |---|---|
   | `report.pdf` | the readable report, with the charts |
   | `metrics.xlsx` | 6 sheets: Summary, Timeline, Languages, Sample PRs, Sample files, Hygiene |
   | `repo_report.json` | everything, machine-readable |
   | `metrics.csv` | one flat row |
   | `report.md` / `report.html` | same report, other formats |
   | `chart-*.svg` | the charts standalone |

   PDF rendering uses whichever engine is installed (Chrome/Chromium/Edge headless, weasyprint,
   or wkhtmltopdf). If none is found it says so and writes `report.html` — open and print it.
   `--no-bundle` writes only JSON + CSV + markdown. Lower `--merge-sample` for a fast pass on a
   very large history.

3. Read `repo_report.json`:
   - `highlights` — the headline facts, ready to quote.
   - `files` / `history` / `environment` — the full metric set. See
     [references/metrics.md](references/metrics.md) for how each is computed.
   - `code_excerpts` — real code, ~40 lines from each of the 3 most substantive files, selected by
     control-flow density so the excerpt shows logic rather than boilerplate.
   - `sample_prs` — up to 3 merged PRs with stats, `touches_tests`, `references_issue`, and a
     truncated `diff_excerpt`. Ranked to show the nature of the work: issue-linked and
     test-touching rank highest; dependency bumps and translation churn are penalised.
   - `timeline_monthly` — commits, merged PRs and distinct authors per month, the series behind
     the activity charts.
   - `cleanup_flags` — **surface these first.** Secrets or PII must be removed from the working
     tree *and* git history before the repo or report is shared.

4. **Review the code before the report leaves the machine.** Excerpts are auto-redacted for
   secret-shaped values, but redaction is not judgement: read each excerpt and diff and confirm
   there is nothing private in them. Swap samples with `--excerpt-files` / `--sample-prs`, or drop
   code entirely with `--no-excerpts`.

## Honesty rules

- Report the metrics as measured; do not editorialise them into a verdict.
- **`% Rich PRs` is a lower bound** — review threads live on the code host, not in git — so say so
  rather than implying the local number is the ceiling.
- Function and class counts are regex **estimates**; label them as such.
- Always surface secret/PII flags. A leaked key or a customer record in a shared repo is a serious
  problem, and catching it first is the point.
