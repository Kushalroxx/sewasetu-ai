from mcp.server.auth.middleware.auth_context import get_access_token

from mcp_servers.common.errors import AuthorizationError
from mcp_servers.common.portal_client import PortalClient
from urllib.parse import quote


def _access_token() -> str:
    access = get_access_token()
    if access is None:
        raise AuthorizationError("Sign in as a block officer before using this tool.")
    return access.token


def list_pending_applications(limit: int = 20) -> dict:
    """List pending applications for the agent-enabled blocks."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/applications",
        _access_token(),
        params={"limit": limit},
    )


def get_application(application_id: int) -> dict:
    """Read recorded case fields and audit trail; do not infer fields absent from the response."""
    return PortalClient().request(
        "GET",
        f"/api/agent/officer/applications/{application_id}",
        _access_token(),
    )


def decide_application(application_id: int, decision: str, reason: str) -> dict:
    """Record an explicit officer decision with an audit reason."""
    return PortalClient().request(
        "POST",
        f"/api/agent/officer/applications/{application_id}/decision",
        _access_token(),
        json_body={"decision": decision, "reason": reason},
    )


def list_sla_risk_applications(days_threshold: int = 3, limit: int = 50) -> dict:
    """List pending cases that are within the configured number of days of deemed approval."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/applications/sla-risk",
        _access_token(),
        params={"days_threshold": days_threshold, "limit": limit},
    )


def list_deemed_approved_applications(days_back: int = 30, limit: int = 50) -> dict:
    """List cases deemed approved during the requested lookback period."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/applications/deemed-approved",
        _access_token(),
        params={"days_back": days_back, "limit": limit},
    )


def search_applications(query: str, status: str = "", limit: int = 20) -> dict:
    """Search enabled-block cases by name, mobile, application number, or bank account."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/applications/search",
        _access_token(),
        params={"query": query, "status": status, "limit": limit},
    )


def get_applicant_history(mobile: str) -> dict:
    """Read case history and audit trails for a mobile number in enabled blocks."""
    return PortalClient().request(
        "GET",
        f"/api/agent/officer/applicants/{quote(mobile, safe='')}/history",
        _access_token(),
    )


def check_duplicate_claims(application_id: int) -> dict:
    """Return candidate cases with explicit matched_on reasons; matches are not proof of fraud."""
    return PortalClient().request(
        "GET",
        f"/api/agent/officer/applications/{application_id}/duplicates",
        _access_token(),
    )


def verify_bank_details(application_id: int) -> dict:
    """Check account and IFSC formats; does not verify a live bank account."""
    return PortalClient().request(
        "GET",
        f"/api/agent/officer/applications/{application_id}/bank-details",
        _access_token(),
    )


def get_application_document(application_id: int) -> dict:
    """Read the stored age-proof file as base64 for authorized officer inspection."""
    return PortalClient().request(
        "GET",
        f"/api/agent/officer/applications/{application_id}/document",
        _access_token(),
    )


def add_field_verification_note(application_id: int, note: str) -> dict:
    """Append a field verification note attributed to the authenticated officer."""
    return PortalClient().request(
        "POST",
        f"/api/agent/officer/applications/{application_id}/field-note",
        _access_token(),
        json_body={"note": note},
    )


def batch_decide_applications(application_ids: list[int], decision: str, reason: str) -> dict:
    """Atomically decide up to 50 pending enabled-block cases."""
    return PortalClient().request(
        "POST",
        "/api/agent/officer/applications/batch-decision",
        _access_token(),
        json_body={"application_ids": application_ids, "decision": decision, "reason": reason},
    )


def reassign_or_flag_case(application_id: int, flag_type: str, reason: str) -> dict:
    """Record or clear a pending-case review flag; does not change statutory SLA processing."""
    return PortalClient().request(
        "POST",
        f"/api/agent/officer/applications/{application_id}/flag",
        _access_token(),
        json_body={"flag_type": flag_type, "reason": reason},
    )


def get_queue_summary(block: str = "") -> dict:
    """Get pending, critical-SLA, and monthly decision counts."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/queue-summary",
        _access_token(),
        params={"block": block} if block else None,
    )


def get_village_breakdown(block: str = "") -> dict:
    """Get pending and approved counts grouped by village."""
    return PortalClient().request(
        "GET",
        "/api/agent/officer/village-breakdown",
        _access_token(),
        params={"block": block} if block else None,
    )
