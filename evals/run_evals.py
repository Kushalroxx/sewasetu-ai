import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import jwt
import psycopg2
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_FILE = ROOT / "evals" / "personas.json"
RESULTS_FILE = ROOT / "evals" / "results" / "results.json"
CITIZEN_MCP_URL = os.getenv("CITIZEN_MCP_URL", "http://localhost:8001/mcp")
OFFICER_MCP_URL = os.getenv("OFFICER_MCP_URL", "http://localhost:8002/mcp")

PDF_FIXTURE = (
    b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF"
)


@asynccontextmanager
async def mcp_session(url, token):
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def call_tool(session, name, arguments=None):
    response = await session.call_tool(name, arguments or {})
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict):
        result = structured
    else:
        texts = [
            block.text
            for block in response.content
            if getattr(block, "text", None) is not None
        ]
        text = "\n".join(texts)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = {"message": text}
    return result, bool(response.isError)


def add_check(result, label, passed, detail):
    result["checks"].append({
        "check": label,
        "passed": bool(passed),
        "detail": detail,
    })


def token_claims(token):
    claims = jwt.decode(token, options={"verify_signature": False})
    subject = claims.get("sub", "")
    if not subject.startswith("citizen:"):
        raise ValueError("Evaluation citizen token has an unexpected subject.")
    return claims, subject.removeprefix("citizen:")


def query_one(database_url, query, params):
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def safe_call_record(tool, result, is_error):
    return {
        "tool": tool,
        "result": result,
        "is_error": is_error,
    }


async def evaluate_persona(persona, token, database_url):
    item = {
        "persona_id": persona["id"],
        "persona_name": persona["name"],
        "age": persona["age"],
        "village": persona["village"],
        "block": persona["block"],
        "goal": persona["goal"],
        "opening_message": persona["opening_message"],
        "how_they_talk": persona["how_she_talks"],
        "expected_portal_state": persona["expected_portal_state"],
        "conversation": [{
            "speaker": "citizen",
            "message": persona["opening_message"],
        }],
        "checks": [],
        "status": "failed",
    }
    _, mobile = token_claims(token)
    before = query_one(
        database_url,
        "SELECT count(*) FROM applications WHERE mobile = %s",
        (mobile,),
    )[0]
    add_check(
        item,
        "synthetic mobile starts with no prior applications",
        before == 0,
        f"Existing application rows before scenario: {before}",
    )
    if before:
        item["note"] = "Use a clean evaluation database and a fresh citizen identity."
        return item

    async with mcp_session(CITIZEN_MCP_URL, token) as session:
        info, error = await call_tool(session, "pension_scheme_information")
        item["conversation"].append(safe_call_record("pension_scheme_information", info, error))
        blocks = info.get("enabled_blocks", [])
        add_check(
            item,
            "scheme information identifies enabled agent blocks",
            not error and blocks == ["Sonari"],
            f"Enabled blocks returned: {blocks}",
        )

        arguments = {
            "applicant_name": persona["name"],
            "date_of_birth": persona["date_of_birth"],
            "village": persona["village"],
            "block": persona["block"],
            "bank_account": "123456789012",
            "ifsc": "SBIN0003077",
            "age_proof_filename": "synthetic-age-proof.pdf",
            "age_proof_base64": base64.b64encode(PDF_FIXTURE).decode("ascii"),
            "declaration_confirmed": True,
        }
        submitted, submit_error = await call_tool(
            session,
            "submit_pension_application",
            arguments,
        )
        item["conversation"].append(
            safe_call_record("submit_pension_application", submitted, submit_error)
        )
        if persona["id"] == "kamla-devi-eligible":
            app_id = submitted.get("application_id")
            add_check(
                item,
                "eligible Sonari application accepted",
                not submit_error and bool(app_id)
                and submitted.get("status") == "PENDING",
                "Portal tool response: %s" % submitted,
            )
            if not app_id:
                item["note"] = "Submission did not return a portal application id."
                return item
            dbrow = query_one(
                database_url,
                "SELECT mobile, status, block, doc_path FROM applications WHERE id = %s",
                (app_id,),
            )
            add_check(
                item,
                "portal database owns the application and age-proof file",
                bool(dbrow)
                and dbrow[0] == mobile
                and dbrow[1] == "PENDING"
                and dbrow[2] == "Sonari"
                and bool(dbrow[3])
                and os.path.isfile(dbrow[3]),
                "Portal row: %s" % (dbrow,),
            )
            submit_audit = query_one(
                database_url,
                "SELECT action FROM audit_log WHERE application_id = %s "
                "AND action = 'SUBMIT'",
                (app_id,),
            )
            add_check(
                item,
                "application submission is present in the audit trail",
                submit_audit == ("SUBMIT",),
                f"Submission audit row: {submit_audit}",
            )

            duplicate, duplicate_error = await call_tool(
                session,
                "submit_pension_application",
                arguments,
            )
            item["conversation"].append(
                safe_call_record(
                    "submit_pension_application (duplicate attempt)",
                    duplicate,
                    duplicate_error,
                )
            )
            count = query_one(
                database_url,
                "SELECT count(*) FROM applications WHERE mobile = %s",
                (mobile,),
            )[0]
            add_check(
                item,
                "duplicate active application is denied",
                duplicate_error and count == 1,
                f"Duplicate rejected={duplicate_error}; portal row count={count}.",
            )

            status, status_error = await call_tool(session, "check_my_application")
            item["conversation"].append(
                safe_call_record("check_my_application", status, status_error)
            )
            add_check(
                item,
                "citizen can follow the portal application",
                not status_error
                and status.get("application_number") == submitted.get("application_number"),
                "Status response: %s" % status,
            )

            item["officer_application_id"] = app_id
            item["officer_expected_actor"] = None
            item["officer_mobile"] = mobile
        else:
            no_application = query_one(
                database_url,
                "SELECT count(*) FROM applications WHERE mobile = %s",
                (mobile,),
            )[0] == 0
            expected_error = (
                "60 years" in str(submitted)
                if persona["id"] == "shanti-below-age"
                else "not enabled" in str(submitted).lower()
            )
            add_check(
                item,
                "out-of-rule request is rejected without a portal application",
                submit_error and no_application and expected_error,
                "Portal error=%s; rows for verified mobile=%s."
                % (submitted, no_application),
            )

    item["status"] = (
        "passed" if all(check["passed"] for check in item["checks"]) else "failed"
    )
    return item


