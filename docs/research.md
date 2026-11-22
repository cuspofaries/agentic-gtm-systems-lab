# Research loop

`--research exa` hands each account the rules cannot decide (`insufficient_evidence`) to Claude, with three tools. Claude chooses what to search, which pages to read and when to stop. The code bounds the loop and checks the evidence.

```bash
export EXA_API_KEY=... ANTHROPIC_API_KEY=...
gtm-lab demo --discovery pdl --research exa --research-steps 6 \
  --max-llm-cents 50 --research-exa-millidollars 50 --example-dir path/to/your-context-dir
```

It replaces `--agentic` for those accounts: `--agentic` asks Claude to decide from the account data alone; `--research` lets it gather more. The two cannot be combined. Accounts the rules decide never reach either.

## The tools

| Tool | What it does | Cost |
|---|---|---|
| `search(query)` | Exa `lite` search limited to the last `signal_policy.max_signal_age_days` days, 5 results with URL, title, date and an excerpt. | $0.004 (Exa's reported cost is charged) |
| `read(url)` | The text of one page (Exa Contents, first 6,000 characters). Only URLs returned by a search in this loop can be read. | $0.001 per page |
| `submit(decision, explanation, evidence)` | The final answer. Each evidence item is a URL and an exact quote. | none |

The tools are declared with `strict: true`, so the arguments always match their schema. Tool use is not forced (Claude Opus 5.5 rejects forced tool use); an answer without a tool call ends the loop as `research_no_decision`.

## What the code enforces

| Bound | Default | When reached |
|---|---|---|
| Tool calls per account (`--research-steps`) | 6, `submit` included | `insufficient_evidence`, missing `research_step_cap_reached` |
| LLM spend per run (`--max-llm-cents`) | 50¢ | Checked before every turn against its worst case (a high input estimate plus the full output cap); actual usage charged after. `research_llm_budget_would_exceed` |
| Exa spend per run (`--research-exa-millidollars`) | 50 ($0.05) | Checked before every search or read against its maximum. The tool answers that the budget is spent and Claude is asked to submit. |

Evidence is checked against what was actually read:

- the URL must have been read with `read` in this loop (a search excerpt does not count);
- the quote, whitespace and case aside, must appear in that page's text and be at least 8 characters.

Items that fail are dropped and counted in `missing` (`research_evidence_unverified`). A `qualified` or `not_qualified` answer with no item left becomes `insufficient_evidence` (`research_no_verified_evidence`). Kept items appear in the decision's evidence as `url: "quote" (quote found in the page read)`.

API errors, refusals, a turn cut by the output cap and a missing answer all leave the account `insufficient_evidence`, with the reason in `missing`.

## What it does not guarantee

The quote check proves that the words are on the page, not that the page is right. A page can be wrong, old, or about another company with the same name; a page can also contain text written to steer a model. Page text reaches Claude inside `<page>` tags with an instruction to treat it as data, the URL and quote are shown to the reviewer, and the plan still needs human approval before any CRM write.

## Audit

Every researched account gets a `qualification.fallback` event with the baseline decision, the research decision and `steps`: each tool call with its input, outcome and Exa cost, and the final `submit` with how many evidence items were kept and dropped. `gtm-lab inspect <run-id>` shows them. The CLI prints the LLM calls and the LLM and Exa spend of the run.

## Testing

`tests/unit/test_research_loop.py` scripts Claude's turns with a fake client and fakes Exa: the full search, read, submit path; quotes not in the page; evidence from a page never read; URLs not from a search; the step cap; both budgets; refusals, truncation, missing answers and API errors; and the events written by the workflow.
