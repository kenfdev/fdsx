# Live Jev local-loop experiment

Run from this checkout's root after exporting `TYPESAFE_API_KEY` in your terminal:

```sh
uv run fdsx validate examples/jev-local-smoke/workflow.yaml
uv run fdsx run examples/jev-local-smoke/workflow.yaml
```

Do not put the key in this directory or paste it into logs. The validate command
checks the definition locally; it does not verify your credentials or call Jev.
The run command contacts `https://api.typesafe.ai` and may incur API charges.
This experiment has no shell tasks, AI CLI calls, or hooks of its own. Your normal
fdsx project/user configuration still applies, including any inherited hooks.

## What it exercises

1. A map processes two items, each with its own local workflow.
2. Each workflow submits the deliberately wrong equation `2 + 2 = 5` to Jev.
3. A `choice` sends a `fix` decision to a deterministic local repair step.
4. Repair changes the equation to `2 + 2 = 4`, then calls Jev again.
5. An `approved` decision finishes that local workflow.
6. After both map items succeed, two parallel branches run the same experiment.

No generative AI is needed for repair. This isolates the new local routing and
real Jev connection from the behavior of another model. The map items deliberately
run the same content; this is a plumbing check, not an evaluation-quality benchmark.
Only the equation is supplied as evaluation material.

Expected: **8 successful evaluation requests**, two per item/branch. SDK retries
or unexpected decisions can increase that number. The local `max_loop: 3`
limits repeated evaluation visits; it is not a spending or wall-clock limit.
If Jev approves the initial wrong equation, the experiment explicitly fails rather
than claiming that the repair loop worked. If it keeps rejecting the repaired
equation, the loop limit fails that local workflow.

## Success criteria

- The command exits with code 0 and the workflow is marked completed.
- `map_results` and `parallel_results` each contain two successful results.
- Every output is `{"equation": "2 + 2 = 4", "action": "approved"}`.
- The run record's `local_workflows` shows `assess → route → repair → assess`
  for each item/branch, followed by successful completion.

Run records are stored under `.fdsx/runs/<thread-id>/run.json`; use the thread ID
shown by the command. Each invocation creates a fresh run unless you explicitly
supply an existing ID. An error from authentication, connectivity, or model access
is not proof of a local-workflow bug. Keep the full record locally and redact it
before sharing, especially if you later replace the sample with real content.

This checks `type: evaluate` in map and parallel. It does not test the classifier,
Jev schema-based tasks, resume, or every failure mode against the live service.