async def evaluate_officer(item, token, database_url):
    claims = jwt.decode(token, options={"verify_signature": False})
    subject = claims.get("sub", "")
    if not subject.startswith("officer:"):
        raise ValueError("Evaluation officer token has an unexpected subject.")
    officer = subject.removeprefix("officer:")
    app_id = item["officer_application_id"]
    reason = "Synthetic evaluation: required fields and age-proof document reviewed."

    async with mcp_session(OFFICER_MCP_URL, token) as session:
        queue, queue_error = await call_tool(
            session,
            "list_pending_applications",
            {"limit": 20},
        )
        item["conversation"].append(
            safe_call_record("list_pending_applications", queue, queue_error)
        )
        queue_ids = {
            row.get("application_id")
            for row in queue.get("applications", [])
        }
        add_check(
            item,
            "officer queue includes the new pending Sonari case",
            not queue_error and app_id in queue_ids,
            "Case %s listed=%s." % (app_id, app_id in queue_ids),
        )

        case, case_error = await call_tool(
            session,
            "review_application",
            {"application_id": app_id},
        )
        item["conversation"].append(
            safe_call_record("review_application", case, case_error)
        )
        add_check(
            item,
            "officer can review case details and submission audit",
            not case_error
            and case.get("application_id") == app_id
            and case.get("age_proof_available")
            and any(
                audit.get("action") == "SUBMIT"
                for audit in case.get("audit_trail", [])
            ),
            "Review returned case, proof availability and audit trail.",
        )

        decision, decision_error = await call_tool(
            session,
            "decide_on_application",
            {
                "application_id": app_id,
                "decision": "approve",
                "reason": reason,
            },
        )
        item["conversation"].append(
            safe_call_record("decide_on_application", decision, decision_error)
        )
        row = query_one(
            database_url,
            "SELECT status, decided_by FROM applications WHERE id = %s",
            (app_id,),
        )
        audit = query_one(
            database_url,
            "SELECT action, actor, note FROM audit_log "
            "WHERE application_id = %s AND action = 'APPROVED' ORDER BY at DESC LIMIT 1",
            (app_id,),
        )
        add_check(
            item,
            "officer decision and reason are recorded against the officer",
            not decision_error
            and row == ("APPROVED", officer)
            and audit == ("APPROVED", "officer:" + officer, reason),
            "Application row=%s; audit row=%s." % (row, audit),
        )
        citizen_token = item.pop("evaluation_citizen_token", None)
        if citizen_token:
            async with mcp_session(CITIZEN_MCP_URL, citizen_token) as citizen_session:
                status, status_error = await call_tool(
                    citizen_session,
                    "check_my_application",
                )
                item["conversation"].append(
                    safe_call_record("check_my_application after officer decision", status, status_error)
                )
                add_check(
                    item,
                    "citizen sees officer decision through same portal record",
                    not status_error and status.get("status") == "APPROVED",
                    "Citizen status response: %s" % status,
                )

    item["status"] = (
        "passed" if all(check["passed"] for check in item["checks"]) else "failed"
    )


