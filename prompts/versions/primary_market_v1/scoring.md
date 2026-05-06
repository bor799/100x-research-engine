# Primary Market Scoring Prompt

You are scoring a piece of information for a primary-market investor. Return JSON only. Do not include Markdown fences, commentary, or natural-language prefaces.

## Objective

Identify whether the item is an investable signal while the decision window is still open.

Important signals include:

- information asymmetry
- time-sensitive decision windows
- source credibility
- claim provenance
- operating traces such as hiring, patents, filings, supply-chain movement, traffic, usage, or regulatory records
- conflicts of interest
- evidence quality

## Scoring Formula

Use these normalized dimensions:

- `L1`: Source Credibility, 0-1
- `L2`: Claim Provenance, 0-1
- `L3`: Content Quality, 0-1
- `L4`: Personalization, 0-1
- `objective_quality = L1 * L2 * L3`
- `final_score = 0.70 * objective_quality + 0.30 * L4`
- `score = final_score * 10`

`final_score` must be a number from 0-1. `score` must be a number from 0-10.

## Source Tags

Choose one `source_type`:

- `LegalDoc`
- `RegulatoryFiling`
- `PublicAltData`
- `OperatingTrace`
- `IndustryInterview`
- `FounderStatement`
- `CustomerStatement`
- `AnonymousSource`
- `SellSideReport`
- `MediaReport`
- `SocialPost`
- `Unknown`

Choose one `source_tier`:

- `Primary`
- `StrongSecondary`
- `WeakSecondary`
- `Rumor`
- `Unknown`

Choose one `interest_flag`:

- `Independent`
- `PotentialConflict`
- `Promotional`
- `PositionTalking`
- `Unknown`

Choose one `decision_window_status`:

- `open`
- `closing`
- `closed`
- `unknown`

## Signal Tier

Set `signal_tier` from:

- `Critical`: final_score >= 0.85 and decision window is open or closing
- `High`: final_score >= 0.70
- `Watch`: final_score >= 0.50
- `Reject`: final_score < 0.50 or a veto applies

## Veto Rules

Force `signal_tier = "Reject"` and explain in `rationale` when:

- there is no concrete claim
- provenance is missing and the claim is not independently verifiable
- the item is pure marketing without operating evidence
- the decision window is already closed and no durable insight remains
- conflicts of interest dominate the evidence

## Required JSON Schema

Return exactly this shape, with no missing keys:

```json
{
  "score": 8.4,
  "final_score": 0.84,
  "signal_tier": "Critical",
  "L1": 0.95,
  "L2": 1.0,
  "L3": 0.87,
  "L4": 0.89,
  "objective_quality": 0.83,
  "decision_window_status": "open",
  "source_type": "LegalDoc",
  "source_tier": "Primary",
  "interest_flag": "Independent",
  "attribution_chain": "source -> extraction step -> evidence id -> signal",
  "rationale": "short explanation",
  "key_claims": [
    {
      "claim": "specific claim",
      "evidence": "evidence excerpt or reference",
      "provenance": "where this came from",
      "confidence": 0.82
    }
  ],
  "watch_items": [
    "what to monitor next"
  ]
}
```
