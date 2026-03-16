Finalize the implementation and create a pull request.

**Steps:**
1. Verify the current state:
   - Run `git status` to see all changes
   - Run the build/type check to confirm no errors
   - Run tests to confirm all pass
2. Stage and commit changes:
   - Create a clean, descriptive commit (or squash if multiple WIP commits exist)
   - Commit message should summarize what was implemented and why
3. Push the branch and create a PR:
   - Push to remote with `git push -u origin <branch>`
   - Create a PR using `gh pr create` with:
     - A clear title summarizing the change
     - A body describing what was done, why, and how to test

**If any step fails:**
- Build/test failures: report the error and output `[STEP:2]` (Error during finalization)
- PR creation issues: report the error and output `[STEP:2]` (Error during finalization)

**On success:**
- Output `[STEP:1]` (PR created successfully)
- Include the PR URL in your output
