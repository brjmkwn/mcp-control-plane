"""Unit tests for HMAC-SHA256 2-Step Confirmation Gate and replay protection."""

from __future__ import annotations

import pytest

from mcp_control_plane.core.errors import ValidationFailedError
from mcp_control_plane.gateway.confirmation import (
    ConfirmationGate,
    InMemoryNonceStore,
    canonical_args_hash,
)

SECRET = "super-secret-key-32-characters-minimum!!"


@pytest.fixture
def gate() -> ConfirmationGate:
    return ConfirmationGate(
        secret=SECRET,
        ttl_seconds=120,
        nonce_store=InMemoryNonceStore(),
    )


def test_canonical_args_hash_ordering() -> None:
    """Canonical hash should be independent of key order and exclude confirmation_token."""
    args1 = {"b": 2, "a": 1, "confirmation_token": "ignore-me"}
    args2 = {"a": 1, "b": 2}
    assert canonical_args_hash(args1) == canonical_args_hash(args2)


@pytest.mark.asyncio
async def test_successful_token_verification(gate: ConfirmationGate) -> None:
    """Valid token passes verification and is consumed."""
    client_id = "client-123"
    tool_name = "deploy_service"
    args = {"service": "payments", "version": "v1.2"}
    args_hash = canonical_args_hash(args)

    token = gate.issue_token(client_id, tool_name, args_hash)
    await gate.verify_and_consume(token, client_id, tool_name, args_hash)


@pytest.mark.asyncio
async def test_replay_token_blocked(gate: ConfirmationGate) -> None:
    """Consuming the same token twice must fail closed with replay error."""
    client_id = "client-123"
    tool_name = "delete_database"
    args_hash = canonical_args_hash({"db": "analytics"})

    token = gate.issue_token(client_id, tool_name, args_hash)
    # First use succeeds
    await gate.verify_and_consume(token, client_id, tool_name, args_hash)

    # Second use (replay) fails
    with pytest.raises(ValidationFailedError, match="Replay rejected"):
        await gate.verify_and_consume(token, client_id, tool_name, args_hash)


@pytest.mark.asyncio
async def test_altered_arguments_blocked(gate: ConfirmationGate) -> None:
    """Calling with altered arguments must fail closed with arguments mismatch."""
    client_id = "client-123"
    tool_name = "transfer_funds"
    orig_args = {"amount": 100, "to": "alice"}
    tampered_args = {"amount": 10000, "to": "eve"}

    token = gate.issue_token(client_id, tool_name, canonical_args_hash(orig_args))

    with pytest.raises(ValidationFailedError, match="Arguments were altered"):
        await gate.verify_and_consume(
            token, client_id, tool_name, canonical_args_hash(tampered_args)
        )


@pytest.mark.asyncio
async def test_tampered_signature_blocked(gate: ConfirmationGate) -> None:
    """Modifying token bytes must fail signature check."""
    client_id = "client-123"
    tool_name = "test_tool"
    args_hash = canonical_args_hash({"a": 1})

    token = gate.issue_token(client_id, tool_name, args_hash)
    tampered_token = token[:-4] + "AAAA"

    with pytest.raises(ValidationFailedError, match="signature"):
        await gate.verify_and_consume(tampered_token, client_id, tool_name, args_hash)


@pytest.mark.asyncio
async def test_client_mismatch_blocked(gate: ConfirmationGate) -> None:
    """Using another client's confirmation token must fail closed."""
    client_1 = "client-1"
    client_2 = "attacker-client"
    tool_name = "test_tool"
    args_hash = canonical_args_hash({"a": 1})

    token = gate.issue_token(client_1, tool_name, args_hash)

    with pytest.raises(ValidationFailedError, match="client mismatch"):
        await gate.verify_and_consume(token, client_2, tool_name, args_hash)


@pytest.mark.asyncio
async def test_tool_mismatch_blocked(gate: ConfirmationGate) -> None:
    """Using a token issued for tool A on tool B must fail closed."""
    client_id = "client-1"
    args_hash = canonical_args_hash({"a": 1})

    token = gate.issue_token(client_id, "safe_tool", args_hash)

    with pytest.raises(ValidationFailedError, match="tool mismatch"):
        await gate.verify_and_consume(token, client_id, "destructive_tool", args_hash)


@pytest.mark.asyncio
async def test_expired_token_blocked() -> None:
    """Expired confirmation token must be rejected."""
    short_gate = ConfirmationGate(secret=SECRET, ttl_seconds=-10)  # already expired
    args_hash = canonical_args_hash({"a": 1})

    token = short_gate.issue_token("client-1", "tool_a", args_hash)

    with pytest.raises(ValidationFailedError, match="expired"):
        await short_gate.verify_and_consume(token, "client-1", "tool_a", args_hash)
