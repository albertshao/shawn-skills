#!/usr/bin/env python3
"""Collect skill PR context, validate review JSON, and optionally post PR feedback."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
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

RECOMMENDATIONS = {"Approve", "Human Review", "Reject"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect GitHub skill PR context and assist agent-led review workflows."
    )
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/repo format.")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number.")
    parser.add_argument(
        "--output-dir",
        help="Optional output directory. Defaults to test-results/skills-quality-evaluator/<repo>/<pr>/<timestamp>/",
    )
    parser.add_argument(
        "--validate-review-json",
        help="Validate an agent-generated review JSON file against the expected schema.",
    )
    parser.add_argument(
        "--post-comment-file",
        help="Post a prepared Markdown file as a PR comment.",
    )
    parser.add_argument(
        "--submit-review-file",
        help="Submit a prepared Markdown file as a PR review comment.",
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


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def fetch_skill_bundle(repo: str, skill_dir: str, ref: str) -> dict[str, str]:
    bundle: dict[str, str] = {}

    def walk(path: str) -> None:
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
                walk(item_path)
            elif item_type == "file" and is_text_file(item_path):
                bundle[item_path] = fetch_repo_file(repo, item_path, ref)

    walk(skill_dir)
    return dict(sorted(bundle.items()))


def materialize_bundle(output_dir: Path, skill_dir: str, skill_files: dict[str, str]) -> dict[str, Any]:
    skill_name = skill_dir.split("/", 1)[1]
    bundle_root = output_dir / "skill_bundles" / skill_name
    bundle_root.mkdir(parents=True, exist_ok=True)
    manifest_files: list[str] = []

    for repo_path, content in skill_files.items():
        rel_path = repo_path[len(skill_dir) + 1 :] if repo_path.startswith(f"{skill_dir}/") else Path(repo_path).name
        target = bundle_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        manifest_files.append(str(target.resolve()))

    return {
        "skill_name": skill_name,
        "skill_dir": skill_dir,
        "bundle_root": str(bundle_root.resolve()),
        "files": sorted(manifest_files),
    }


def build_review_stub(skill_name: str) -> dict[str, Any]:
    return {
        "overall_score": 0,
        "overall_comment": "",
        "recommendation": "Human Review",
        "details": {
            dimension: {
                "score": 0,
                "comment": "",
            }
            for dimension in DIMENSIONS
        },
        "_notes": f"Replace placeholder values after the agent completes the review for {skill_name}.",
    }


def write_review_template(output_dir: Path, repo: str, pr_metadata: dict[str, Any], skills: list[str]) -> Path:
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
        "| Skill | Overall Score | Recommendation | Overall Comment |",
        "| --- | ---: | --- | --- |",
    ]
    for skill_name in skills:
        lines.append(f"| `{skill_name}` | TODO | TODO | TODO |")

    lines.extend(
        [
            "",
            "## Detailed Review",
            "",
            "Replace the placeholders below with the final agent-generated review.",
            "",
        ]
    )
    for skill_name in skills:
        lines.extend(
            [
                f"### `{skill_name}`",
                "",
                "Overall assessment: TODO",
                "",
                "```json",
                json.dumps(build_review_stub(skill_name), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    template_path = output_dir / "pr_review_comment_template.md"
    template_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return template_path


def validate_review_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    overall_score = payload.get("overall_score")
    if not isinstance(overall_score, (int, float)) or not 0 <= float(overall_score) <= 100:
        raise ValueError("overall_score must be a number between 0 and 100.")

    overall_comment = payload.get("overall_comment")
    if not isinstance(overall_comment, str) or not overall_comment.strip():
        raise ValueError("overall_comment must be a non-empty string.")

    recommendation = payload.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        raise ValueError("recommendation must be one of: Approve, Human Review, Reject.")

    details = payload.get("details")
    if not isinstance(details, dict):
        raise ValueError("details must be an object.")

    for dimension in DIMENSIONS:
        entry = details.get(dimension)
        if not isinstance(entry, dict):
            raise ValueError(f"Missing details entry for {dimension}.")
        score = entry.get("score")
        comment = entry.get("comment")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
            raise ValueError(f"{dimension}.score must be a number between 0 and 100.")
        if not isinstance(comment, str) or not comment.strip():
            raise ValueError(f"{dimension}.comment must be a non-empty string.")

    return {
        "valid": True,
        "review_json": str(path.resolve()),
        "recommendation": recommendation,
        "overall_score": round(float(overall_score), 2),
    }


def post_pr_comment(repo: str, pr_number: int, comment_file: Path) -> None:
    run_gh(["pr", "comment", str(pr_number), "--repo", repo, "--body-file", str(comment_file)])


def submit_pr_review(repo: str, pr_number: int, comment_file: Path) -> None:
    run_gh(["pr", "review", str(pr_number), "--repo", repo, "--comment", "--body-file", str(comment_file)])


def main() -> int:
    args = parse_args()
    if args.post_comment_file and args.submit_review_file:
        raise ValueError("Use either --post-comment-file or --submit-review-file, not both.")

    output_dir = ensure_output_dir(args.repo, args.pr, args.output_dir)
    pr_metadata = fetch_pr_metadata(args.repo, args.pr)
    write_json(output_dir / "pr_metadata.json", pr_metadata)

    changed_skill_dirs = list_changed_skill_dirs(pr_metadata)
    write_json(output_dir / "changed_skill_dirs.json", changed_skill_dirs)
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

    bundle_manifests: list[dict[str, Any]] = []
    for skill_dir in changed_skill_dirs:
        skill_files = fetch_skill_bundle(args.repo, skill_dir, pr_metadata["headRefOid"])
        bundle_manifests.append(materialize_bundle(output_dir, skill_dir, skill_files))

    stub_dir = output_dir / "review_stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    for manifest in bundle_manifests:
        stub_path = stub_dir / f"{manifest['skill_name']}_review_stub.json"
        write_json(stub_path, build_review_stub(manifest["skill_name"]))

    template_path = write_review_template(
        output_dir,
        args.repo,
        pr_metadata,
        [manifest["skill_name"] for manifest in bundle_manifests],
    )

    validation_result: dict[str, Any] | None = None
    if args.validate_review_json:
        validation_result = validate_review_json(Path(args.validate_review_json))
        write_json(output_dir / "validation_result.json", validation_result)

    if args.post_comment_file:
        post_pr_comment(args.repo, args.pr, Path(args.post_comment_file))
    if args.submit_review_file:
        submit_pr_review(args.repo, args.pr, Path(args.submit_review_file))

    summary = {
        "repo": args.repo,
        "pr": args.pr,
        "changed_skill_dirs": changed_skill_dirs,
        "bundle_manifests": bundle_manifests,
        "review_template_file": str(template_path.resolve()),
        "validation_result": validation_result,
        "posted_comment_file": str(Path(args.post_comment_file).resolve()) if args.post_comment_file else None,
        "submitted_review_file": str(Path(args.submit_review_file).resolve()) if args.submit_review_file else None,
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
