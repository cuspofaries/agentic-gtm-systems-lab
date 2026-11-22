# Agent v1 results (historical, frozen)

Recorded with agent v1 (lexical evidence-term check, disconfirmation = one recent-events
search), last at commit `48ca5e2` (PR #13). The cases themselves are unchanged and still
in `../cases.yaml` and `../holdout.yaml`.

These cassettes replay only with the v1 code: check out `48ca5e2` and run

```bash
gtm-lab opportunity-eval --cassette evals/opportunity/v1/cassette.json
gtm-lab opportunity-eval --cases evals/opportunity/holdout.yaml --cassette evals/opportunity/v1/holdout-cassette.json
```

The reports here are the outputs of those commands at that commit, mistakes included.
