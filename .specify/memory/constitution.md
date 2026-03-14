<!--
Sync Impact Report
===================
Version change: N/A → 1.0.0
Added sections:
  - Preamble
  - Principle 1: Declarative Simplicity
  - Principle 2: CLI-Native Execution
  - Principle 3: Stateful Correctness
  - Principle 4: Modular Extensibility
  - Principle 5: Security by Design
  - Principle 6: Test Trophy Discipline
  - Principle 7: Minimal Dependencies
  - Anti-Patterns (Prohibited Practices)
  - Governance
Removed sections: (none — initial version)
Templates requiring updates:
  - .specify/templates/plan-template.md ⚠ pending (does not exist yet)
  - .specify/templates/spec-template.md ⚠ pending (does not exist yet)
  - .specify/templates/tasks-template.md ⚠ pending (does not exist yet)
Follow-up TODOs: none
-->

# fdsx Project Constitution

**Version**: 1.0.0
**Ratification Date**: 2026-03-15
**Last Amended**: 2026-03-15

## Preamble

fdsx (Flow-Driven Stateful eXecution) is a lightweight framework
that enables declarative YAML-based AI agent workflow orchestration.
This constitution defines the non-negotiable principles, quality
standards, and governance rules that all contributors and automated
agents MUST follow when developing, reviewing, or extending fdsx.

The priority order when trade-offs arise:
**Correctness > Developer Experience > Performance**

---

## Principle 1: Declarative Simplicity

Users MUST be able to define complete multi-agent workflows using
only YAML — no programming language knowledge required for basic
use cases.

- The YAML schema MUST remain intuitive and self-documenting.
- Every state type (Task, Choice, Parallel, Wait, Pass) MUST be
  expressible in YAML without escape hatches to code.
- Error messages from YAML parsing MUST clearly indicate what is
  wrong and where, referencing line numbers when possible.

**Rationale**: fdsx exists because writing Python graph code is a
barrier. If YAML authoring becomes equally complex, the project
has failed its core mission.

## Principle 2: CLI-Native Execution

fdsx MUST execute LLM tasks by invoking CLI tools (claude, opencode,
codex, etc.) as subprocesses. fdsx MUST NEVER require users to
provide LLM API keys directly.

- All LLM interaction MUST go through subprocess CLI calls.
- Authentication MUST be delegated entirely to the underlying
  CLI tools.
- Adding a new provider MUST only require specifying the CLI
  command and its argument format — not an SDK integration.

**Rationale**: CLI-native execution eliminates API key management,
respects existing tool authentication flows, and avoids SDK vendor
lock-in. Users already have CLI tools configured; fdsx orchestrates
them, not replaces them.

## Principle 3: Stateful Correctness

State transitions, checkpoints, and variable mutations MUST be
deterministic, explicit, and recoverable. No silent failures.
No lost state.

- Every state mutation MUST be traceable through execution logs.
- Checkpoint persistence MUST guarantee that a workflow can resume
  from the last successful state after interruption.
- State variable writes MUST use explicit `resultPath` declarations
  — implicit or ambient state mutation is prohibited.
- All errors MUST surface to the user with actionable context
  (state name, transition, input that caused the failure).

**Rationale**: Workflows involving multiple LLM agents are
inherently non-deterministic in their outputs. The framework
itself MUST be the reliable foundation — if users cannot trust
state management, the entire system is unusable.

## Principle 4: Modular Extensibility

The codebase MUST maintain clean module boundaries with a
plugin-friendly architecture for providers, state types, and
checkpoint backends.

- Each component (YAML loader, state machine engine, provider
  interface, checkpoint backend) MUST be independently testable.
- Adding a new provider or state type MUST NOT require modifying
  core engine code — use well-defined extension points.
- Module interfaces MUST be documented with type annotations.

**Rationale**: fdsx targets a diverse ecosystem of CLI tools and
LLM providers. A monolithic design would make provider additions
painful and create coupling that slows development.

## Principle 5: Security by Design

All external execution MUST be isolated and all user-supplied
input MUST be validated before processing.

