# Prompt Versioning And A/B Testing

## Why This Exists

V3 cannot treat prompts as static files. The system needs to compare old and new screening standards, old and new extraction formats, and decide which version should be active without rewriting code.

Two prompt roles matter most:

- `scoring`: information screening standard, decides whether a source is worth deeper processing.
- `extraction`: information extraction format, decides the structure and style of the final brief.

`telegram_brief` is a formatting role. It can be versioned too, but it is downstream from scoring and extraction.

## Current Bundles

Prompt registry:

```text
prompts/registry.json
```

Current bundles:

```text
prompts/versions/primary_market_v1/
  scoring.md
  extraction.md

prompts/versions/v2_legacy/
  scoring.md
  extraction.md
```

`primary_market_v1` is the V3 default. `v2_legacy` is the baseline copied from V2 prompts for comparison. Keep V2 legacy prompt text unchanged unless explicitly creating a new legacy-derived version.

## Switching

Switch active prompt bundle by changing:

```json
{
  "active_bundle": "primary_market_v1"
}
```

To test another bundle without making it active, add it to:

```json
{
  "parallel_test_bundles": ["primary_market_v1", "v2_legacy"]
}
```

The code path should load prompts through `PromptRegistry`, not hardcoded paths.

## Parallel Evaluation

For the same fetched content:

```text
FetchedContent
  -> scoring(primary_market_v1)
  -> extraction(primary_market_v1)

FetchedContent
  -> scoring(v2_legacy)
  -> extraction(v2_legacy)
```

Store each result with:

- `prompt_bundle`
- `prompt_role`
- `prompt_hash`
- `model`
- `input_hash`
- parsed JSON
- parser status
- score
- output preview

This lets V3 compare prompt quality without changing live output.

## Test Dataset

The second stage should create a fixture set under V3, not by reading V2 queue:

```text
tests/fixtures/articles/
  high_signal_primary_market.md
  low_quality_marketing.md
  technical_code_only.md
  long_form_timeout_candidate.md
```

Each fixture should include expected behavior:

- expected screening result
- expected source type
- expected decision window behavior
- expected parser success or failure

## Acceptance Rules

- Prompt paths are resolved by bundle and role.
- Scoring and extraction can be switched independently in config later.
- A run can compare multiple bundles for the same input.
- Parser errors never flow into Obsidian or Telegram.
- Live output records the active bundle and prompt hashes.
