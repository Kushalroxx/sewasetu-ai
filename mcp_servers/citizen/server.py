from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from mcp_servers.citizen.tools import (
    get_application_status as check_application_status,
    get_scheme_information,
    submit_pension_application as create_pension_application,
    withdraw_application as withdraw_citizen_application,
)
from mcp_servers.common.auth import PortalTokenVerifier
from mcp_servers.common.config import (
    CITIZEN_MCP_PORT,
    CITIZEN_RESOURCE_URL,
    PORTAL_ISSUER_URL,
)

mcp = FastMCP(
    "Sewa Setu Citizen",
    instructions=(
        "Help a citizen apply for or check their own Old Age Pension application. "
        "Use official portal rules and do not promise eligibility. Submission requires "
        "an age-proof file upload and explicit declaration. Treat the file as opaque: "
        "do not inspect its image or text, judge authenticity, or reject it as invalid "
        "based on its contents. The portal checks file presence, supported extension, "
        "base64 encoding, and size; authorized officers verify the evidence. Ask only "
        "that the citizen provide the required file and confirm their declaration. "
        "Withdrawing an application is irreversible: identify the citizen's pending "
        "application and obtain their explicit confirmation before calling the withdrawal tool."
    ),
    host="0.0.0.0",
    port=CITIZEN_MCP_PORT,
    streamable_http_path="/mcp",
    auth=AuthSettings(
        issuer_url=PORTAL_ISSUER_URL,
        resource_server_url=CITIZEN_RESOURCE_URL,
        validate_token_resource=False,
        required_scopes=["citizen"],
    ),
    token_verifier=PortalTokenVerifier(CITIZEN_RESOURCE_URL, "citizen"),
    max_request_body_size=8 * 1024 * 1024,
)


@mcp.tool()
def pension_scheme_information() -> dict:
    """Get current pension rules and blocks enabled for agent applications."""
    return get_scheme_information()


@mcp.tool()
def check_my_application() -> dict:
    """Get the authenticated citizen's latest full application record and audit trail.

    Returns the recorded personal, address, bank, document-availability, status,
    decision, and audit details. It does not return the age-proof file contents.
    """
    return check_application_status()


@mcp.tool()
def withdraw_application(application_id: int, confirmation_confirmed: bool) -> dict:
    """Withdraw the authenticated citizen's pending application.

    Ask the citizen to confirm this irreversible action before setting
    confirmation_confirmed to true. The application must belong to this citizen
    and still be pending.
    """
    return withdraw_citizen_application(
        application_id=application_id,
        confirmation_confirmed=confirmation_confirmed,
    )


@mcp.tool()
def submit_pension_application(
    applicant_name: str,
    date_of_birth: str,
    village: str,
    block: str,
    bank_account: str,
    age_proof_filename: str,
    age_proof_base64: str,
    declaration_confirmed: bool,
    ifsc: str = "",
    gender: str = "",
    marital_status: str = "",
    husband_name: str = "",
) -> dict:
    """Submit the authenticated citizen's application and age-proof file for officer review.

    Date of birth must be DD/MM/YYYY. Supply the age-proof file as base64 and set
    declaration_confirmed only after the citizen has reviewed the declaration.
    Do not inspect or assess the file's contents, authenticity, or evidentiary value.
    The portal checks file presence, supported extension, base64 encoding, and size;
    an authorized officer reviews the document.
    """
    return create_pension_application(
        applicant_name=applicant_name,
        date_of_birth=date_of_birth,
        village=village,
        block=block,
        bank_account=bank_account,
        age_proof_filename=age_proof_filename,
        age_proof_base64=age_proof_base64,
        declaration_confirmed=declaration_confirmed,
        ifsc=ifsc,
        gender=gender,
        marital_status=marital_status,
        husband_name=husband_name,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
