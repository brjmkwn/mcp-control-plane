"""Transport and Identity security: Origin verification and Scoped Bearer RBAC."""

from __future__ import annotations

from mcp_control_plane.config import ClientPrincipal
from mcp_control_plane.core.errors import AuthError, OriginDeniedError, ScopeDeniedError
from mcp_control_plane.core.metrics import record_guardrail_block


def validate_origin(origin: str | None, allowed_origins: list[str]) -> None:
    """Validate incoming Origin header against allowlist to prevent DNS-rebinding attacks.

    - If Origin is provided, it MUST exist in allowed_origins.
    - If Origin is absent, request is allowed (non-browser CLI/server client).
    - If Origin is not allowlisted, fail closed with OriginDeniedError (HTTP 403).
    """
    if not origin:
        return

    clean_origin = origin.strip().rstrip("/")
    normalized_allowed = [o.strip().rstrip("/") for o in allowed_origins if o.strip()]

    if clean_origin not in normalized_allowed:
        record_guardrail_block("origin_denied", tool="gateway")
        raise OriginDeniedError(origin=origin)


def authenticate_bearer(
    auth_header: str | None,
    token_store: dict[str, ClientPrincipal],
) -> ClientPrincipal:
    """Authenticate request Bearer token against the provisioned principal store.

    Fails closed (HTTP 401) on missing header, invalid format, or unrecognized token.
    """
    if not auth_header:
        record_guardrail_block("auth_missing", tool="gateway")
        raise AuthError("Missing 'Authorization' header.")

    parts = auth_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        record_guardrail_block("auth_malformed", tool="gateway")
        raise AuthError("Invalid Authorization header format. Expected 'Bearer <token>'.")

    token = parts[1]
    principal = token_store.get(token)
    if not principal:
        record_guardrail_block("auth_invalid_token", tool="gateway")
        raise AuthError("Invalid or expired Bearer token.")

    return principal


def require_scope(
    principal: ClientPrincipal,
    required_scope: str,
    tool_name: str = "unknown",
) -> None:
    """Check that authenticated principal has the required scope or admin privilege.

    Fails closed with ScopeDeniedError (HTTP 403).
    """
    if "admin" in principal.scopes:
        return

    if required_scope not in principal.scopes:
        record_guardrail_block("rbac_scope_denied", tool=tool_name)
        raise ScopeDeniedError(
            required_scope=required_scope,
            provided_scopes=principal.scopes,
        )
