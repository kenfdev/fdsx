Review the changes from a code quality perspective. Check for the following:
- Code readability and naming clarity
- Function/method size and complexity (single responsibility)
- DRY violations and code duplication
- Appropriate error handling
- Dead code, unused imports, and unnecessary abstractions
- Consistency with existing codebase patterns

**Design decisions reference:**
Review {report:coder-decisions.md} to understand the recorded design decisions.
- Do not flag intentionally documented decisions as FP
- However, also evaluate whether the design decisions themselves are sound, and flag any problems

**Scope boundary — plan report:**
Review {report:plan.md} for the **Out of Scope** section.
- Issues in files or areas listed as out of scope MUST NOT be flagged as blocking
- Even if an out-of-scope area has a real issue, it is deferred to a later phase by design
- Only review code within the plan's stated scope (files listed in File Change Summary)

## Judgment Procedure

1. Review the change diff and detect issues based on the code quality criteria above
2. Cross-check each finding against the plan's Out of Scope table — discard any finding that falls in deferred scope
3. For each remaining issue, classify as blocking/non-blocking:
   - **Blocking**: Bugs, DRY violations creating maintenance risk, unhandled error cases, dead code
   - **Non-blocking**: Style preferences, minor naming suggestions
4. If there is even one blocking issue, judge as REJECT

## Output

For each finding:
- File and line number
- What the issue is
- How to fix it
- Severity: blocking / non-blocking

Final verdict: `approved` or `needs_fix`
