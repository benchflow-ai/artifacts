---
name: repo-verify
description: "Execute a base→gold fail→pass check on a fix pull request to confirm it contains a genuine regression test. Checks out the pre-PR commit in a throwaway git worktree, installs dependencies, applies only the PR's test changes (expects FAIL), then applies the source fix (expects PASS). A real FAIL→PASS transition returns verified:true; a build failure, an already-passing test, or a suite needing a live server/database returns verified:false with the reason. Use to confirm a PR's tests actually reproduce the bug it fixes, check whether a fix is covered by a regression test, or validate that a repository's fixes are reproducible. Trigger phrases: 'does this PR have a real regression test', 'verify this fix', 'run the fail to pass', 'check if this PR's tests reproduce the bug'."
---

# Repo verify

Confirm — by running it — that a fix PR contains a test that genuinely fails before the fix and
passes after. Static signals like "the PR touches a test file" don't establish that; only
executing it does. Runs in a throwaway `git worktree`, so the working checkout is never touched.

## Workflow

1. Pick a fix PR (or base/head SHAs):
   ```bash
   python3 scripts/verify_env.py /path/to/repo --pr 22
   # or: --base <sha> --head <sha>
   ```

2. Read the verdict:
   - `verified: true`, `reason: "real fail→pass"` — the PR's tests reproduce the bug and the fix
     resolves them.
   - `verified: false` — the reason says why: the PR changes no tests; the tests already pass at
     base (so nothing was reproduced); they still fail after the fix (build or environment
     problem); or the suite is e2e/database-backed and can't run hermetically.
   - a `warning` about e2e/integration means reproduction likely needs a live server or database.

3. To sweep a repository, loop over its fix PRs and keep the ones returning `verified: true`.

## Limits

- A build that won't reproduce is a legitimate `verified: false` — record it rather than retrying
  until it passes.
- The harness runs the PR's **own** tests. If the test was written alongside the fix, it shows the
  fix works, not that the bug pre-existed independently.
- Dependency install is best-effort and timeboxed; `verified: false` with an install error means
  the repo needs a proper build environment, not that the fix is bad.
- It runs only the PR's changed test files, not the full suite — so it checks the fail→pass
  precondition, not that the rest of the suite still passes.
