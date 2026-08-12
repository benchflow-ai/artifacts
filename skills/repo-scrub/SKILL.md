---
name: repo-scrub
description: "Scan a git repository for leaked secrets and env keys, a missing LICENSE, and unclear authorship (bot or non-employee committers), then optionally fix the safe parts. Reports secret matches with values redacted, .env files tracked or untracked, secrets found in git history, license presence, and committer domains. With --fix it untracks .env, adds it to .gitignore, writes a values-blanked .env.example, and scaffolds a LICENSE; --redact-code also replaces secrets found in tracked source. Never rewrites git history automatically — it prints the command instead. Use before publishing, open-sourcing, transferring, or sharing a repository, or when asked to find leaked keys, add a license, or check IP hygiene. Trigger phrases: 'scan for secrets', 'is there an API key in this repo', 'check before open-sourcing', 'add a license', 'clean this repo before sharing'."
---

# Repo scrub

Find what shouldn't leave the repository, and fix the safe parts. Catches the problems that most
often surface too late: a key committed to `.env`, no LICENSE, and unclear authorship.

**Two passes.** Run it on your own repository *before* publishing or transferring it — it is
offline (git + regex, no network). Run it again on any repository you receive, since you can't
verify someone else's scrub.

## Workflow

1. **Scan first** (report only):
   ```bash
   python3 scripts/scrub_repo.py /path/to/repo
   ```
   Reports secrets in tracked files (redacted, with file:line), `.env` files, git-history leak
   hits, LICENSE presence, and bot/suspect committers.

2. **Rotate any leaked key first** — a human step the tool can't do. Assume anything found is
   compromised and rotate it at the provider before touching the repository.

3. **Apply the safe fixes**:
   ```bash
   python3 scripts/scrub_repo.py /path/to/repo --fix --license MIT --owner "Your Name"
   ```
   Untracks `.env`, adds `.env`/`.env.*` to `.gitignore`, writes a values-blanked `.env.example`,
   and scaffolds a LICENSE if missing. Add `--redact-code` to also replace secrets found in
   tracked source with `REDACTED_SECRET` in the working tree (review the diff before committing).

4. **History leaks are reported, never auto-rewritten** — that's destructive and needs a
   force-push. The scan prints the exact `git filter-repo` command; run it deliberately, after
   rotating the key.

5. **Authorship** — for bot or non-employee committers (CI bots, `test@example.com`, agent
   accounts), confirm the code's provenance is what you think it is.

## Safety

- Never commits secrets, never force-pushes, never rewrites history on its own.
- `--fix` only untracks/ignores `.env`, writes `.env.example`, and adds a LICENSE.
  `--redact-code` edits the working tree only — nothing is committed.
- License scaffolds are stubs (the proprietary one is complete; MIT/Apache are pointers) — confirm
  the choice yourself; picking a license is not the tool's call.
