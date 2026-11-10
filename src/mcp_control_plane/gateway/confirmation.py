"""HMAC-SHA256 two-step confirmation gate and single-use nonce replay protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Protocol

from mcp_control_plane.core.errors import ConfirmationRequiredError, ValidationFailedError
from mcp_control_plane.core.metrics import record_guardrail_block


def canonical_args_hash(args: dict[str, Any]) -> str:
    """Generate deterministic SHA256 hex digest of tool arguments (ignoring confirmation_token)."""
    clean_args = {k: v for k, v in args.items() if k != "confirmation_token"}
    canonical_json = json.dumps(clean_args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class NonceStore(Protocol):
    """Protocol for marking and checking consumed confirmation token nonces."""

    async def consume_nonce(self, nonce: str, ttl_seconds: int) -> bool:
        """Attempt to consume a nonce. Returns True if successfully consumed (first use), False if replayed."""
        ...


class InMemoryNonceStore:
    """In-memory thread-safe nonce store for testing and single-process deployments."""

    def __init__(self) -> None:
        self._consumed: dict[str, float] = {}

    def _purge_expired(self) -> None:
        now = time.time()
        self._consumed = {k: exp for k, exp in self._consumed.items() if exp > now}

    async def consume_nonce(self, nonce: str, ttl_seconds: int) -> bool:
        self._purge_expired()
        now = time.time()
        if nonce in self._consumed:
            return False
        self._consumed[nonce] = now + ttl_seconds
        return True


class RedisNonceStore:
    """Redis-backed atomic nonce store using SETNX with expiration."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def consume_nonce(self, nonce: str, ttl_seconds: int) -> bool:
        key = f"mcp:nonce:{nonce}"
        # Set if Not Exists with expiry
        result = await self._redis.set(key, "1", ex=ttl_seconds, nx=True)
        return bool(result)


class ConfirmationGate:
    """Issues and verifies cryptographic HMAC confirmation tokens for mutating operations."""

    def __init__(
        self,
        secret: str,
        ttl_seconds: int = 120,
        nonce_store: NonceStore | None = None,
    ) -> None:
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds
        self.nonce_store = nonce_store or InMemoryNonceStore()

    def issue_token(
        self,
        client_id: str,
        tool_name: str,
        args_hash: str,
    ) -> str:
        """Create a signed HMAC-SHA256 confirmation token."""
        exp = int(time.time()) + self.ttl_seconds
        nonce = uuid.uuid4().hex
        payload = {
            "client_id": client_id,
            "tool": tool_name,
            "args_hash": args_hash,
            "exp": exp,
            "nonce": nonce,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self.secret, payload_bytes, hashlib.sha256).digest()

        encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8")
        encoded_sig = base64.urlsafe_b64encode(sig).decode("utf-8")
        return f"{encoded_payload}.{encoded_sig}"

    async def verify_and_consume(
        self,
        token: str,
        client_id: str,
        tool_name: str,
        current_args_hash: str,
    ) -> None:
        """Validate token signature, expiration, bindings, and ensure single-use nonce."""
        parts = token.strip().split(".")
        if len(parts) != 2:
            record_guardrail_block("confirmation_malformed_token", tool=tool_name)
            raise ValidationFailedError("Malformed confirmation token format.")

        encoded_payload, encoded_sig = parts
        try:
            payload_bytes = base64.urlsafe_b64decode(encoded_payload.encode("utf-8"))
            provided_sig = base64.urlsafe_b64decode(encoded_sig.encode("utf-8"))
        except Exception as e:
            record_guardrail_block("confirmation_decode_failed", tool=tool_name)
            raise ValidationFailedError(f"Failed to decode confirmation token: {e}") from e

        expected_sig = hmac.new(self.secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, provided_sig):
            record_guardrail_block("confirmation_signature_invalid", tool=tool_name)
            raise ValidationFailedError("Invalid confirmation token signature.")

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            record_guardrail_block("confirmation_json_invalid", tool=tool_name)
            raise ValidationFailedError("Invalid confirmation token payload.") from e

        # Expiration check
        if time.time() > payload.get("exp", 0):
            record_guardrail_block("confirmation_token_expired", tool=tool_name)
            raise ValidationFailedError("Confirmation token has expired.")

        # Client ID binding check
        if payload.get("client_id") != client_id:
            record_guardrail_block("confirmation_client_mismatch", tool=tool_name)
            raise ValidationFailedError("Confirmation token client mismatch.")

        # Tool binding check
        if payload.get("tool") != tool_name:
            record_guardrail_block("confirmation_tool_mismatch", tool=tool_name)
            raise ValidationFailedError("Confirmation token tool mismatch.")

        # Arguments hash binding check
        if payload.get("args_hash") != current_args_hash:
            record_guardrail_block("confirmation_args_mismatch", tool=tool_name)
            raise ValidationFailedError(
                "Confirmation token arguments mismatch. Arguments were altered."
            )

        # Replay prevention (consume single-use nonce)
        nonce = payload.get("nonce", "")
        consumed = await self.nonce_store.consume_nonce(nonce, ttl_seconds=self.ttl_seconds)
        if not consumed:
            record_guardrail_block("confirmation_replay_blocked", tool=tool_name)
            raise ValidationFailedError(
                "Confirmation token has already been consumed. Replay rejected."
            )

    async def check_or_prompt_confirmation(
        self,
        client_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        """If confirmation_token is absent, raises ConfirmationRequiredError with preview and new token.

        If present, verifies and consumes the token.
        """
        token = args.get("confirmation_token")
        args_hash = canonical_args_hash(args)

        if not token:
            new_token = self.issue_token(client_id, tool_name, args_hash)
            clean_preview = {k: v for k, v in args.items() if k != "confirmation_token"}
            raise ConfirmationRequiredError(
                preview=clean_preview,
                confirmation_token=new_token,
                expires_in_seconds=self.ttl_seconds,
            )

        await self.verify_and_consume(token, client_id, tool_name, args_hash)
