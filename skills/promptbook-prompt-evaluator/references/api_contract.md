# API Contract Assumptions

This skill ships with placeholder API assumptions so it can be adapted to the real HSBC PromptBook endpoints without hardcoding internal URLs.

## Fetch Prompt

Set:

- `PROMPTBOOK_GET_PROMPT_URL_TEMPLATE`

Expected usage:

```text
https://promptbook.example/api/prompts/{prompt_id}
```

Default JSON-path assumptions:

- `prompt` for prompt text
- `title` for prompt title

Example fetch response shape:

```json
{
  "id": 100729,
  "title": "Customer Service Response Prompt",
  "prompt": "You are a helpful assistant..."
}
```

If the real API nests fields, set:

- `PROMPTBOOK_PROMPT_TEXT_PATH`
- `PROMPTBOOK_PROMPT_TITLE_PATH`

Example:

```text
PROMPTBOOK_PROMPT_TEXT_PATH=data.promptContent
PROMPTBOOK_PROMPT_TITLE_PATH=data.name
```

## Post Score

Set:

- `PROMPTBOOK_POST_SCORE_URL_TEMPLATE`

Expected usage:

```text
https://promptbook.example/api/prompts/{prompt_id}/evaluations
```

Default POST body:

```json
{
  "prompt_id": 100729,
  "evaluation": {
    "overall_score": 4.57,
    "overall_comment": "Well-structured and reusable.",
    "recommendation": "Publish",
    "details": {}
  }
}
```

If the real API expects a different payload, patch `post_result()` in the script.

## Authentication

If your PromptBook API requires bearer auth, set:

- `PROMPTBOOK_API_TOKEN`

If your evaluator endpoint requires bearer auth, set:

- `EVALUATOR_API_KEY`

## Evaluator Endpoint

The script assumes an OpenAI-compatible chat completions endpoint:

- `EVALUATOR_API_BASE_URL` default `https://api.openai.com/v1`
- `EVALUATOR_MODEL` required

If you are using Azure OpenAI or an internal compatible gateway, point `EVALUATOR_API_BASE_URL` to that endpoint.
