import base64

from mcp.server.auth.middleware.auth_context import get_access_token

from mcp_servers.common.errors import AuthorizationError
from mcp_servers.common.portal_client import PortalClient


def _access_token() -> str:
    access = get_access_token()
    if access is None:
        raise AuthorizationError("Sign in to Sewa Setu before using this tool.")
    return access.token


def get_scheme_information() -> dict:
    """Read official, current scheme rules from the Sewa Setu portal."""
    return PortalClient().request("GET", "/api/agent/scheme")


def get_application_status() -> dict:
    """Check the application owned by the authenticated citizen."""
    return PortalClient().request(
        "GET",
        "/api/agent/citizen/application",
        _access_token(),
    )


def withdraw_application(application_id: int, confirmation_confirmed: bool) -> dict:
    """Withdraw the authenticated citizen's own pending application after explicit confirmation."""
    return PortalClient().request(
        "POST",
        f"/api/agent/citizen/applications/{application_id}/withdraw",
        _access_token(),
        json_body={"confirmation_confirmed": confirmation_confirmed},
    )


def submit_pension_application(
    *,
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
    """Submit through portal validation, without evaluating age-proof authenticity.

    The supplied file is opaque to this tool. The portal validates presence,
    filename extension, base64 encoding, and size; an authorized officer reviews
    its contents and evidentiary value.
    """
    data = {
        "applicant_name": applicant_name,
        "dob": date_of_birth,
        "village": village,
        "block": block,
        "bank_account": bank_account,
        "ifsc": ifsc,
        "gender": gender,
        "marital_status": marital_status,
        "husband_name": husband_name,
        "age_proof_filename": age_proof_filename,
        "age_proof_base64": age_proof_base64,
        "declaration_confirmed": declaration_confirmed,
    }
    return PortalClient().request(
        "POST",
        "/api/agent/citizen/applications",
        _access_token(),
        json_body=data,
    )


def base64_document(contents: bytes) -> str:
    """Encode file contents for MCP clients that need a local helper."""
    return base64.b64encode(contents).decode("ascii")