- CLI subprocesses MUST run in isolated environments (git worktrees
  or equivalent sandboxing) when executing untrusted workflows.
- YAML inputs MUST be validated against a strict schema before
  execution — reject unknown keys, invalid types, and malformed
  references.
- Prompt templates MUST sanitize variable interpolation to prevent
  injection of unintended commands or prompt fragments.
- fdsx MUST NEVER execute arbitrary shell commands from YAML
  without explicit user opt-in (e.g., `allowSystemCommands: true`).

**Rationale**: fdsx orchestrates subprocess execution — a prime
vector for injection attacks. Defense in depth (validation +
isolation + explicit opt-in) is required to prevent malicious
or malformed YAML from causing damage.

## Principle 6: Test Trophy Discipline

Development MUST follow TDD (test-driven development). Tests MUST
provide genuine value — coverage percentage is not a goal in itself.

- Follow the test trophy model: integration tests form the
  largest and most valuable layer.
- Every new feature MUST have tests written BEFORE implementation.
- Unit tests MUST focus on pure logic (YAML parsing, state
  transition rules, variable resolution).
- Integration tests MUST verify end-to-end workflow execution
  including subprocess invocation and checkpoint recovery.
- Tests that assert implementation details rather than behavior
  MUST be rejected in review.
- Python testing best practices MUST be followed: use pytest,
  fixtures, parametrize for variant coverage, and clear test
  naming (`test_<scenario>_<expected_outcome>`).

**Rationale**: Vanity coverage metrics create false confidence.
The test trophy (integration-heavy, minimal mocks) catches real
bugs — the kind that surface when YAML, state machine, and
subprocesses interact. TDD ensures testability is designed in,
not bolted on.

## Principle 7: Minimal Dependencies

fdsx MUST minimize third-party dependencies. Only essential
libraries are permitted.

- Approved core dependencies: langgraph, pyyaml, typer.
- Adding any new dependency MUST be justified with a clear
  rationale explaining why the functionality cannot be achieved
  with the standard library or existing dependencies.
- Dependencies MUST be actively maintained (commit activity
  within the last 6 months, no unresolved critical CVEs).
- Transitive dependency count MUST be considered — prefer
  libraries with fewer transitive dependencies.

**Rationale**: Every dependency is a liability — supply chain
risk, version conflicts, maintenance burden. fdsx is a lightweight
framework; its dependency footprint MUST reflect that.

---

## Anti-Patterns (Prohibited Practices)

The following practices are explicitly prohibited:

1. **API Key Coupling**: fdsx MUST NEVER accept, store, or
   transmit LLM API keys. Authentication is the CLI tool's
   responsibility.

2. **God Classes**: No single class or module may accumulate
   responsibilities across multiple architectural boundaries
   (e.g., parsing + execution + persistence in one file).

3. **Implicit State Mutation**: State changes that are not
   declared in `resultPath` or equivalent explicit mechanisms
   are prohibited.

4. **Test-Free Features**: No feature may be merged without
   corresponding tests written via TDD.

5. **Unnecessary Abstraction**: Do not create abstractions for
   single-use patterns. Three similar lines of code are better
   than a premature helper function.

---

## Governance

### Amendment Procedure

- **Authority**: kenfdev is the sole maintainer and
  decision-maker for constitution amendments.
- **Process**: Amendments are made at the maintainer's discretion
  and committed directly to the repository.
- **Documentation**: Every amendment MUST update the version
  number, last amended date, and sync impact report.

### Versioning Policy

The constitution follows semantic versioning:

- **MAJOR** (X.0.0): Removal or redefinition of existing
  principles. Backward-incompatible governance changes.
- **MINOR** (x.Y.0): New principles added, existing principles
  materially expanded, new sections introduced.
- **PATCH** (x.y.Z): Clarifications, wording improvements, typo
  fixes, non-semantic refinements.

### Compliance Review

- All code contributions MUST be reviewable against these
  principles.
- Automated agents (Claude Code, Cursor, etc.) operating on this
  codebase MUST adhere to this constitution.
- When a principle conflicts with a practical constraint, the
  maintainer makes the final call and documents the exception.
