import base64
import binascii
import mimetypes
import os
import re
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, request

import app as portal
from oauth import verify_token

agent_api = Blueprint("agent_api", __name__)
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def enabled_blocks():
    configured = os.getenv(
        "MCP_ENABLED_BLOCKS",
        "Sonari,Rajapara,Dhemaji Pathar,Borgaon,Namti,Khelua",
    )
    blocks = [block.strip() for block in configured.split(",") if block.strip()]
    invalid = [block for block in blocks if block not in portal.BLOCKS]
    if invalid or not blocks:
        raise RuntimeError("MCP_ENABLED_BLOCKS must contain valid portal blocks.")
    return list(dict.fromkeys(blocks))


def _authorized(role):
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, (jsonify(error="A Sewa Setu access token is required."), 401)

    resource = os.getenv(
        "CITIZEN_RESOURCE_URL" if role == "citizen" else "OFFICER_RESOURCE_URL",
        "http://localhost:8001/mcp" if role == "citizen"
        else "http://localhost:8002/mcp",
    ).rstrip("/")
    claims = verify_token(token, resource)
    if not claims:
        return None, (jsonify(error="The access token is invalid or expired."), 401)

    scopes = claims.get("scope", "")
    scopes = scopes.split() if isinstance(scopes, str) else scopes
    if claims.get("role") != role or role not in scopes:
        return None, (jsonify(error="This access token cannot perform this action."), 403)

    subject = claims.get("sub", "")
    prefix = "citizen:" if role == "citizen" else "officer:"
    identity = subject.removeprefix(prefix) if subject.startswith(prefix) else ""
    if not identity:
        return None, (jsonify(error="The access token has an invalid subject."), 403)
    if role == "citizen" and not re.fullmatch(r"\d{10}", identity):
        return None, (jsonify(error="The citizen identity is invalid."), 403)
    return (claims, identity), None


def _json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify(error="A JSON object is required."), 400)
    return data, None


def _format_date(value):
    return value.isoformat() if value else None


def _officer_context():
    auth, error = _authorized("officer")
    if error:
        return None, None, error
    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return None, None, (jsonify(error=str(exc)), 500)
    return auth[1], blocks, None


