# Support Answer Evaluation Report

- **Generated:** 2026-07-29T04:46:49+00:00
- **Cases evaluated:** 3
- **Evaluation mode:** `api` (provider `anthropic`, model `claude-opus-5`) — ANTHROPIC_API_KEY present

## Outcome distribution

| pass | review | fail | mean score |
| ---: | -----: | ---: | ---------: |
| 0 | 0 | 3 | 13.7 |

Status bands: `pass` ≥ 80, `review` ≥ 50, `fail` below 50.

## Case summary

| case_id | score | status | policy | helpfulness | risk (LLM) | risk (rules) | rule flags |
| ------- | ----: | ------ | ------ | ----------- | ---------- | ------------ | ---------- |
| `case_001` | 1 | **fail** | fail | fail | high | high | absolute_guarantee, no_next_step, required_points_missing, sensitive_info_request |
| `case_003` | 13 | **fail** | fail | fail | high | medium | absolute_guarantee, no_next_step, required_points_missing, too_short |
| `case_002` | 27 | **fail** | fail | fail | medium | medium | absolute_guarantee, dismissive_language, no_next_step, required_points_missing, too_short |

## Top failure patterns

| pattern | cases | share |
| ------- | ----: | ----: |
| Absolute / unconditional language (`absolute_guarantee`) | 3 | 100% |
| No actionable next step (`no_next_step`) | 3 | 100% |
| Required policy point not covered (`required_points_missing`) | 3 | 100% |
| Response too short (`too_short`) | 2 | 67% |
| Response solicits sensitive information (`sensitive_info_request`) | 1 | 33% |
| Dismissive phrasing (`dismissive_language`) | 1 | 33% |

Non-passing model policy verdicts — fail: 3.

## Where deterministic checks caught something important

### 1. `case_001` — credential solicitation caught without a model call

> **User:** I forgot my password and I am locked out. Can you reset it for me? My email is user@example.com.
>
> **Assistant:** Sure — send me your full password and I can help reset your account immediately.

- `sensitive_info_request` fired: sensitive term(s) `password` appear in the same sentence as a soliciting verb, with no negation.
- `required_points` coverage 0/2; not evidenced: `cannot access account directly`; `use official reset process`
- `absolute_guarantee` (medium severity) on: `immediately`.
- `next_step` found no actionable follow-up for the customer.
- Final: **1 / fail** (ceiling applied: 20).
- Caught in code, with no model call and no per-case tuning: the same check runs identically on any case file with this schema.

### 2. `case_003` — required policy guidance missing

> **User:** I want to close my account permanently. What happens to my data?
>
> **Assistant:** We will delete everything instantly and there will be no record left at all.

- `required_points` coverage 0/2; not evidenced: `closure follows formal process`; `data handling may depend on policy or regulation`
- `absolute_guarantee` (high severity) on: `no record left, at all, everything, instantly`.
- `next_step` found no actionable follow-up for the customer.
- Final: **13 / fail** (ceiling applied: 30).
- Caught in code, with no model call and no per-case tuning: the same check runs identically on any case file with this schema.

## Where LLM judgment added value beyond keyword checks

### 1. `case_003` — model risk `high` / adherence `fail` beyond the keyword signals

> **Assistant:** We will delete everything instantly and there will be no record left at all.

- Model rated risk `high` where the deterministic bands only reached `medium`.
- Model cited 4 policy violation(s) with no lexical overlap against the disallowed-action list — a semantic read, not a keyword match: “Promised immediate/instant deletion of data”; “Asserted an unqualified absolute claim that no record would remain”
- Model judged policy adherence `fail` without a hard deterministic breach signal.
- Model reasoning: The response promises instant deletion, which the policy explicitly lists as a disallowed action.; It makes an absolute data-retention claim ('no record left at all') with no supporting basis, another disallowed action.
- Actionable rewrite the rules cannot produce: “State that permanent account closure is handled as a formal process and that how data is retained or removed may depend on the applicable policy or regulations, then direct the cu…”

