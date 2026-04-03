---
name: promptbook-prompt-evaluator
description: Evaluate PromptBook prompts by prompt ID or ID range, fetch prompt content from PromptBook, score prompt quality with the HSBC Prompt Quality Evaluator, generate strict JSON reports, and optionally post the evaluation back to PromptBook. Use this whenever the user mentions PromptBook, prompt review, prompt scoring, prompt publishing checks, prompt IDs like 100729, or asks to assess one prompt or a range of prompts for quality and publish readiness.
---

# PromptBook Prompt Evaluator

Use this skill to run the full PromptBook prompt-review flow:

1. Resolve one prompt ID, multiple IDs, or a numeric range from the user's request.
2. Fetch prompt content from PromptBook.
3. Evaluate each prompt with the HSBC Prompt Quality Evaluator.
4. Save one strict JSON report per prompt.
5. Optionally post the result back to PromptBook.

## Script Location

`skills/promptbook-prompt-evaluator/scripts/evaluate_promptbook_prompts.py`

## When To Use

Use this skill whenever the user asks to:

- evaluate a PromptBook prompt
- score prompt quality before publishing
- review PromptBook prompt IDs
- assess a prompt range such as `100729 to 100740` or `100729 到 100740`
- generate JSON prompt-review output for governance or publishing

## Inputs

The user will usually provide one of these:

- a single prompt ID
- multiple prompt IDs
- a numeric range
- less commonly, direct prompt text for a one-off evaluation

If the request does not contain a resolvable prompt ID or range and no prompt text is provided, ask for the missing prompt identifier.

## Environment Variables

The bundled script expects configurable endpoints instead of hardcoded HSBC internals.
Read [api_contract.md](/Users/olivia/shawn/codespace/shawn-skills/skills/promptbook-prompt-evaluator/references/api_contract.md) before first use.

Required for PromptBook fetch:

- `PROMPTBOOK_GET_PROMPT_URL_TEMPLATE`
- `PROMPTBOOK_API_TOKEN` if the endpoint requires bearer auth

Required for evaluation:

- `EVALUATOR_API_KEY`
- `EVALUATOR_MODEL`

Optional for evaluation:

- `EVALUATOR_API_BASE_URL` defaults to `https://api.openai.com/v1`
- `EVALUATOR_TEMPERATURE` defaults to `0`

Optional for posting results back:

- `PROMPTBOOK_POST_SCORE_URL_TEMPLATE`

Optional JSON-path overrides for fetch responses:

- `PROMPTBOOK_PROMPT_TEXT_PATH` defaults to `prompt`
- `PROMPTBOOK_PROMPT_TITLE_PATH` defaults to `title`

## Usage

Run from the repository root.

Single prompt:

```bash
python3 skills/promptbook-prompt-evaluator/scripts/evaluate_promptbook_prompts.py \
  --request "帮我评估一下 100729"
```

Range:

```bash
python3 skills/promptbook-prompt-evaluator/scripts/evaluate_promptbook_prompts.py \
  --request "评估 100729 到 100735" \
  --post-results
```

Explicit IDs:

```bash
python3 skills/promptbook-prompt-evaluator/scripts/evaluate_promptbook_prompts.py \
  --ids 100729 100731 100740
```

Dry-run ID parsing only:

```bash
python3 skills/promptbook-prompt-evaluator/scripts/evaluate_promptbook_prompts.py \
  --request "帮我评估 100729 到 100731 和 100740" \
  --resolve-only
```

## Output

By default the script writes reports under:

`test-results/promptbook-prompt-evaluator/<timestamp>/`

It creates:

- one JSON file per prompt, such as `prompt_100729.json`
- `run_summary.json` for the full batch

## Review Rules

- Do not fabricate prompt content if PromptBook fetch fails.
- Do not return free-form prose instead of JSON.
- Validate all seven scoring dimensions before saving or posting.
- If the model omits `overall_score`, recompute it from the seven dimension scores.
- If posting is enabled, only post reports that passed local JSON validation.

## Implementation Notes

- Use the bundled Python script for deterministic steps instead of manually redoing the flow.
- Use [hsbc_prompt_quality_evaluator.md](/Users/olivia/shawn/codespace/shawn-skills/skills/promptbook-prompt-evaluator/references/hsbc_prompt_quality_evaluator.md) as the source evaluator prompt.
- If the PromptBook API response shape differs from the default assumptions, update the JSON-path environment variables or patch the client logic.
- If the post-score contract differs from the placeholder body shape, patch the posting function instead of forcing the wrong payload.
