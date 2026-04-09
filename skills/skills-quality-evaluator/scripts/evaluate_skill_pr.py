#!/usr/bin/env python3
"""Evaluate skill-focused GitHub PRs and produce a professional review comment."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DIMENSIONS = (
    "trigger_discoverability",
    "instruction_quality",
    "determinism_reliability",
    "structure_best_practice",
    "safety_compliance",
    "business_value_reusability",
)

WEIGHTS = {
    "trigger_discoverability": 15,
    "instruction_quality": 20,
    "determinism_reliability": 20,
    "structure_best_practice": 15,
    "safety_compliance": 15,
    "business_value_reusability": 15,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate GitHub PRs that add or update skills."
    )
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/repo format.")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number.")
    parser.add_argument(
        "--output-dir",
        help="Optional output directory. Defaults to test-results/skills-quality-evaluator/<repo>/<pr>/<timestamp>/",
    )
    parser.add_argument(
        "--post-comment",
        action="store_true",
        help="Post the generated Markdown review as a PR comment.",
    )
    parser.add_argument(
        "--submit-review",
        action="store_true",
        help="Submit the generated Markdown review as a PR review comment.",
    )
    return parser.parse_args()


def run_gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh {' '.join(args)} failed")
    return result.stdout


def get_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def ensure_output_dir(repo: str, pr_number: int, output_dir: str | None) -> Path:
    if output_dir:
        path = Path(output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        repo_slug = repo.replace("/", "__")
        path = (
            Path("test-results")
            / "skills-quality-evaluator"
            / repo_slug
            / f"pr-{pr_number}"
            / timestamp
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_pr_metadata(repo: str, pr_number: int) -> dict[str, Any]:
    stdout = run_gh(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,title,body,url,author,files,headRefOid,headRefName,baseRefName",
        ]
    )
    return json.loads(stdout)


def list_changed_skill_dirs(pr_metadata: dict[str, Any]) -> list[str]:
    skill_dirs: set[str] = set()
    for file_info in pr_metadata.get("files", []):
        path = file_info.get("path", "")
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "skills":
            skill_dirs.add("/".join(parts[:2]))
    return sorted(skill_dirs)


def gh_api_json(repo: str, endpoint: str) -> Any:
    stdout = run_gh(["api", f"repos/{repo}/{endpoint}"])
    return json.loads(stdout)


def fetch_repo_file(repo: str, path: str, ref: str) -> str:
    payload = gh_api_json(repo, f"contents/{path}?ref={ref}")
    if payload.get("type") != "file":
        raise RuntimeError(f"{path} is not a file at ref {ref}")
    content = payload.get("content", "")
    encoding = payload.get("encoding")
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return str(content)


def fetch_skill_bundle(repo: str, skill_dir: str, ref: str) -> dict[str, str]:
    bundle: dict[str, str] = {}

    def _walk(path: str) -> None:
        payload = gh_api_json(repo, f"contents/{path}?ref={ref}")
        if isinstance(payload, dict) and payload.get("type") == "file":
            if is_text_file(path):
                bundle[path] = fetch_repo_file(repo, path, ref)
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            item_path = item.get("path")
            item_type = item.get("type")
            if not item_path:
                continue
            if item_type == "dir":
                _walk(item_path)
            elif item_type == "file" and is_text_file(item_path):
                bundle[item_path] = fetch_repo_file(repo, item_path, ref)

    _walk(skill_dir)
    return dict(sorted(bundle.items()))


def is_text_file(path: str) -> bool:
    return path.endswith(
        (
            ".md",
            ".py",
            ".json",
            ".yaml",
            ".yml",
            ".txt",
            ".sh",
        )
    )


def load_review_prompt() -> str:
    script_path = Path(__file__).resolve()
    return (script_path.parent.parent / "references" / "skill_review_prompt.md").read_text(
        encoding="utf-8"
    )


def call_evaluator(prompt: str) -> dict[str, Any]:
    api_key = get_env("EVALUATOR_API_KEY")
    model = get_env("EVALUATOR_MODEL")
    base_url = get_env("EVALUATOR_API_BASE_URL", required=False, default="https://api.openai.com/v1")
    temperature = float(os.getenv("EVALUATOR_TEMPERATURE", "0"))

    body = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": load_review_prompt()},
            {"role": "user", "content": prompt},
        ],
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Evaluator HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Evaluator request failed: {exc}") from exc

    choices = payload.get("choices", [])
    if not choices:
        raise RuntimeError("Evaluator returned no choices.")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Evaluator returned empty content.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Evaluator returned invalid JSON: {content}") from exc


def build_evaluation_prompt(
    repo: str,
    pr_metadata: dict[str, Any],
    skill_dir: str,
    skill_files: dict[str, str],
) -> str:
    related_files = [
        file_info["path"]
        for file_info in pr_metadata.get("files", [])
        if file_info.get("path", "").startswith(f"{skill_dir}/")
    ]
    file_blocks = []
    for path, content in skill_files.items():
        file_blocks.append(f"### FILE: {path}\n```text\n{content}\n```")

    return (
        f"Repository: {repo}\n"
        f"PR: #{pr_metadata['number']} - {pr_metadata['title']}\n"
        f"PR URL: {pr_metadata['url']}\n"
        f"Skill Directory: {skill_dir}\n"
        f"Changed Files In PR: {json.dumps(related_files, ensure_ascii=False)}\n\n"
        f"Review the current skill bundle at the PR head commit.\n\n"
        + "\n\n".join(file_blocks)
    )


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    review_summary = result.get("review_summary")
    if not isinstance(review_summary, str) or not review_summary.strip():
        raise ValueError("Missing review_summary.")

    strengths = result.get("key_strengths")
    if not isinstance(strengths, list) or not all(isinstance(item, str) for item in strengths):
        raise ValueError("key_strengths must be a list of strings.")

    risks = result.get("key_risks")
    if not isinstance(risks, list) or not all(isinstance(item, str) for item in risks):
        raise ValueError("key_risks must be a list of strings.")

    details = result.get("details")
    if not isinstance(details, dict):
        raise ValueError("Missing details object.")

    normalized_details: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        entry = details.get(dimension)
        if not isinstance(entry, dict):
            raise ValueError(f"Missing dimension: {dimension}")
        score = entry.get("score")
        comment = entry.get("comment")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
            raise ValueError(f"Invalid score for {dimension}: {score}")
        if not isinstance(comment, str) or not comment.strip():
            raise ValueError(f"Missing comment for {dimension}")
        normalized_details[dimension] = {
            "score": round(float(score), 2),
            "comment": comment.strip(),
        }

    return {
        "review_summary": review_summary.strip(),
        "key_strengths": [item.strip() for item in strengths if item.strip()],
        "key_risks": [item.strip() for item in risks if item.strip()],
        "details": normalized_details,
    }


def weighted_total(details: dict[str, dict[str, Any]]) -> float:
    total = 0.0
    for dimension, weight in WEIGHTS.items():
        total += details[dimension]["score"] * (weight / 100)
    return round(total, 2)


def rating_level(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Good"
    if score >= 70:
        return "Needs Improvement"
    return "Rejected"


def recommendation(score: float) -> str:
    if score >= 90:
        return "Publish"
    if score >= 80:
        return "Publish with Minor Improvements"
    if score >= 70:
        return "Revise Before Approval"
    return "Do Not Publish"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_skill_section(skill_name: str, evaluation: dict[str, Any]) -> str:
    lines = [
        f"### `{skill_name}`",
        "",
        evaluation["review_summary"],
        "",
        f"**Overall score:** {evaluation['overall_score']}",
        f"**Rating:** {evaluation['rating_level']}",
        f"**Recommendation:** {evaluation['recommendation']}",
        "",
        "| Dimension | Weight | Score | Comment |",
        "| --- | ---: | ---: | --- |",
    ]
    labels = {
        "trigger_discoverability": "Trigger & Discoverability",
        "instruction_quality": "Instruction Quality",
        "determinism_reliability": "Determinism & Reliability",
        "structure_best_practice": "Structure & Best Practice",
        "safety_compliance": "Safety & Compliance",
        "business_value_reusability": "Business Value & Reusability",
    }
    for dimension in DIMENSIONS:
        detail = evaluation["details"][dimension]
        lines.append(
            f"| {labels[dimension]} | {WEIGHTS[dimension]}% | {detail['score']} | {detail['comment']} |"
        )

    if evaluation["key_strengths"]:
        lines.extend(["", "**Key strengths**"])
        lines.extend([f"- {item}" for item in evaluation["key_strengths"]])
    if evaluation["key_risks"]:
        lines.extend(["", "**Key risks / improvements**"])
        lines.extend([f"- {item}" for item in evaluation["key_risks"]])
    lines.append("")
    return "\n".join(lines)


def render_pr_comment(repo: str, pr_metadata: dict[str, Any], evaluations: list[dict[str, Any]]) -> str:
    lines = [
        "# Skill Governance Review",
        "",
        f"Repository: `{repo}`",
        f"PR: [#{pr_metadata['number']} {pr_metadata['title']}]({pr_metadata['url']})",
        f"Head branch: `{pr_metadata.get('headRefName', 'unknown')}`",
        f"Base branch: `{pr_metadata.get('baseRefName', 'unknown')}`",
        "",
        "## Summary",
        "",
        "| Skill | Overall Score | Rating | Recommendation |",
        "| --- | ---: | --- | --- |",
    ]
    for evaluation in evaluations:
        lines.append(
            f"| `{evaluation['skill_name']}` | {evaluation['overall_score']} | "
            f"{evaluation['rating_level']} | {evaluation['recommendation']} |"
        )
    lines.append("")
    lines.append("## Detailed Review")
    lines.append("")
    for evaluation in evaluations:
        lines.append(render_skill_section(evaluation["skill_name"], evaluation))
    lines.extend(
        [
            "## Method",
            "",
            "This review evaluates the skill submission against six dimensions: trigger and discoverability, instruction quality, determinism and reliability, structure and best practice, safety and compliance, and business value and reusability. The final score is a weighted total derived from those dimension scores.",
            "",
        ]
    )
    return "\n".join(lines)


def post_pr_comment(repo: str, pr_number: int, comment_markdown: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(comment_markdown)
        temp_path = handle.name
    try:
        run_gh(["pr", "comment", str(pr_number), "--repo", repo, "--body-file", temp_path])
    finally:
        Path(temp_path).unlink(missing_ok=True)


def submit_pr_review(repo: str, pr_number: int, comment_markdown: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(comment_markdown)
        temp_path = handle.name
    try:
        run_gh(["pr", "review", str(pr_number), "--repo", repo, "--comment", "--body-file", temp_path])
    finally:
        Path(temp_path).unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    if args.post_comment and args.submit_review:
        raise ValueError("Use either --post-comment or --submit-review, not both.")

    output_dir = ensure_output_dir(args.repo, args.pr, args.output_dir)
    pr_metadata = fetch_pr_metadata(args.repo, args.pr)
    write_json(output_dir / "pr_metadata.json", pr_metadata)

    changed_skill_dirs = list_changed_skill_dirs(pr_metadata)
    if not changed_skill_dirs:
        summary = {
            "repo": args.repo,
            "pr": args.pr,
            "message": "PR does not modify any skill directory under skills/.",
            "evaluations": [],
        }
        write_json(output_dir / "run_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    evaluations: list[dict[str, Any]] = []
    for skill_dir in changed_skill_dirs:
        skill_files = fetch_skill_bundle(args.repo, skill_dir, pr_metadata["headRefOid"])
        prompt = build_evaluation_prompt(args.repo, pr_metadata, skill_dir, skill_files)
        raw_result = call_evaluator(prompt)
        validated = validate_result(raw_result)
        overall = weighted_total(validated["details"])
        skill_report = {
            "skill_name": skill_dir.split("/", 1)[1],
            "skill_dir": skill_dir,
            "overall_score": overall,
            "rating_level": rating_level(overall),
            "recommendation": recommendation(overall),
            **validated,
        }
        evaluations.append(skill_report)
        report_name = f"{skill_report['skill_name']}_evaluation.json"
        write_json(output_dir / report_name, skill_report)

    comment_markdown = render_pr_comment(args.repo, pr_metadata, evaluations)
    comment_path = output_dir / "pr_review_comment.md"
    comment_path.write_text(comment_markdown + "\n", encoding="utf-8")

    if args.post_comment:
        post_pr_comment(args.repo, args.pr, comment_markdown)
    if args.submit_review:
        submit_pr_review(args.repo, args.pr, comment_markdown)

    summary = {
        "repo": args.repo,
        "pr": args.pr,
        "changed_skill_dirs": changed_skill_dirs,
        "comment_file": str(comment_path.resolve()),
        "posted_comment": args.post_comment,
        "submitted_review": args.submit_review,
        "evaluations": evaluations,
    }
    write_json(output_dir / "run_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
