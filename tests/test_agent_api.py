import base64
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import app as portal
import agent_api
import oauth


@pytest.fixture
def client():
    return portal.app.test_client()


def citizen_claims():
    return {
        "sub": "citizen:9000000001",
        "role": "citizen",
        "scope": "citizen",
    }


def officer_claims():
    return {
        "sub": "officer:admin",
        "role": "officer",
        "scope": "officer",
    }


def application_payload(**overrides):
    payload = {
        "applicant_name": "Kamla Devi",
        "dob": "12/04/1956",
        "village": "Sonari Basti",
        "block": "Sonari",
        "bank_account": "123456789012",
        "ifsc": "SBIN0003077",
        "age_proof_filename": "proof.pdf",
        "age_proof_base64": base64.b64encode(b"synthetic-proof").decode("ascii"),
        "declaration_confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_scheme_information_reports_enabled_block(client, monkeypatch):
    monkeypatch.setenv("MCP_ENABLED_BLOCKS", "Sonari")

    response = client.get("/api/agent/scheme")

    assert response.status_code == 200
    assert response.json["enabled_blocks"] == ["Sonari"]
    assert response.json["monthly_amount_inr"] == 250


def test_scheme_information_defaults_to_all_portal_blocks(client, monkeypatch):
    monkeypatch.delenv("MCP_ENABLED_BLOCKS", raising=False)

    response = client.get("/api/agent/scheme")

    assert response.status_code == 200
    assert response.json["enabled_blocks"] == portal.BLOCKS


def test_application_requires_explicit_declaration(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())

    response = client.post(
        "/api/agent/citizen/applications",
        headers={"Authorization": "Bearer signed-token"},
        json=application_payload(declaration_confirmed=False),
    )

    assert response.status_code == 400
    assert "declaration" in response.json["error"]


def test_citizen_withdrawal_requires_explicit_confirmation(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())

    response = client.post(
        "/api/agent/citizen/applications/1/withdraw",
        headers={"Authorization": "Bearer test"},
        json={"confirmation_confirmed": False},
    )

    assert response.status_code == 400
    assert "explicitly confirm" in response.json["error"]


def test_citizen_application_status_includes_authenticated_applicant_name(client, monkeypatch):
    class Cursor:
        def __init__(self):
            self.fetchone_results = [
                (7, "SSP26123456", "Kamla Devi", "9000000001",
                 None, "Female", "Widowed", "Ramesh Devi", "Farmer",
                 "Sonari Basti", "Sonari", "123456789012", "SBIN0003077",
                 None, "PENDING", None, None, None),
                None,
            ]
            self.statements = []

        def execute(self, statement, params):
            self.statements.append((statement, params))

        def fetchone(self):
            return self.fetchone_results.pop(0)

        def fetchall(self):
            return [(None, "SUBMIT", "applicant:9000000001", "Application submitted")]

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.get(
        "/api/agent/citizen/application",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert response.json["applicant_name"] == "Kamla Devi"
    assert response.json["mobile"] == "9000000001"
    assert response.json["husband_name"] == "Ramesh Devi"
    assert response.json["husband_employer"] == "Farmer"
    assert response.json["village"] == "Sonari Basti"
    assert response.json["bank_account"] == "123456789012"
    assert response.json["ifsc"] == "SBIN0003077"
    assert response.json["status"] == "PENDING"
    assert response.json["age_proof_filename"] is None
    assert response.json["age_proof_available"] is False
    assert response.json["audit_trail"][0]["action"] == "SUBMIT"
    assert connection.cur.statements[0][1] == ("9000000001",)
    assert "applicant_name" in connection.cur.statements[0][0]


def test_citizen_withdrawal_is_scoped_to_authenticated_mobile(client, monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((statement, params))

        def fetchone(self):
            return ("SSP26123456",)

    class Connection:
        def __init__(self):
            self.cur = Cursor()
            self.committed = False
            self.closed = False

        def cursor(self):
            return self.cur

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("successful withdrawal must not roll back")

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.post(
        "/api/agent/citizen/applications/17/withdraw",
        headers={"Authorization": "Bearer test"},
        json={"confirmation_confirmed": True},
    )

    assert response.status_code == 200
    assert response.json["status"] == "WITHDRAWN"
    update_sql, update_params = connection.cur.calls[0]
    assert "id = %s AND mobile = %s" in update_sql
    assert update_params[1:] == (17, "9000000001")
    assert connection.cur.calls[1][1][1:3] == ("WITHDRAW", "applicant:9000000001")
    assert connection.committed
    assert connection.closed


def test_citizen_withdrawal_rejects_non_owned_or_non_pending_case(client, monkeypatch):
    class Cursor:
        def execute(self, statement, params):
            self.params = params

        def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.cur = Cursor()
            self.rolled_back = False

        def cursor(self):
            return self.cur

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.post(
        "/api/agent/citizen/applications/17/withdraw",
        headers={"Authorization": "Bearer test"},
        json={"confirmation_confirmed": True},
    )

    assert response.status_code == 409
    assert connection.rolled_back


def test_underage_applicant_is_rejected_by_portal_validation(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())

    response = client.post(
        "/api/agent/citizen/applications",
        headers={"Authorization": "Bearer signed-token"},
        json=application_payload(dob="05/10/1967"),
    )

    assert response.status_code == 400
    assert any("60 years" in error for error in response.json["validation_errors"])


def test_agent_submission_rejects_block_outside_allowlist(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())
    monkeypatch.setenv("MCP_ENABLED_BLOCKS", "Sonari")

    response = client.post(
        "/api/agent/citizen/applications",
        headers={"Authorization": "Bearer signed-token"},
        json=application_payload(block="Rajapara"),
    )

    assert response.status_code == 400
    assert response.json["enabled_blocks"] == ["Sonari"]


def test_officer_decision_requires_a_reason(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())

    response = client.post(
        "/api/agent/officer/applications/1/decision",
        headers={"Authorization": "Bearer signed-token"},
        json={"decision": "approve", "reason": " "},
    )

    assert response.status_code == 400
    assert "reason is required" in response.json["error"]


def test_citizen_token_cannot_read_officer_queue(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: citizen_claims())

    response = client.get(
        "/api/agent/officer/applications",
        headers={"Authorization": "Bearer signed-token"},
    )

    assert response.status_code == 403


def test_authorization_metadata_matches_mcp_resource_discovery(client):
    metadata = client.get("/.well-known/oauth-authorization-server").json

    assert metadata["issuer"] == oauth.ISSUER
    assert metadata["authorization_endpoint"] == oauth.ISSUER_BASE + "/oauth/authorize"
    assert metadata["token_endpoint"] == oauth.ISSUER_BASE + "/oauth/token"


def test_officer_search_binds_user_query_as_a_parameter(client, monkeypatch):
    class Cursor:
        def execute(self, statement, params):
            self.statement = statement
            self.params = params

        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.get(
        "/api/agent/officer/applications/search",
        headers={"Authorization": "Bearer test"},
        query_string={"query": "' OR 1=1 --"},
    )

    assert response.status_code == 200
    assert "' OR 1=1 --" not in connection.cur.statement
    assert "%' OR 1=1 --%" in connection.cur.params


def test_batch_decision_rejects_duplicate_ids_before_database_access(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())

    response = client.post(
        "/api/agent/officer/applications/batch-decision",
        headers={"Authorization": "Bearer test"},
        json={"application_ids": [1, 1], "decision": "approve", "reason": "Reviewed"},
    )

    assert response.status_code == 400
    assert "duplicates" in response.json["error"]


def test_flag_type_is_validated_before_database_access(client, monkeypatch):
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())

    response = client.post(
        "/api/agent/officer/applications/1/flag",
        headers={"Authorization": "Bearer test"},
        json={"flag_type": "APPROVE'; DROP TABLE applications;--", "reason": "Review"},
    )

    assert response.status_code == 400
    assert "flag_type" in response.json["error"]


def test_officer_review_includes_husband_fields_and_audit(client, monkeypatch):
    class Cursor:
        def __init__(self):
            self.fetchone_results = [
                (5, "APP-5", "Applicant", "9000000001", None, "Female", "Married",
                 "Spouse", "Employer", "Village", "Sonari", "123456789012",
                 "SBIN0003077", None, "PENDING", None, None, None),
                None,
            ]

        def execute(self, statement, params):
            self.statement = statement

        def fetchone(self):
            return self.fetchone_results.pop(0)

        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.get(
        "/api/agent/officer/applications/5",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert response.json["husband_name"] == "Spouse"
    assert response.json["husband_employer"] == "Employer"
    assert response.json["audit_trail"] == []


def test_duplicate_check_returns_exact_match_reason_and_comparison(client, monkeypatch):
    duplicate = (6, "APP-6", "Other applicant", "9000000002", "Spouse",
                "Village", "222222222222", "Sonari", "REJECTED")

    class Cursor:
        def __init__(self):
            self.fetchone_calls = 0
            self.last_sql = ""

        def execute(self, statement, params):
            self.last_sql = statement

        def fetchone(self):
            self.fetchone_calls += 1
            return (5, "APP-5", "Applicant", "9000000001", "Spouse",
                    "Village", "111111111111", "Sonari", "PENDING")

        def fetchall(self):
            if "mobile = %s" in self.last_sql:
                return [duplicate]
            return []

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)

    response = client.get(
        "/api/agent/officer/applications/5/duplicates",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert response.json["comparison"]["husband_name"] == "Spouse"
    assert response.json["matches"][0]["application_id"] == 6
    assert response.json["matches"][0]["matched_on"] == ["mobile"]
    assert "not proof" in response.json["interpretation"]


def test_queue_summary_includes_separate_all_time_status_counts(client, monkeypatch):
    class Cursor:
        def execute(self, statement, params):
            self.statement = statement
            self.params = params

        def fetchone(self):
            return (10, 2, 3, 4, 5, 100, 200, 300, 400)

    class Connection:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(agent_api, "verify_token", lambda token, audience: officer_claims())
    monkeypatch.setattr(portal, "get_db", lambda: connection)
    monkeypatch.setenv("MCP_ENABLED_BLOCKS", "Sonari,Rajapara")

    response = client.get(
        "/api/agent/officer/queue-summary",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert response.json["all_time_status_breakdown"] == {
        "APPROVED": 100,
        "DEEMED_APPROVED": 200,
        "REJECTED": 300,
        "WITHDRAWN": 400,
    }
    assert response.json["approved_this_month"] == 3
    assert "status = 'DEEMED_APPROVED'" in connection.cur.statement
    assert "status = 'WITHDRAWN'" in connection.cur.statement
    assert connection.cur.params[1] == ["Sonari", "Rajapara"]


@pytest.mark.parametrize(
    "issuer",
    ["http://localhost:8000", "http://localhost:8000/"],
)
def test_portal_verifies_current_and_legacy_issuer(monkeypatch, issuer):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(oauth, "signing_key", lambda: ("test", private_key))
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "citizen:9000000001",
            "aud": "http://localhost:8001/mcp",
            "scope": "citizen",
            "role": "citizen",
            "exp": int(time.time()) + 60,
        },
        private_key,
        algorithm="RS256",
    )

    claims = oauth.verify_token(token, "http://localhost:8001/mcp")

    assert claims["iss"] == issuer