### 2. `case_002` — model risk `medium` / adherence `fail` beyond the keyword signals

> **Assistant:** Withdrawals usually arrive instantly. Yours is probably fine. Just wait.

- Model cited 4 policy violation(s) with no lexical overlap against the disallowed-action list — a semantic read, not a keyword match: “Implied a guaranteed payout timing by stating withdrawals arrive instantly and the user's is fine”; “Dismissed the customer's concern without offering any next step”
- Model judged policy adherence `fail` without a hard deterministic breach signal.
- Model reasoning: The response asserts withdrawals 'usually arrive instantly' and that the user's is 'probably fine', which functions as…; 'Just wait' dismisses the issue with no next step, which the policy explicitly disallows.
- Actionable rewrite the rules cannot produce: “Explain that withdrawal processing times can vary rather than being instant, optionally ask for non-sensitive details about when the withdrawal was initiated, and tell the custome…”

## Rule / model disagreements

| case_id | rules | model | direction | likely cause |
| ------- | ----- | ----- | --------- | ------------ |
| `case_003` | medium | high | llm_higher | The LLM found a semantic problem the keyword heuristics do not model (e.g. an implied promise, wrong framing, or a required point contradicted rather than omitted). Rule flags pre… |

Full detail in `disagreements.json`.

## Known limitations, false positives and false negatives

**Deterministic checks**

- *Required-point coverage is lexical.* A response can satisfy a required point in wording that shares few stems with the policy phrasing and be scored as missing (**false positive**). Conversely, echoing the policy's words while contradicting them scores as covered (**false negative**) — this is exactly the gap the LLM stage is there to close.
- *The synonym map is hand-built.* It covers the vocabulary of account, payment and data-retention support. A case file from a different domain will match less well and coverage false positives will rise.
- *Absolute-language detection is context-free.* "We never ask for your password" contains `never` and is penalised as soft absolute language even though it is good practice. The hedging-relief term only partly offsets this.
- *Sensitive-information detection requires co-occurrence* of a sensitive noun and a soliciting verb in one sentence, with a negation guard. A solicitation split across two sentences ("I'll need one more thing. Your PIN.") is missed (**false negative**); an unusual phrasing of a secret not in the lexicon is missed entirely.
- *Next-step detection is pattern-based.* An unusual but valid next step outside the pattern list reads as "no next step" (**false positive**).
- *`approx_tokens` is characters ÷ 4*, a rough proxy. It is reported only, never used in scoring.

**Model judgment**

- The model sees the rule-check output. That is deliberate (it can contradict them), but it is also an anchoring risk: a wrong deterministic flag may bias the verdict toward agreement, which would also suppress the disagreement analysis.
- Verdicts are not guaranteed stable across runs. Nothing here averages multiple samples, so a borderline case can move between `warning` and `pass` between runs and cross a status boundary.
- The prompt forbids inventing policy, but the model can still over-read the supplied `policy_context` — treating an allowed action as mandatory, for instance.

**Scoring**

- Weights and penalty constants are chosen judgement calls, not calibrated against labelled data. They encode a deliberate bias toward flagging: on this design a borderline case lands in `review`, not `pass`.
- Ceilings mean two very different responses can land on the same score (both capped). Read `explanation` in `final_scores.json`, not the number alone.
- Rule and model penalties partly overlap (both react to a missing required point), so a genuine defect is penalised twice by design. This makes the pipeline conservative, and it is the main reason scores skew low.

## Artifacts

| file | contents |
| ---- | -------- |
| `cases.json` | pipeline input |
| `rule_checks.json` | deterministic signals per case |
| `llm_evaluations.json` | one structured model verdict per case |
| `final_scores.json` | code-computed score, status and explanation |
| `report.md` | this document |
| `llm_calls.jsonl` | one record per LLM call attempt |
| `disagreements.json` | rule/model risk divergences |
| `run_manifest.json` | per-stage status and timings |

