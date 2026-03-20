Review the changes from a security perspective. Check for the following vulnerabilities:
- Injection attacks (SQL, command, XSS)
- Authentication and authorization flaws
- Data exposure risks
- Cryptographic weaknesses


**Design decisions reference:**
Review {report:coder-decisions.md} to understand the recorded design decisions.
- Do not flag intentionally documented decisions as FP
- However, also evaluate whether the design decisions themselves are sound, and flag any problems

**Scope boundary — plan report:**
Review {report:plan.md} for the **Out of Scope** section.
- Vulnerabilities in files or areas listed as out of scope MUST NOT be flagged as blocking
- Even if an out-of-scope area has a real vulnerability, it is deferred to a later phase by design
- Only review code within the plan's stated scope (files listed in File Change Summary)

## Judgment Procedure

1. Review the change diff and detect issues based on the security criteria above
   - Cross-check changes against REJECT criteria tables defined in knowledge
2. Cross-check each finding against the plan's Out of Scope table — discard any finding that falls in deferred scope
3. For each remaining issue, classify as blocking/non-blocking based on Policy's scope determination table and judgment rules
4. If there is even one blocking issue, judge as REJECT
