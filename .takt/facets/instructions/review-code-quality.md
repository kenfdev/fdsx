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

## Judgment Procedure

1. Review the change diff and detect issues based on the code quality criteria above
2. For each detected issue, classify as blocking/non-blocking:
   - **Blocking**: Bugs, DRY violations creating maintenance risk, unhandled error cases, dead code
   - **Non-blocking**: Style preferences, minor naming suggestions
3. If there is even one blocking issue, judge as REJECT

## Output

For each finding:
- File and line number
- What the issue is
- How to fix it
- Severity: blocking / non-blocking

Final verdict: `approved` or `needs_fix`