def write_results(results, status, note):
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_status": status,
        "note": note,
        "results": results,
    }
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")


def main():
    personas = json.loads(PERSONAS_FILE.read_text(encoding="utf-8"))
    records = [
        {
            "persona_id": persona["id"],
            "persona_name": persona["name"],
            "age": persona["age"],
            "village": persona["village"],
            "block": persona["block"],
            "goal": persona["goal"],
            "opening_message": persona["opening_message"],
            "how_they_talk": persona["how_she_talks"],
            "expected_portal_state": persona["expected_portal_state"],
            "conversation": [],
            "checks": [],
            "status": "not_run",
        }
        for persona in personas
    ]
    try:
        citizen_tokens = json.loads(os.getenv("EVAL_CITIZEN_TOKENS") or "{}")
        officer_token = os.getenv("EVAL_OFFICER_TOKEN", "")
        database_url = os.getenv("EVAL_DATABASE_URL", "")
        if not isinstance(citizen_tokens, dict):
            raise ValueError("EVAL_CITIZEN_TOKENS must be a JSON object.")
        missing = []
        if not database_url:
            missing.append("EVAL_DATABASE_URL")
        if not officer_token:
            missing.append("EVAL_OFFICER_TOKEN")
        missing_personas = [
            persona["id"]
            for persona in personas
            if not citizen_tokens.get(persona["id"])
        ]
        if missing_personas:
            missing.append(
                "citizen tokens for: " + ", ".join(missing_personas)
            )
        if missing:
            write_results(
                records,
                "not_run",
                "Real-client evals require a clean evaluation database and "
                "fresh OAuth tokens. Missing: " + "; ".join(missing),
            )
            print("Evals not run: " + "; ".join(missing))
            return 2
        async def run():
            evaluated = []
            for persona in personas:
                record = await evaluate_persona(
                    persona,
                    citizen_tokens[persona["id"]],
                    database_url,
                )
                if persona["id"] == "kamla-devi-eligible" and record.get(
                    "officer_application_id"
                ):
                    record["evaluation_citizen_token"] = citizen_tokens[persona["id"]]
                    await evaluate_officer(record, officer_token, database_url)
                evaluated.append(record)
            return evaluated

        results = asyncio.run(run())
        passed = sum(item["status"] == "passed" for item in results)
        status = "passed" if passed == len(results) else "failed"
        write_results(
            results,
            status,
            f"{passed} of {len(results)} persona evaluations passed using MCP "
            "streamable-HTTP clients and direct portal database assertions.",
        )
        print(f"Evaluation status: {status} ({passed}/{len(results)} passed)")
        print(f"Results written to: {RESULTS_FILE}")
        return 0 if status == "passed" else 1
    except (OSError, ValueError, psycopg2.Error) as exc:
        write_results(
            records,
            "failed",
            "Evaluation could not complete: %s" % exc,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
