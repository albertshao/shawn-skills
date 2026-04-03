#!/usr/bin/env python3
"""Fetch PromptBook prompts, evaluate them, and save or post strict JSON reports."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DIMENSIONS = (
    "clarity",
    "completeness",
    "instruction_quality",
    "consistency",
    "generalizability",
    "maintainability",
    "compliance",
)

RECOMMENDATIONS = {"Publish", "Publish with Revision", "Do Not Publish"}


@dataclass
class PromptRecord:
    prompt_id: int
    title: str
    prompt_text: str
    raw_payload: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate PromptBook prompts and save strict JSON reports."
    )
    parser.add_argument("--request", help="Free-form user request containing IDs or ranges.")
    parser.add_argument("--ids", nargs="+", type=int, help="Explicit prompt IDs to evaluate.")
    parser.add_argument(
        "--range",
        dest="prompt_range",
        nargs=2,
        type=int,
        metavar=("START_ID", "END_ID"),
        help="Inclusive prompt ID range.",
    )
    parser.add_argument(
        "--prompt-text",
        help="Direct prompt text for one-off evaluation when PromptBook fetch is not needed.",
    )
    parser.add_argument(
        "--prompt-title",
        default="Ad hoc prompt",
        help="Prompt title to use with --prompt-text.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for generated reports. Defaults to test-results/propbook-prompt-evaluator/<timestamp>/",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Resolve prompt IDs and print them without fetching or evaluating.",
    )
    parser.add_argument(
        "--post-results",
        action="store_true",
        help="Post validated evaluation reports back to PromptBook.",
    )
    return parser.parse_args()


def resolve_prompt_ids(args: argparse.Namespace) -> list[int]:
    ids: set[int] = set(args.ids or [])
    if args.prompt_range:
        start_id, end_id = args.prompt_range
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        ids.update(range(start_id, end_id + 1))
    if args.request:
        ids.update(parse_prompt_ids_from_text(args.request))
    return sorted(ids)


def parse_prompt_ids_from_text(text: str) -> set[int]:
    ids: set[int] = set()
    range_pattern = re.compile(r"(\d{3,})\s*(?:-|到|to)\s*(\d{3,})", re.IGNORECASE)
    for match in range_pattern.finditer(text):
        start_id = int(match.group(1))
        end_id = int(match.group(2))
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        ids.update(range(start_id, end_id + 1))

    single_ids = re.findall(r"\b\d{3,}\b", text)
    ids.update(int(value) for value in single_ids)
    return ids


def ensure_output_dir(output_dir: str | None) -> Path:
    if output_dir:
        path = Path(output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path("test-results") / "propbook-prompt-evaluator" / timestamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def maybe_add_auth_headers(headers: dict[str, str], env_name: str) -> None:
    token = os.getenv(env_name)
    if token:
        headers["Authorization"] = f"Bearer {token}"


def http_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_headers = dict(headers or {})
    data: bytes | None = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method.upper(), headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {payload[:500]}") from exc


def dot_get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def fetch_prompt(prompt_id: int) -> PromptRecord:
    template = get_env("PROPBOOK_GET_PROMPT_URL_TEMPLATE")
    url = template.format(prompt_id=prompt_id)
    headers: dict[str, str] = {"Accept": "application/json"}
    maybe_add_auth_headers(headers, "PROPBOOK_API_TOKEN")
    payload = http_request("GET", url, headers=headers)

    title_path = os.getenv("PROPBOOK_PROMPT_TITLE_PATH", "title")
    prompt_path = os.getenv("PROPBOOK_PROMPT_TEXT_PATH", "prompt")

    title = dot_get(payload, title_path, f"Prompt {prompt_id}")
    prompt_text = dot_get(payload, prompt_path)
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise RuntimeError(
            f"Prompt text not found for prompt {prompt_id}. "
            f"Checked path '{prompt_path}'."
        )
    return PromptRecord(
        prompt_id=prompt_id,
        title=str(title),
        prompt_text=prompt_text.strip(),
        raw_payload=payload,
    )


def load_evaluator_prompt() -> str:
    script_path = Path(__file__).resolve()
    prompt_file = script_path.parent.parent / "references" / "hsbc_prompt_quality_evaluator.md"
    return prompt_file.read_text(encoding="utf-8")


def evaluate_prompt(prompt_record: PromptRecord) -> dict[str, Any]:
    base_url = get_env("EVALUATOR_API_BASE_URL", required=False, default="https://api.openai.com/v1")
    api_key = get_env("EVALUATOR_API_KEY")
    model = get_env("EVALUATOR_MODEL")
    temperature = float(os.getenv("EVALUATOR_TEMPERATURE", "0"))
    evaluator_instructions = load_evaluator_prompt()

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": evaluator_instructions},
            {
                "role": "user",
                "content": (
                    f"Prompt ID: {prompt_record.prompt_id}\n"
                    f"Prompt Title: {prompt_record.title}\n\n"
                    f"Prompt Content:\n{prompt_record.prompt_text}"
                ),
            },
        ],
    }
    payload = http_request("POST", f"{base_url.rstrip('/')}/chat/completions", headers=headers, body=body)
    choices = payload.get("choices")
    if not choices:
        raise RuntimeError("Evaluator response did not contain choices.")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Evaluator response did not contain JSON content.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Evaluator returned invalid JSON: {content}") from exc


def validate_evaluation(result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("details")
    if not isinstance(details, dict):
        raise ValueError("Evaluation JSON must contain a 'details' object.")

    normalized_details: dict[str, dict[str, Any]] = {}
    scores: list[float] = []
    for dimension in DIMENSIONS:
        entry = details.get(dimension)
        if not isinstance(entry, dict):
            raise ValueError(f"Missing detail block for dimension: {dimension}")
        score = entry.get("score")
        comment = entry.get("comment")
        if not isinstance(score, (int, float)) or not 0 <= score <= 5:
            raise ValueError(f"Invalid score for dimension '{dimension}': {score}")
        if not isinstance(comment, str) or not comment.strip():
            raise ValueError(f"Missing comment for dimension '{dimension}'")
        score_value = round(float(score), 2)
        normalized_details[dimension] = {"score": score_value, "comment": comment.strip()}
        scores.append(score_value)

    recommendation = result.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        raise ValueError(
            "Recommendation must be one of: Publish, Publish with Revision, Do Not Publish"
        )

    overall_comment = result.get("overall_comment")
    if not isinstance(overall_comment, str) or not overall_comment.strip():
        raise ValueError("Missing overall_comment in evaluation JSON.")

    overall_score = result.get("overall_score")
    computed_score = round(sum(scores) / len(scores), 2)
    if not isinstance(overall_score, (int, float)):
        overall_score = computed_score
    else:
        overall_score = round(float(overall_score), 2)

    normalized = {
        "overall_score": overall_score,
        "overall_comment": overall_comment.strip(),
        "recommendation": recommendation,
        "details": normalized_details,
    }
    return normalized


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def post_result(prompt_id: int, evaluation: dict[str, Any]) -> dict[str, Any]:
    template = get_env("PROPBOOK_POST_SCORE_URL_TEMPLATE")
    url = template.format(prompt_id=prompt_id)
    headers: dict[str, str] = {"Accept": "application/json"}
    maybe_add_auth_headers(headers, "PROPBOOK_API_TOKEN")
    body = {"prompt_id": prompt_id, "evaluation": evaluation}
    return http_request("POST", url, headers=headers, body=body)


def build_ad_hoc_prompt(prompt_text: str, prompt_title: str) -> PromptRecord:
    return PromptRecord(
        prompt_id=0,
        title=prompt_title,
        prompt_text=prompt_text.strip(),
        raw_payload={"title": prompt_title, "prompt": prompt_text.strip()},
    )


def main() -> int:
    args = parse_args()
    output_dir = ensure_output_dir(args.output_dir)

    prompt_ids = resolve_prompt_ids(args)
    if args.resolve_only:
        print(json.dumps({"prompt_ids": prompt_ids}, ensure_ascii=False, indent=2))
        return 0

    prompt_records: list[PromptRecord] = []
    errors: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    if args.prompt_text:
        prompt_records.append(build_ad_hoc_prompt(args.prompt_text, args.prompt_title))
    for prompt_id in prompt_ids:
        try:
            prompt_records.append(fetch_prompt(prompt_id))
        except Exception as exc:  # noqa: BLE001
            errors.append({"prompt_id": prompt_id, "stage": "fetch", "error": str(exc)})

    if not prompt_records:
        if errors:
            write_json(output_dir / "run_summary.json", {"reports": reports, "errors": errors})
            print(json.dumps({"reports": reports, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        raise ValueError("No prompt IDs or prompt text provided.")

    for prompt_record in prompt_records:
        target_label = (
            f"prompt_{prompt_record.prompt_id}" if prompt_record.prompt_id else "ad_hoc_prompt"
        )
        try:
            raw_evaluation = evaluate_prompt(prompt_record)
            normalized_evaluation = validate_evaluation(raw_evaluation)
            report = {
                "prompt_id": prompt_record.prompt_id,
                "prompt_title": prompt_record.title,
                "prompt_text": prompt_record.prompt_text,
                "evaluation": normalized_evaluation,
            }
            write_json(output_dir / f"{target_label}.json", report)

            posted_response: dict[str, Any] | None = None
            if args.post_results and prompt_record.prompt_id:
                posted_response = post_result(prompt_record.prompt_id, normalized_evaluation)
                write_json(output_dir / f"{target_label}_post_response.json", posted_response)

            reports.append(
                {
                    "prompt_id": prompt_record.prompt_id,
                    "prompt_title": prompt_record.title,
                    "report_file": str((output_dir / f"{target_label}.json").resolve()),
                    "posted": posted_response is not None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "prompt_id": prompt_record.prompt_id,
                    "prompt_title": prompt_record.title,
                    "stage": "evaluate_or_post",
                    "error": str(exc),
                }
            )

    summary = {"reports": reports, "errors": errors}
    write_json(output_dir / "run_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
