#!/usr/bin/env python3
"""
Gemini code reviewer — called by .github/workflows/gemini.yml.

Reads context from env vars, calls Gemini API, and posts the response
as a comment on the triggering PR or issue.
"""

import os
import sys

from google import genai
from google.genai import types
from github import Github

SYSTEM_PROMPT = """You are an expert code reviewer embedded in a GitHub workflow.
You will be given context (a PR diff, issue description, or code snippet) and a
specific instruction from a developer who mentioned @gemini in a comment.

Respond with clear, actionable feedback in GitHub-flavored Markdown.
- Lead with a brief summary (1–3 sentences).
- Use bullet points or numbered lists for findings.
- Label each finding as **Blocking**, **Suggestion**, or **Nit** where relevant.
- If no issues are found, say so clearly.
- Keep the response focused and concise.
"""


def get_pr_diff(repo, pr_number: int) -> str | None:
    """Return the unified diff for a pull request, or None if not a PR."""
    try:
        pr = repo.get_pull(pr_number)
        files = pr.get_files()
        diff_parts = []
        for f in files:
            diff_parts.append(f"### {f.filename} ({f.status})")
            if f.patch:
                diff_parts.append(f"```diff\n{f.patch}\n```")
        return "\n\n".join(diff_parts) if diff_parts else None
    except Exception:
        return None


def build_context(repo, pr_number: int | None, event_name: str) -> str:
    """Build the code/issue context to send to Gemini."""
    if pr_number:
        diff = get_pr_diff(repo, pr_number)
        if diff:
            return f"## PR #{pr_number} — changed files\n\n{diff}"
        # Fallback: issue body
        try:
            issue = repo.get_issue(pr_number)
            return f"## Issue/PR #{pr_number}\n\n{issue.body or '(no description)'}"
        except Exception:
            pass
    return "(no additional context available)"


def get_latest_pro_model(client: genai.Client) -> str:
    """Return the model ID for the latest available Gemini Pro model.

    Queries the API for all available models, filters to those whose name
    contains 'pro' and supports 'generateContent', then prefers stable over
    preview/experimental releases.  Fails loudly on any error so the Actions
    job surfaces a red failure rather than silently using a stale model.
    """
    # Let any API exception propagate — don't swallow errors.
    all_models = list(client.models.list())

    def _is_pro(m) -> bool:
        name = (m.name or "").lower()
        supported = [a.lower() for a in (getattr(m, "supported_actions", None) or [])]
        return "pro" in name and "generatecontent" in supported

    pro_models = [m for m in all_models if _is_pro(m)]
    if not pro_models:
        print(
            "ERROR: No Gemini Pro models available — check GEMINI_API_KEY and API quota.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Prefer stable (no 'preview' or 'experimental') if any exist.
    stable = [m for m in pro_models if "preview" not in m.name.lower() and "experimental" not in m.name.lower()]
    candidates = stable if stable else pro_models

    # Sort descending by name so the lexicographically latest version wins
    # (e.g. "gemini-3.1-pro" > "gemini-3-pro" > "gemini-1.5-pro").
    candidates.sort(key=lambda m: m.name, reverse=True)
    chosen = candidates[0].name
    # The API returns names like "models/gemini-3.1-pro"; strip the prefix.
    if chosen.startswith("models/"):
        chosen = chosen[len("models/"):]
    print(f"Selected model: {chosen}")
    return chosen


def strip_mention(text: str) -> str:
    """Remove @gemini from the instruction text."""
    return text.replace("@gemini", "").strip()


def main() -> None:
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    repo_name = os.environ.get("REPO", "")
    comment_body = os.environ.get("COMMENT_BODY", "")
    issue_number_str = os.environ.get("ISSUE_NUMBER", "")
    pr_number_str = os.environ.get("PR_NUMBER", "")

    if not gemini_api_key:
        print("ERROR: GEMINI_API_KEY secret is not set.", file=sys.stderr)
        sys.exit(1)

    if not github_token or not repo_name:
        print("ERROR: GITHUB_TOKEN or REPO env var missing.", file=sys.stderr)
        sys.exit(1)

    instruction = strip_mention(comment_body)
    if not instruction:
        instruction = "Please review this code and provide feedback."

    # Resolve numbers
    pr_number = int(pr_number_str) if pr_number_str.isdigit() else None
    issue_number = int(issue_number_str) if issue_number_str.isdigit() else None
    target_number = issue_number or pr_number  # used for posting the reply

    if not target_number:
        print("ERROR: Could not determine issue/PR number.", file=sys.stderr)
        sys.exit(1)

    # GitHub client
    gh = Github(github_token)
    repo = gh.get_repo(repo_name)

    # Build code context
    context = build_context(repo, pr_number, os.environ.get("EVENT_NAME", ""))

    # Call Gemini
    client = genai.Client(api_key=gemini_api_key)
    model = get_latest_pro_model(client)
    user_message = f"{instruction}\n\n---\n\n{context}"
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    review_text = response.text.strip()

    # Post reply as a comment
    reply = f"### Gemini Code Review\n\n{review_text}\n\n---\n*Review generated by `{model}` via `@gemini` mention.*"
    issue = repo.get_issue(target_number)
    issue.create_comment(reply)
    print(f"Posted Gemini review to #{target_number}.")


if __name__ == "__main__":
    main()
