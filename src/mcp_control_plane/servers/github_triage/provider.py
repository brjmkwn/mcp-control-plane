"""GitHub REST API Provider abstraction with Live and Deterministic Mock implementations."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from mcp_control_plane.core.errors import UpstreamError
from mcp_control_plane.core.logging import get_logger

logger = get_logger("github_triage.provider")


class GitHubProvider(Protocol):
    """Protocol defining GitHub incident & CI triage operations."""

    async def get_failed_workflow_logs(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict[str, Any]: ...

    async def get_commit_diff(
        self,
        owner: str,
        repo: str,
        commit_sha: str,
    ) -> dict[str, Any]: ...

    async def list_open_incidents(
        self,
        owner: str,
        repo: str,
        label: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def draft_triage_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def post_pr_triage_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        comment: str,
    ) -> dict[str, Any]: ...


class DeterministicMockGitHubProvider:
    """Deterministic mock provider with seeded CI/CD failures and incidents for testing/benchmarks."""

    def __init__(self) -> None:
        self.created_issues: list[dict[str, Any]] = []
        self.created_comments: list[dict[str, Any]] = []

    async def get_failed_workflow_logs(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "repo": f"{owner}/{repo}",
            "status": "completed",
            "conclusion": "failure",
            "workflow_name": "CI / Test & Build",
            "failed_step": "Run Pytest Suite",
            "error_excerpt": (
                "FAILED tests/integration/test_orders.py::test_checkout_timeout\n"
                "AssertionError: Expected HTTP 200 OK, got HTTP 504 Gateway Timeout\n"
                "  File 'src/services/checkout.py', line 142 in execute_payment\n"
                "ConnectionRefusedError: [Errno 111] Connection refused to payment-gateway:5000"
            ),
            "duration_seconds": 182,
        }

    async def get_commit_diff(
        self,
        owner: str,
        repo: str,
        commit_sha: str,
    ) -> dict[str, Any]:
        return {
            "commit_sha": commit_sha,
            "repo": f"{owner}/{repo}",
            "author": "brij-admin",
            "message": "fix(payments): decrease connection pool idle timeout",
            "files_changed": [
                {
                    "filename": "src/services/checkout.py",
                    "status": "modified",
                    "additions": 4,
                    "deletions": 2,
                    "patch": (
                        "@@ -140,2 +140,4 @@\n"
                        "- timeout = 30.0\n"
                        "+ timeout = 5.0\n"
                        "+ pool_max_idle = 10\n"
                    ),
                }
            ],
            "stats": {"total": 6, "additions": 4, "deletions": 2},
        }

    async def list_open_incidents(
        self,
        owner: str,
        repo: str,
        label: str | None = None,
    ) -> list[dict[str, Any]]:
        incidents = [
            {
                "issue_number": 104,
                "title": "[P1 Incident] High checkout error rate in production",
                "state": "open",
                "labels": ["incident", "p1", "payments"],
                "author": "pagerduty-bot",
                "created_at": "2026-11-24T18:30:00Z",
                "comments_count": 5,
            },
            {
                "issue_number": 98,
                "title": "[P2 Incident] Transient token expiration alerts from auth-service",
                "state": "open",
                "labels": ["incident", "p2", "auth"],
                "author": "monitoring-bot",
                "created_at": "2026-11-24T14:15:00Z",
                "comments_count": 2,
            },
        ]
        if label:
            incidents = [
                inc for inc in incidents if label.lower() in [lbl.lower() for lbl in inc["labels"]]
            ]
        return incidents

    async def draft_triage_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        issue_number = 200 + len(self.created_issues) + 1
        issue = {
            "issue_number": issue_number,
            "url": f"https://github.com/{owner}/{repo}/issues/{issue_number}",
            "title": title,
            "body": body,
            "labels": labels or ["triage", "incident"],
            "state": "open",
            "created_at": "2026-11-24T20:00:00Z",
        }
        self.created_issues.append(issue)
        return issue

    async def post_pr_triage_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        comment: str,
    ) -> dict[str, Any]:
        comment_id = 1000 + len(self.created_comments) + 1
        record = {
            "comment_id": comment_id,
            "url": f"https://github.com/{owner}/{repo}/pull/{pr_number}#issuecomment-{comment_id}",
            "pr_number": pr_number,
            "body": comment,
            "created_at": "2026-11-24T20:00:00Z",
        }
        self.created_comments.append(record)
        return record


class LiveGitHubProvider:
    """Production provider calling GitHub REST API v3 using AsyncClient and GITHUB_TOKEN."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "mcp-control-plane-triage/1.0",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.request(method, url, headers=self.headers, **kwargs)
                if response.status_code >= 500:
                    raise UpstreamError(
                        upstream_name="github",
                        message=f"GitHub upstream 5xx error ({response.status_code}): {response.text[:200]}",
                    )
                if response.status_code == 404:
                    raise UpstreamError(
                        upstream_name="github",
                        message=f"Resource not found on GitHub: {path}",
                        retryable=False,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as te:
                raise UpstreamError(
                    upstream_name="github",
                    message="Request to GitHub API timed out.",
                ) from te
            except httpx.HTTPStatusError as hse:
                raise UpstreamError(
                    upstream_name="github",
                    message=f"GitHub API HTTP error: {hse.response.status_code}",
                    retryable=(hse.response.status_code >= 500),
                ) from hse

    async def get_failed_workflow_logs(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        path = f"repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        data = await self._request("GET", path)
        jobs = data.get("jobs", [])
        failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
        excerpt = "No logs available"
        failed_step = "unknown"
        if failed_jobs:
            first_fail = failed_jobs[0]
            steps = first_fail.get("steps", [])
            failing_steps = [s for s in steps if s.get("conclusion") == "failure"]
            if failing_steps:
                failed_step = failing_steps[0].get("name", "unknown")
            excerpt = f"Job '{first_fail.get('name')}' failed at step '{failed_step}'"

        return {
            "run_id": run_id,
            "repo": f"{owner}/{repo}",
            "status": "completed",
            "conclusion": "failure" if failed_jobs else "success",
            "workflow_name": "GitHub Actions Run",
            "failed_step": failed_step,
            "error_excerpt": excerpt,
            "duration_seconds": 0,
        }

    async def get_commit_diff(self, owner: str, repo: str, commit_sha: str) -> dict[str, Any]:
        path = f"repos/{owner}/{repo}/commits/{commit_sha}"
        data = await self._request("GET", path)
        return {
            "commit_sha": commit_sha,
            "repo": f"{owner}/{repo}",
            "author": data.get("commit", {}).get("author", {}).get("name", "unknown"),
            "message": data.get("commit", {}).get("message", ""),
            "files_changed": data.get("files", [])[:10],
            "stats": data.get("stats", {}),
        }

    async def list_open_incidents(
        self, owner: str, repo: str, label: str | None = None
    ) -> list[dict[str, Any]]:
        path = f"repos/{owner}/{repo}/issues"
        params = {"state": "open", "labels": label or "incident"}
        issues = await self._request("GET", path, params=params)
        return [
            {
                "issue_number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "labels": [lbl["name"] for lbl in i.get("labels", [])],
                "author": i.get("user", {}).get("login", "unknown"),
                "created_at": i.get("created_at"),
                "comments_count": i.get("comments", 0),
            }
            for i in issues
        ]

    async def draft_triage_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        path = f"repos/{owner}/{repo}/issues"
        payload = {"title": title, "body": body, "labels": labels or ["incident", "triage"]}
        data = await self._request("POST", path, json=payload)
        return {
            "issue_number": data["number"],
            "url": data["html_url"],
            "title": data["title"],
            "body": data["body"],
            "labels": [lbl["name"] for lbl in data.get("labels", [])],
            "state": data["state"],
            "created_at": data["created_at"],
        }

    async def post_pr_triage_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        comment: str,
    ) -> dict[str, Any]:
        path = f"repos/{owner}/{repo}/issues/{pr_number}/comments"
        payload = {"body": comment}
        data = await self._request("POST", path, json=payload)
        return {
            "comment_id": data["id"],
            "url": data["html_url"],
            "pr_number": pr_number,
            "body": data["body"],
            "created_at": data["created_at"],
        }
