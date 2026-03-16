Review the changes from a performance perspective. Check for the following:
- Algorithmic complexity (unnecessary O(n^2) or worse)
- N+1 query patterns
- Unnecessary sequential operations that could be parallelized
- Large allocations in hot paths
- Missing caching opportunities
- Resource leaks (connections, file handles, streams)
- Inefficient data structure choices

**Design decisions reference:**
Review {report:coder-decisions.md} to understand the recorded design decisions.
- Do not flag intentionally documented decisions as FP
- However, also evaluate whether the design decisions themselves are sound from a performance perspective

## Judgment Procedure

1. Review the change diff and detect performance issues
2. For each detected issue, classify as blocking/non-blocking:
   - **Blocking**: N+1 queries, O(n^2) in hot paths, resource leaks, unbounded allocations
   - **Non-blocking**: Minor optimization opportunities, micro-optimizations
3. If there is even one blocking issue, judge as REJECT

## Output

For each finding:
- File and line number
- What the performance impact is
- How to fix it
- Severity: blocking / non-blocking

Final verdict: `approved` or `needs_fix`
