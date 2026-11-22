# Agent v2 results on the decision cases (historical, frozen)

Recorded with agent v2 at commit `4e00248` (PR #16), before cassette keys included the
prompt and tool fingerprint. Replays with that commit:

```bash
gtm-lab decision-eval --cassette evals/decisions/v2/cassette.json
```

`REPORT.md` is the output of that command at that commit, mistakes included. The cases
(`../cases.yaml`) are unchanged.
