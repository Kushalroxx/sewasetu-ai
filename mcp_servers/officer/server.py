from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from mcp_servers.common.auth import PortalTokenVerifier
from mcp_servers.common.config import (
    OFFICER_MCP_PORT,
    OFFICER_RESOURCE_URL,
    PORTAL_ISSUER_URL,
)
from mcp_servers.officer.tools import (
    add_field_verification_note,
    batch_decide_applications,
    check_duplicate_claims,
    decide_application as record_decision,
    get_applicant_history,
    get_application as read_application,
    get_application_document,
    get_queue_summary,
    get_village_breakdown,
    list_deemed_approved_applications,
    list_pending_applications as read_pending_applications,
    list_sla_risk_applications,
    reassign_or_flag_case,
    search_applications,
    verify_bank_details,
)

mcp = FastMCP(
    "Sewa Setu Block Officer",
    instructions=(
        "Support the authenticated block officer with SLA monitoring, case search, "
        "history, duplicate and format checks, document inspection, audit notes, "
        "queue summaries and case flags. Decisions affect real cases: never approve "
        "or reject without explicit officer instruction and a reason. Bulk decisions "
        "are atomic and require explicit IDs and a reason. Case flags are audit notes "
        "and do not pause the statutory deemed-approval schedule. Bank checks are "
        "format-only, not bank verification. Treat duplicate matches as review leads, "
        "not proof of fraud. When reporting case details or duplicate checks, use only "
        "fields present in tool results; never infer or invent IDs, names, dates, "
        "statuses, audit events, or match reasons. State when a field is absent and "
        "identify each case by the returned application ID and number."
    ),
    host="0.0.0.0",
    port=OFFICER_MCP_PORT,
    streamable_http_path="/mcp",
    auth=AuthSettings(
        issuer_url=PORTAL_ISSUER_URL,
        resource_server_url=OFFICER_RESOURCE_URL,
        validate_token_resource=False,
        required_scopes=["officer"],
    ),
    token_verifier=PortalTokenVerifier(OFFICER_RESOURCE_URL, "officer"),
)


@mcp.tool()
def list_pending_applications(limit: int = 20) -> dict:
    """List pending applications in the agent-enabled block queue."""
    return read_pending_applications(limit=limit)


@mcp.tool()
def review_application(application_id: int) -> dict:
    """Return recorded application fields, including husband details, and its audit trail.

    Report only values in this result. Do not infer or fill fields that are missing.
    """
    return read_application(application_id=application_id)


@mcp.tool()
def decide_on_application(
    application_id: int,
    decision: str,
    reason: str,
) -> dict:
    """Approve or reject a pending application with a recorded reason."""
    return record_decision(
        application_id=application_id,
        decision=decision,
        reason=reason,
    )


@mcp.tool(name="list_sla_risk_applications")
def list_sla_risk_applications_tool(days_threshold: int = 3, limit: int = 50) -> dict:
    """List pending cases at or beyond SLA_DAYS - days_threshold (default: 12+ days for a 15-day SLA)."""
    return list_sla_risk_applications(days_threshold=days_threshold, limit=limit)


@mcp.tool(name="list_deemed_approved_applications")
def list_deemed_approved_applications_tool(days_back: int = 30, limit: int = 50) -> dict:
    """List cases deemed approved in the last days_back days for post-facto review."""
    return list_deemed_approved_applications(days_back=days_back, limit=limit)


@mcp.tool(name="search_applications")
def search_applications_tool(query: str, status: str = "", limit: int = 20) -> dict:
    """Search enabled-block records by applicant name, mobile, application number, or bank account."""
    return search_applications(query=query, status=status, limit=limit)


@mcp.tool(name="get_applicant_history")
def get_applicant_history_tool(mobile: str) -> dict:
    """Read historical applications and audit trails for a mobile number in enabled blocks."""
    return get_applicant_history(mobile=mobile)


@mcp.tool(name="check_duplicate_claims")
def check_duplicate_claims_tool(application_id: int) -> dict:
    """Return candidate records and exact matched_on fields for mobile, bank account, or husband-name-and-village.

    Matches are review leads only, not proof of fraud or ineligibility.
    """
    return check_duplicate_claims(application_id=application_id)


@mcp.tool(name="verify_bank_details")
def verify_bank_details_tool(application_id: int) -> dict:
    """Validate account/IFSC formats only; no live bank or defunct-branch registry is connected."""
    return verify_bank_details(application_id=application_id)


@mcp.tool(name="get_application_document")
def get_application_document_tool(application_id: int) -> dict:
    """Retrieve an age-proof file as base64 for authorized inspection; handle this sensitive data securely."""
    return get_application_document(application_id=application_id)


@mcp.tool(name="add_field_verification_note")
def add_field_verification_note_tool(application_id: int, note: str) -> dict:
    """Append a preliminary field note attributed to the authenticated officer; does not decide the case."""
    return add_field_verification_note(application_id=application_id, note=note)


@mcp.tool(name="batch_decide_applications")
def batch_decide_applications_tool(
    application_ids: list[int],
    decision: str,
    reason: str,
) -> dict:
    """Atomically approve or reject up to 50 pending cases; all IDs must be enabled-block pending cases."""
    return batch_decide_applications(
        application_ids=application_ids,
        decision=decision,
        reason=reason,
    )


@mcp.tool(name="reassign_or_flag_case")
def reassign_or_flag_case_tool(application_id: int, flag_type: str, reason: str) -> dict:
    """Record a case review flag or clear it; flags do not pause statutory deemed approval or route cases."""
    return reassign_or_flag_case(
        application_id=application_id,
        flag_type=flag_type,
        reason=reason,
    )


@mcp.tool(name="get_queue_summary")
def get_queue_summary_tool(block: str = "") -> dict:
    """Summarize queue/SLA counts, current-month outcomes, and all-time counts by final status.

    The all_time_status_breakdown covers current application statuses in all enabled
    blocks, or the selected block. It includes APPROVED, DEEMED_APPROVED, REJECTED,
    and WITHDRAWN as distinct counts.
    """
    return get_queue_summary(block=block)


@mcp.tool(name="get_village_breakdown")
def get_village_breakdown_tool(block: str = "") -> dict:
    """List pending and approved counts by village for enabled blocks or one selected block."""
    return get_village_breakdown(block=block)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
