Review the changes from all perspectives: code quality, correctness, and design.

Check for the following:

**Code Quality:**
- Readability and naming clarity
- Function size and complexity
- DRY violations and code duplication
- Dead code and unused imports

**Correctness:**
- Edge case handling
- Error handling patterns
- Type safety
- Test coverage for new/changed code

**Design:**
- Appropriate abstraction levels
- Module decomposition and responsibility boundaries
- Consistency with existing codebase patterns

**Design decisions reference:**
Review {report:coder-decisions.md} to understand the recorded design decisions.
- Do not flag intentionally documented decisions as FP
- However, also evaluate whether the design decisions themselves are sound

## Judgment Procedure

1. Review the change diff and plan report
2. Verify the implementation matches the plan
3. Detect issues based on the criteria above
4. For each issue, classify as blocking/non-blocking:
   - **Blocking**: Bugs, missing error handling, design violations, missing tests
   - **Non-blocking**: Style preferences, minor naming suggestions
5. If there is even one blocking issue, judge as needs changes

## Output

For each finding:
- File and line number
- What the issue is
- How to fix it
- Severity: blocking / non-blocking

Final verdict: "Implementation is acceptable" or "Changes needed"
