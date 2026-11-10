"""MCP Tools for GitHub CI & Incident Triage (Server 2)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mcp_control_plane.core.models import ToolResult, ToolSpec
from mcp_control_plane.gateway.registry import ToolRegistry
from mcp_control_plane.servers.github_triage.provider import GitHubProvider

SERVER_NAME = "github_triage"
UPSTREAM_NAME = "github"


class GetFailedWorkflowLogsInput(BaseModel):
    """Input for retrieving logs and error excerpts of a failed CI workflow."""

    owner: str = Field(description="Repository owner or organization name.")
    repo: str = Field(description="Repository name.")
    run_id: int = Field(description="Workflow run ID.")


class GetCommitDiffInput(BaseModel):
    """Input for inspecting files changed in a commit."""

    owner: str = Field(description="Repository owner.")
    repo: str = Field(description="Repository name.")
    commit_sha: str = Field(description="Commit SHA hash to inspect.")


class ListOpenIncidentsInput(BaseModel):
    """Input for listing open triage and incident issues."""

    owner: str = Field(description="Repository owner.")
    repo: str = Field(description="Repository name.")
    label: str | None = Field(
        default=None, description="Optional label filter (e.g. 'incident', 'p0', 'bug')."
    )


class DraftTriageIssueInput(BaseModel):
    """Input for creating a structured incident triage issue (Mutating / 2-Step Gate)."""

    owner: str = Field(description="Repository owner.")
    repo: str = Field(description="Repository name.")
    title: str = Field(description="Incident issue title.")
    body: str = Field(description="Incident description, timeline, and mitigation steps.")
    labels: list[str] | None = Field(default=None, description="Labels to tag the issue.")


class PostPrTriageCommentInput(BaseModel):
    """Input for posting root-cause comments on a PR (Mutating / 2-Step Gate)."""

    owner: str = Field(description="Repository owner.")
    repo: str = Field(description="Repository name.")
    pr_number: int = Field(description="Pull Request number.")
    comment: str = Field(description="Markdown comment body with root cause analysis.")


def register_github_triage_tools(registry: ToolRegistry, provider: GitHubProvider) -> None:
    """Register all 5 GitHub CI & Incident triage tools."""

    async def get_failed_workflow_logs_handler(params: GetFailedWorkflowLogsInput) -> ToolResult:
        data = await provider.get_failed_workflow_logs(params.owner, params.repo, params.run_id)
        return ToolResult.json_data(data)

    async def get_commit_diff_handler(params: GetCommitDiffInput) -> ToolResult:
        data = await provider.get_commit_diff(params.owner, params.repo, params.commit_sha)
        return ToolResult.json_data(data)

    async def list_open_incidents_handler(params: ListOpenIncidentsInput) -> ToolResult:
        data = await provider.list_open_incidents(params.owner, params.repo, params.label)
        return ToolResult.json_data({"incidents": data, "count": len(data)})

    async def draft_triage_issue_handler(params: DraftTriageIssueInput) -> ToolResult:
        issue = await provider.draft_triage_issue(
            params.owner,
            params.repo,
            params.title,
            params.body,
            params.labels,
        )
        return ToolResult.json_data(issue)

    async def post_pr_triage_comment_handler(params: PostPrTriageCommentInput) -> ToolResult:
        comment = await provider.post_pr_triage_comment(
            params.owner,
            params.repo,
            params.pr_number,
            params.comment,
        )
        return ToolResult.json_data(comment)

    # 1. get_failed_workflow_logs (Read)
    registry.register(
        ToolSpec(
            name="get_failed_workflow_logs",
            server=SERVER_NAME,
            description="Retrieve error excerpts and failing steps for a GitHub Actions workflow run.",
            input_model=GetFailedWorkflowLogsInput,
            handler=get_failed_workflow_logs_handler,
            required_scope="read:github",
            mutating=False,
            rate_limit_rpm=100,
            max_concurrency=20,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 2. get_commit_diff (Read)
    registry.register(
        ToolSpec(
            name="get_commit_diff",
            server=SERVER_NAME,
            description="Inspect changed files, patch diffs, and commit metadata for a commit SHA.",
            input_model=GetCommitDiffInput,
            handler=get_commit_diff_handler,
            required_scope="read:github",
            mutating=False,
            rate_limit_rpm=100,
            max_concurrency=20,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 3. list_open_incidents (Read)
    registry.register(
        ToolSpec(
            name="list_open_incidents",
            server=SERVER_NAME,
            description="List open GitHub issues filtered by incident or bug labels.",
            input_model=ListOpenIncidentsInput,
            handler=list_open_incidents_handler,
            required_scope="read:github",
            mutating=False,
            rate_limit_rpm=100,
            max_concurrency=20,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 4. draft_triage_issue (Write - Mutating 2-Step Confirmation)
    registry.register(
        ToolSpec(
            name="draft_triage_issue",
            server=SERVER_NAME,
            description="Create a structured incident triage issue (Requires 2-step confirmation).",
            input_model=DraftTriageIssueInput,
            handler=draft_triage_issue_handler,
            required_scope="write:github",
            mutating=True,
            rate_limit_rpm=30,
            max_concurrency=5,
            upstream_name=UPSTREAM_NAME,
        )
    )

    # 5. post_pr_triage_comment (Write - Mutating 2-Step Confirmation)
    registry.register(
        ToolSpec(
            name="post_pr_triage_comment",
            server=SERVER_NAME,
            description="Post root-cause analysis or fix suggestion comment on a PR (Requires 2-step confirmation).",
            input_model=PostPrTriageCommentInput,
            handler=post_pr_triage_comment_handler,
            required_scope="write:github",
            mutating=True,
            rate_limit_rpm=30,
            max_concurrency=5,
            upstream_name=UPSTREAM_NAME,
        )
    )