def _bounded_int(value, name, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, (jsonify(error=f"{name} must be an integer between {minimum} and {maximum}."), 400)
    if not minimum <= parsed <= maximum:
        return None, (jsonify(error=f"{name} must be an integer between {minimum} and {maximum}."), 400)
    return parsed, None


def _requested_block(blocks):
    block = (request.args.get("block") or "").strip()
    if block and block not in blocks:
        return None, (jsonify(error="block must be one of the agent-enabled blocks.", enabled_blocks=blocks), 400)
    return ([block] if block else blocks), None


def _audit_rows(cur, application_id):
    cur.execute(
        "SELECT at, action, actor, note FROM audit_log "
        "WHERE application_id = %s ORDER BY at ASC, id ASC",
        (application_id,),
    )
    return [
        {
            "at": _format_date(row[0]),
            "action": row[1],
            "actor": row[2],
            "note": row[3],
        }
        for row in cur.fetchall()
    ]


def _active_case_flag(cur, application_id):
    cur.execute(
        "SELECT action, note FROM audit_log "
        "WHERE application_id = %s AND action IN ('CASE_FLAGGED', 'CASE_FLAG_CLEARED') "
        "ORDER BY id DESC LIMIT 1",
        (application_id,),
    )
    row = cur.fetchone()
    if row and row[0] == "CASE_FLAGGED":
        flag_type, _, reason = (row[1] or "").partition(": ")
        return {"type": flag_type, "reason": reason}
    return None


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@agent_api.get("/api/agent/scheme")
def scheme_information():
    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500
    return jsonify({
        "scheme_name": "Old Age Pension",
        "monthly_amount_inr": 250,
        "minimum_age": portal.MIN_AGE,
        "residency": "Resident of Purvanchal",
        "enabled_blocks": blocks,
        "application_window_closes_at": _format_date(portal.SCHEME_DEADLINE),
        "requirements": [
            "Applicant is at least 60 years old",
            "Required personal and address details",
            "Bank account number; IFSC is validated when provided",
            "Age-proof document in PDF, JPG or PNG format, up to 5 MB",
            "Applicant's declaration",
            "No active application exists for the verified mobile number",
        ],
        "important_note": (
            "The portal validates and records every application. Scheme information "
            "is informational and is not a guarantee of eligibility or approval."
        ),
    })


@agent_api.get("/api/agent/citizen/application")
def citizen_application_status():
    auth, error = _authorized("citizen")
    if error:
        return error
    _, mobile = auth
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, dob, gender, "
            "marital_status, husband_name, husband_employer, village, block, "
            "bank_account, ifsc, doc_path, status, submitted_at, decided_at, "
            "decided_by FROM applications WHERE mobile = %s "
            "ORDER BY submitted_at DESC NULLS LAST LIMIT 1",
            (mobile,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(error="No application was found for this mobile number."), 404
        cur.execute(
            "SELECT note FROM audit_log WHERE application_id = %s "
            "AND action IN ('APPROVED', 'REJECTED', 'DEEMED_APPROVED') "
            "ORDER BY at DESC, id DESC LIMIT 1",
            (row[0],),
        )
        reason_row = cur.fetchone()
        document_path = row[13]
        audit_trail = _audit_rows(cur, row[0])
        return jsonify({
            "application_id": row[0],
            "application_number": row[1],
            "applicant_name": row[2],
            "mobile": row[3],
            "date_of_birth": _format_date(row[4]),
            "gender": row[5],
            "marital_status": row[6],
            "husband_name": row[7],
            "husband_employer": row[8],
            "village": row[9],
            "block": row[10],
            "bank_account": row[11],
            "ifsc": row[12],
            "age_proof_filename": os.path.basename(document_path) if document_path else None,
            "age_proof_available": bool(document_path and os.path.isfile(document_path)),
            "status": row[14],
            "submitted_at": _format_date(row[15]),
            "decided_at": _format_date(row[16]),
            "decided_by": row[17],
            "decision_note": reason_row[0] if reason_row else None,
            "audit_trail": audit_trail,
        })
    finally:
        conn.close()


@agent_api.post("/api/agent/citizen/applications/<int:application_id>/withdraw")
def withdraw_citizen_application(application_id):
    auth, error = _authorized("citizen")
    if error:
        return error
    _, mobile = auth
    data, error = _json_object()
    if error:
        return error
    if data.get("confirmation_confirmed") is not True:
        return jsonify(error="The citizen must explicitly confirm withdrawal."), 400

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE applications SET status = 'WITHDRAWN', decided_at = %s, "
            "decided_by = 'APPLICANT' WHERE id = %s AND mobile = %s "
            "AND status = 'PENDING' RETURNING application_no",
            (datetime.now(), application_id, mobile),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify(
                error="No pending application belonging to this citizen was found with that ID."
            ), 409
        portal.write_audit(cur, application_id, "WITHDRAW", f"applicant:{mobile}")
        conn.commit()
        return jsonify({
            "application_id": application_id,
            "application_number": row[0],
            "status": "WITHDRAWN",
            "message": "The application was withdrawn. The citizen may submit a new application.",
        })
    finally:
        conn.close()


@agent_api.post("/api/agent/citizen/applications")
def submit_citizen_application():
    auth, error = _authorized("citizen")
    if error:
        return error
    _, mobile = auth
    data, error = _json_object()
    if error:
        return error
    if data.get("declaration_confirmed") is not True:
        return jsonify(error="The applicant must review and confirm the declaration."), 400

    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500
    block = (data.get("block") or "").strip()
    if block not in blocks:
        return jsonify(
            error="Agent applications are not enabled for this block.",
            enabled_blocks=blocks,
        ), 400

    filename = data.get("age_proof_filename")
    encoded_document = data.get("age_proof_base64")
    if not isinstance(filename, str) or not isinstance(encoded_document, str):
        return jsonify(error="An age-proof filename and base64 document are required."), 400
    extension = os.path.splitext(filename)[1].lower()
    if extension not in DOCUMENT_EXTENSIONS:
        return jsonify(error="Age proof must be a PDF, JPG or PNG file."), 400
    if len(encoded_document) > ((MAX_DOCUMENT_BYTES + 2) // 3) * 4:
        return jsonify(error="Age-proof document must be 5 MB or smaller."), 413
    try:
        document = base64.b64decode(encoded_document, validate=True)
    except (binascii.Error, ValueError):
        return jsonify(error="Age-proof document is not valid base64."), 400
    if not document:
        return jsonify(error="Age-proof document cannot be empty."), 400
    if len(document) > MAX_DOCUMENT_BYTES:
        return jsonify(error="Age-proof document must be 5 MB or smaller."), 413

    errors, cleaned = portal.validate_application(data, "agent-age-proof")
    if errors:
        return jsonify(error="Application validation failed.", validation_errors=errors), 400

    conn = portal.get_db()
    document_path = None
    try:
        cur = conn.cursor()
        existing = portal.active_application(cur, mobile)
        if existing:
            conn.rollback()
            return jsonify(
                error="An active application already exists for this verified mobile number.",
                application_number=existing[1],
                status=existing[2],
            ), 409

        os.makedirs(portal.UPLOAD_DIR, exist_ok=True)
        document_path = os.path.join(
            portal.UPLOAD_DIR,
            "agent_%s%s" % (secrets.token_hex(16), extension),
        )
        with open(document_path, "wb") as document_file:
            document_file.write(document)
        cleaned["doc_path"] = document_path
        application_id, application_number = portal.create_application(
            cur,
            mobile,
            cleaned,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        if document_path and os.path.exists(document_path):
            os.remove(document_path)
        raise
    finally:
        conn.close()

    portal.send_sms(
        mobile,
        "Sewa Setu: application %s received. Track it at the official status portal."
        % application_number,
    )
    return jsonify({
        "application_id": application_id,
        "application_number": application_number,
        "status": "PENDING",
        "block": cleaned["block"],
        "submitted_at": datetime.now().isoformat(),
        "message": "The portal accepted and recorded the application.",
    }), 201


@agent_api.get("/api/agent/officer/applications")
def officer_pending_applications():
    auth, error = _authorized("officer")
    if error:
        return error
    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500

    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return jsonify(error="limit must be an integer between 1 and 50."), 400
    if not 1 <= limit <= 50:
        return jsonify(error="limit must be an integer between 1 and 50."), 400

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, application_no, applicant_name, village, block, "
            "submitted_at FROM applications WHERE status = 'PENDING' "
            "AND block = ANY(%s) ORDER BY submitted_at ASC LIMIT %s",
            (blocks, limit),
        )
        rows = cur.fetchall()
        return jsonify({
            "enabled_blocks": blocks,
            "applications": [
                {
                    "application_id": row[0],
                    "application_number": row[1],
                    "applicant_name": row[2],
                    "village": row[3],
                    "block": row[4],
                    "submitted_at": _format_date(row[5]),
                }
                for row in rows
            ],
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/<int:application_id>")
def officer_application(application_id):
    auth, error = _authorized("officer")
    if error:
        return error
    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, dob, gender, "
            "marital_status, husband_name, husband_employer, village, block, "
            "bank_account, ifsc, doc_path, status, submitted_at, decided_at, "
            "decided_by FROM applications "
            "WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        cur.execute(
            "SELECT at, action, actor, note FROM audit_log "
            "WHERE application_id = %s ORDER BY at ASC",
            (application_id,),
        )
        audit = cur.fetchall()
        return jsonify({
            "application_id": row[0],
            "application_number": row[1],
            "applicant_name": row[2],
            "mobile": row[3],
            "date_of_birth": _format_date(row[4]),
            "gender": row[5],
            "marital_status": row[6],
            "husband_name": row[7],
            "husband_employer": row[8],
            "village": row[9],
            "block": row[10],
            "bank_account": row[11],
            "ifsc": row[12],
            "age_proof_filename": os.path.basename(row[13]) if row[13] else None,
            "age_proof_available": bool(row[13] and os.path.isfile(row[13])),
            "status": row[14],
            "active_flag": _active_case_flag(cur, application_id),
            "submitted_at": _format_date(row[15]),
            "decided_at": _format_date(row[16]),
            "decided_by": row[17],
            "audit_trail": [
                {
                    "at": _format_date(item[0]),
                    "action": item[1],
                    "actor": item[2],
                    "reason": item[3],
                }
                for item in audit
            ],
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/sla-risk")
def officer_sla_risk_applications():
    _, blocks, error = _officer_context()
    if error:
        return error
    days_threshold, error = _bounded_int(request.args.get("days_threshold", 3), "days_threshold", 1, 14)
    if error:
        return error
    limit, error = _bounded_int(request.args.get("limit", 50), "limit", 1, 100)
    if error:
        return error

    age_days = portal.SLA_DAYS - days_threshold
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT a.id, a.application_no, a.applicant_name, a.mobile, a.village, "
            "a.block, a.submitted_at, "
            "GREATEST(0, EXTRACT(EPOCH FROM (NOW() - a.submitted_at)) / 86400)::int "
            "FROM applications a WHERE a.status = 'PENDING' AND a.block = ANY(%s) "
            "AND a.submitted_at <= NOW() - (%s * INTERVAL '1 day') "
            "ORDER BY a.submitted_at ASC LIMIT %s",
            (blocks, age_days, limit),
        )
        rows = cur.fetchall()
        return jsonify({
            "sla_days": portal.SLA_DAYS,
            "days_threshold": days_threshold,
            "applications": [
                {
                    "application_id": row[0],
                    "application_number": row[1],
                    "applicant_name": row[2],
                    "mobile": row[3],
                    "village": row[4],
                    "block": row[5],
                    "submitted_at": _format_date(row[6]),
                    "age_days": row[7],
                    "active_flag": _active_case_flag(cur, row[0]),
                }
                for row in rows
            ],
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/deemed-approved")
def officer_deemed_approved_applications():
    _, blocks, error = _officer_context()
    if error:
        return error
    days_back, error = _bounded_int(request.args.get("days_back", 30), "days_back", 1, 3650)
    if error:
        return error
    limit, error = _bounded_int(request.args.get("limit", 50), "limit", 1, 100)
    if error:
        return error

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT a.id, a.application_no, a.applicant_name, a.mobile, a.village, "
            "a.block, a.status, a.decided_at, a.decided_by "
            "FROM applications a WHERE a.block = ANY(%s) "
            "AND (a.status = 'DEEMED_APPROVED' OR EXISTS ("
            "SELECT 1 FROM audit_log l WHERE l.application_id = a.id "
            "AND l.action IN ('DEEMED', 'DEEMED_APPROVED'))) "
            "AND COALESCE(a.decided_at, (SELECT MAX(l.at) FROM audit_log l "
            "WHERE l.application_id = a.id AND l.action IN ('DEEMED', 'DEEMED_APPROVED'))) "
            ">= NOW() - (%s * INTERVAL '1 day') "
            "ORDER BY COALESCE(a.decided_at, a.submitted_at) DESC LIMIT %s",
            (blocks, days_back, limit),
        )
        rows = cur.fetchall()
        return jsonify({
            "days_back": days_back,
            "applications": [
                {
                    "application_id": row[0],
                    "application_number": row[1],
                    "applicant_name": row[2],
                    "mobile": row[3],
                    "village": row[4],
                    "block": row[5],
                    "status": row[6],
                    "decided_at": _format_date(row[7]),
                    "decided_by": row[8],
                }
                for row in rows
            ],
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/search")
def officer_search_applications():
    _, blocks, error = _officer_context()
    if error:
        return error
    query = (request.args.get("query") or "").strip()
    if not 2 <= len(query) <= 100:
        return jsonify(error="query must contain 2 to 100 characters."), 400
    status = (request.args.get("status") or "").strip().upper()
    allowed_statuses = {"PENDING", "APPROVED", "REJECTED", "DEEMED_APPROVED", "WITHDRAWN"}
    if status and status not in allowed_statuses:
        return jsonify(error="status is not a recognized application status."), 400
    limit, error = _bounded_int(request.args.get("limit", 20), "limit", 1, 100)
    if error:
        return error

    pattern = "%" + _escape_like(query) + "%"
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT id, application_no, applicant_name, mobile, village, block, "
            "bank_account, status, submitted_at FROM applications "
            "WHERE block = ANY(%s) AND (applicant_name ILIKE %s ESCAPE '\\' "
            "OR mobile ILIKE %s ESCAPE '\\' OR application_no ILIKE %s ESCAPE '\\' "
            "OR bank_account ILIKE %s ESCAPE '\\')"
        )
        params = [blocks, pattern, pattern, pattern, pattern]
        if status:
            sql += " AND status = %s"
            params.append(status)
        sql += " ORDER BY submitted_at DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        return jsonify({
            "enabled_blocks": blocks,
            "applications": [
                {
                    "application_id": row[0],
                    "application_number": row[1],
                    "applicant_name": row[2],
                    "mobile": row[3],
                    "village": row[4],
                    "block": row[5],
                    "bank_account": row[6],
                    "status": row[7],
                    "submitted_at": _format_date(row[8]),
                }
                for row in rows
            ],
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applicants/<mobile>/history")
def officer_applicant_history(mobile):
    _, blocks, error = _officer_context()
    if error:
        return error
    if not re.fullmatch(r"\d{10,15}", mobile):
        return jsonify(error="mobile must contain 10 to 15 digits."), 400

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, dob, village, block, "
            "status, submitted_at, decided_at, decided_by FROM applications "
            "WHERE mobile = %s AND block = ANY(%s) ORDER BY submitted_at DESC",
            (mobile, blocks),
        )
        rows = cur.fetchall()
        history = []
        for row in rows:
            history.append({
                "application_id": row[0],
                "application_number": row[1],
                "applicant_name": row[2],
                "mobile": row[3],
                "date_of_birth": _format_date(row[4]),
                "village": row[5],
                "block": row[6],
                "status": row[7],
                "submitted_at": _format_date(row[8]),
                "decided_at": _format_date(row[9]),
                "decided_by": row[10],
                "audit_trail": _audit_rows(cur, row[0]),
            })
        return jsonify({"mobile": mobile, "applications": history})
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/<int:application_id>/duplicates")
def officer_duplicate_claims(application_id):
    _, blocks, error = _officer_context()
    if error:
        return error
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, husband_name, village, "
            "bank_account, block, status FROM applications "
            "WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        target = cur.fetchone()
        if not target:
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        _, _, _, mobile, husband_name, village, bank_account, _, _ = target

        matches = {}
        checks = [
            ("mobile", "mobile = %s", mobile),
            ("bank_account", "bank_account = %s", bank_account),
            ("husband_name_and_village", "LOWER(husband_name) = LOWER(%s) AND LOWER(village) = LOWER(%s)",
             (husband_name, village)),
        ]
        for match_type, condition, values in checks:
            if not values or (isinstance(values, tuple) and not all(values)):
                continue
            cur.execute(
                "SELECT id, application_no, applicant_name, mobile, husband_name, village, "
                "block, bank_account, status FROM applications WHERE id <> %s AND block = ANY(%s) "
                "AND " + condition + " ORDER BY submitted_at DESC LIMIT 100",
                (application_id, blocks, *(values if isinstance(values, tuple) else (values,))),
            )
            for row in cur.fetchall():
                match = matches.setdefault(row[0], {
                    "application_id": row[0],
                    "application_number": row[1],
                    "applicant_name": row[2],
                    "mobile": row[3],
                    "husband_name": row[4],
                    "village": row[5],
                    "block": row[6],
                    "bank_account": row[7],
                    "status": row[8],
                    "matched_on": [],
                })
                match["matched_on"].append(match_type)
        return jsonify({
            "application_id": application_id,
            "comparison": {
                "mobile": mobile,
                "husband_name": husband_name,
                "village": village,
                "bank_account": bank_account,
            },
            "matches": list(matches.values()),
            "interpretation": "Matches are review leads, not proof of fraud or ineligibility.",
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/<int:application_id>/bank-details")
def officer_verify_bank_details(application_id):
    _, blocks, error = _officer_context()
    if error:
        return error
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT bank_account, ifsc FROM applications "
            "WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        account, ifsc = row
        account_valid = bool(account and re.fullmatch(r"\d{9,18}", account))
        ifsc_valid = bool(ifsc and re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc))
        return jsonify({
            "application_id": application_id,
            "account_format_valid": account_valid,
            "ifsc_format_valid": ifsc_valid,
            "ifsc": ifsc,
            "bank_branch_status": "not_checked",
            "note": "Format checks only; no bank, NPCI, or defunct-branch registry is connected.",
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/applications/<int:application_id>/document")
def officer_application_document(application_id):
    _, blocks, error = _officer_context()
    if error:
        return error
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_path FROM applications WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        path = row[0]
        if not path:
            return jsonify(error="No age-proof document is recorded for this application."), 404

        upload_root = os.path.realpath(portal.UPLOAD_DIR)
        document_path = os.path.realpath(path)
        if os.path.commonpath((upload_root, document_path)) != upload_root:
            portal.app.logger.error("Application %s document path is outside upload directory", application_id)
            return jsonify(error="Stored document path is invalid."), 500
        if not os.path.isfile(document_path):
            return jsonify(error="The stored age-proof document is unavailable."), 404
        size = os.path.getsize(document_path)
        if size > MAX_DOCUMENT_BYTES:
            return jsonify(error="Stored document exceeds the 5 MB inspection limit."), 413
        extension = os.path.splitext(document_path)[1].lower()
        if extension not in DOCUMENT_EXTENSIONS:
            return jsonify(error="Stored document type is not supported for inspection."), 415
        with open(document_path, "rb") as document:
            content = base64.b64encode(document.read()).decode("ascii")
        return jsonify({
            "application_id": application_id,
            "filename": os.path.basename(document_path),
            "content_type": mimetypes.guess_type(document_path)[0] or "application/octet-stream",
            "size_bytes": size,
            "content_base64": content,
        })
    finally:
        conn.close()


@agent_api.post("/api/agent/officer/applications/<int:application_id>/field-note")
def officer_add_field_verification_note(application_id):
    officer, blocks, error = _officer_context()
    if error:
        return error
    data, error = _json_object()
    if error:
        return error
    if not isinstance(data.get("note"), str):
        return jsonify(error="note must be a string."), 400
    note = data["note"].strip()
    if not note or len(note) > 500:
        return jsonify(error="note is required and must be 500 characters or fewer."), 400

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM applications WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        if not cur.fetchone():
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        portal.write_audit(cur, application_id, "FIELD_VERIFICATION_NOTE", f"officer:{officer}", note)
        conn.commit()
        return jsonify({
            "application_id": application_id,
            "recorded_by": officer,
            "note": note,
            "message": "Field note recorded; it does not approve or reject the application.",
        }), 201
    finally:
        conn.close()


@agent_api.post("/api/agent/officer/applications/batch-decision")
def officer_batch_decision():
    officer, blocks, error = _officer_context()
    if error:
        return error
    data, error = _json_object()
    if error:
        return error
    ids = data.get("application_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or any(type(value) is not int or value < 1 for value in ids):
        return jsonify(error="application_ids must contain 1 to 50 positive integer IDs."), 400
    if len(set(ids)) != len(ids):
        return jsonify(error="application_ids must not contain duplicates."), 400
    if not isinstance(data.get("decision"), str) or not isinstance(data.get("reason"), str):
        return jsonify(error="decision and reason must be strings."), 400
    decision = data["decision"].strip().lower()
    reason = data["reason"].strip()
    if decision not in ("approve", "reject"):
        return jsonify(error="decision must be approve or reject."), 400
    if not reason or len(reason) > 500:
        return jsonify(error="reason is required and must be 500 characters or fewer."), 400

    status = "APPROVED" if decision == "approve" else "REJECTED"
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE applications SET status = %s, decided_at = %s, decided_by = %s "
            "WHERE id = ANY(%s) AND status = 'PENDING' AND block = ANY(%s) "
            "RETURNING id, application_no, mobile",
            (status, datetime.now(), officer, ids, blocks),
        )
        rows = cur.fetchall()
        if len(rows) != len(ids):
            conn.rollback()
            return jsonify(error="No cases were changed: every ID must be pending and in an agent-enabled block."), 409
        for row in rows:
            portal.write_audit(cur, row[0], status, f"officer:{officer}", reason)
        conn.commit()
    finally:
        conn.close()

    for _, application_number, mobile in rows:
        portal.send_sms(
            mobile,
            f"Sewa Setu: application {application_number} has been {status.lower()}.",
        )
    return jsonify({
        "status": status,
        "decided_by": officer,
        "reason": reason,
        "applications": [
            {"application_id": row[0], "application_number": row[1]}
            for row in rows
        ],
    })


@agent_api.post("/api/agent/officer/applications/<int:application_id>/flag")
def officer_flag_case(application_id):
    officer, blocks, error = _officer_context()
    if error:
        return error
    data, error = _json_object()
    if error:
        return error
    if not isinstance(data.get("flag_type"), str) or not isinstance(data.get("reason"), str):
        return jsonify(error="flag_type and reason must be strings."), 400
    flag_type = data["flag_type"].strip().upper()
    reason = data["reason"].strip()
    allowed_flags = {"NEEDS_DOC_REUPLOAD", "SUSPECTED_UNDERAGE", "ADDRESS_UNVERIFIED", "CLEAR"}
    if flag_type not in allowed_flags:
        return jsonify(error="flag_type must be NEEDS_DOC_REUPLOAD, SUSPECTED_UNDERAGE, ADDRESS_UNVERIFIED, or CLEAR."), 400
    if not reason or len(reason) > 450:
        return jsonify(error="reason is required and must be 450 characters or fewer."), 400

    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM applications WHERE id = %s AND block = ANY(%s)",
            (application_id, blocks),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(error="Application not found in the agent-enabled blocks."), 404
        if row[0] != "PENDING":
            return jsonify(error="Only pending applications can be flagged or unflagged."), 409
        if flag_type == "CLEAR":
            action = "CASE_FLAG_CLEARED"
            note = reason
        else:
            action = "CASE_FLAGGED"
            note = f"{flag_type}: {reason}"
        portal.write_audit(cur, application_id, action, f"officer:{officer}", note)
        conn.commit()
        return jsonify({
            "application_id": application_id,
            "flag": None if flag_type == "CLEAR" else {"type": flag_type, "reason": reason},
            "recorded_by": officer,
            "deemed_approval_paused": False,
            "note": "Flags are recorded for review; the statutory deemed-approval schedule is unchanged.",
        }), 201
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/queue-summary")
def officer_queue_summary():
    _, blocks, error = _officer_context()
    if error:
        return error
    selected_blocks, error = _requested_block(blocks)
    if error:
        return error
    critical_days = max(0, portal.SLA_DAYS - 3)
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE status = 'PENDING'), "
            "COUNT(*) FILTER (WHERE status = 'PENDING' AND submitted_at <= NOW() - (%s * INTERVAL '1 day')), "
            "COUNT(*) FILTER (WHERE status = 'APPROVED' AND decided_at >= date_trunc('month', NOW())), "
            "COUNT(*) FILTER (WHERE status = 'REJECTED' AND decided_at >= date_trunc('month', NOW())), "
            "COUNT(*) FILTER (WHERE status = 'DEEMED_APPROVED' AND decided_at >= date_trunc('month', NOW())), "
            "COUNT(*) FILTER (WHERE status = 'APPROVED'), "
            "COUNT(*) FILTER (WHERE status = 'DEEMED_APPROVED'), "
            "COUNT(*) FILTER (WHERE status = 'REJECTED'), "
            "COUNT(*) FILTER (WHERE status = 'WITHDRAWN') "
            "FROM applications WHERE block = ANY(%s)",
            (critical_days, selected_blocks),
        )
        row = cur.fetchone()
        return jsonify({
            "blocks": selected_blocks,
            "total_pending": row[0],
            "critical_sla_count": row[1],
            "approved_this_month": row[2],
            "rejected_this_month": row[3],
            "deemed_approved_this_month": row[4],
            "all_time_status_breakdown": {
                "APPROVED": row[5],
                "DEEMED_APPROVED": row[6],
                "REJECTED": row[7],
                "WITHDRAWN": row[8],
            },
            "sla_days": portal.SLA_DAYS,
            "critical_sla_threshold_days": critical_days,
        })
    finally:
        conn.close()


@agent_api.get("/api/agent/officer/village-breakdown")
def officer_village_breakdown():
    _, blocks, error = _officer_context()
    if error:
        return error
    selected_blocks, error = _requested_block(blocks)
    if error:
        return error
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT village, block, COUNT(*) FILTER (WHERE status = 'PENDING'), "
            "COUNT(*) FILTER (WHERE status IN ('APPROVED', 'DEEMED_APPROVED')) "
            "FROM applications WHERE block = ANY(%s) "
            "GROUP BY village, block ORDER BY block, village",
            (selected_blocks,),
        )
        rows = cur.fetchall()
        return jsonify({
            "blocks": selected_blocks,
            "villages": [
                {
                    "village": row[0],
                    "block": row[1],
                    "pending": row[2],
                    "approved": row[3],
                }
                for row in rows
            ],
        })
    finally:
        conn.close()


@agent_api.post("/api/agent/officer/applications/<int:application_id>/decision")
def officer_decision(application_id):
    auth, error = _authorized("officer")
    if error:
        return error
    _, officer = auth
    data, error = _json_object()
    if error:
        return error
    decision = (data.get("decision") or "").strip().lower()
    reason = (data.get("reason") or "").strip()
    if decision not in ("approve", "reject"):
        return jsonify(error="decision must be approve or reject."), 400
    if not reason:
        return jsonify(error="A reason is required for every officer decision."), 400
    if len(reason) > 500:
        return jsonify(error="Decision reason must be 500 characters or fewer."), 400

    try:
        blocks = enabled_blocks()
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 500
    status = "APPROVED" if decision == "approve" else "REJECTED"
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE applications SET status = %s, decided_at = %s, decided_by = %s "
            "WHERE id = %s AND status = 'PENDING' AND block = ANY(%s) "
            "RETURNING application_no, mobile",
            (status, datetime.now(), officer, application_id, blocks),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify(
                error="Application is not pending or is outside the agent-enabled blocks."
            ), 409
        actor = "officer:%s" % officer
        portal.write_audit(cur, application_id, status, actor, reason)
        conn.commit()
    finally:
        conn.close()

    portal.send_sms(
        row[1],
        "Sewa Setu: application %s has been %s."
        % (row[0], status.lower()),
    )
    return jsonify({
        "application_number": row[0],
        "status": status,
        "decided_by": officer,
        "reason": reason,
    })
