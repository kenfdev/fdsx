Finalize the implementation: commit all changes, push the branch, and create a pull request.

You MUST complete every step below in order. Do NOT skip any step. Do NOT output `[STEP:1]` until all three gates pass.

---

## Gate 1: Verify & Commit

1. Run `git status` to see all changes (staged, unstaged, untracked)
2. Run the build/type check — if it fails, output `[STEP:2]` immediately
3. Run the test suite — if it fails, output `[STEP:2]` immediately
4. Stage all relevant changes with `git add`
5. Create a commit with a clear, descriptive message summarizing what was implemented and why
6. Confirm the commit succeeded with `git log -1 --oneline`

## Gate 2: Push

1. Push the branch to the remote: `git push -u origin HEAD`
2. Confirm the push succeeded (exit code 0)
3. If push fails (e.g., no remote, auth error), output `[STEP:2]`

## Gate 3: Create Pull Request

1. Create a PR using `gh pr create` with:
   - A clear title summarizing the change
   - A body with: what was done, why, and how to test
2. Confirm the PR was created and capture the PR URL
3. If PR creation fails, output `[STEP:2]`

---

**On success (all 3 gates passed):**
- Output the PR URL
- Output `[STEP:1]` (PR created successfully)

**On any failure:**
- Report which gate failed and the error details
- Output `[STEP:2]` (Error during finalization)
