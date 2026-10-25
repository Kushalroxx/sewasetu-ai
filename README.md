# Sewa Setu — Old Age Pension Portal

Department of Social Welfare, Government of Purvanchal. Maintained in-house.

## Running

```
If you do not already have a local `.env`, copy `.env.example` to `.env` and set
`ADMIN_PASSWORD`. Then run:

```
docker compose up --build
```
```

- Portal: http://localhost:8000
- Status portal: http://localhost:8000/status (mobile number + password; the
  password is the applicant's date of birth as DDMMYYYY)
- Departmental login: http://localhost:8000/admin (user `admin`, password from
  `ADMIN_PASSWORD`)
- SMS gateway console (simulated telecom): http://localhost:8000/__gateway/ —
  OTPs and notifications appear here once "delivered" (1–4 s carrier latency).
  The gateway's inbox is also readable as JSON at
  `/__gateway/api/messages?to=<mobile>`.
- Database: Postgres, loaded from `seed/seed.sql` on first boot (~100k applications).
- Citizen MCP (streamable HTTP): http://localhost:8001/mcp
- Block officer MCP (streamable HTTP): http://localhost:8002/mcp
- Persona and evaluation dashboard: http://localhost:8000/eval-dashboard

The default MCP block allowlist includes all portal blocks: Sonari, Rajapara,
Dhemaji Pathar, Borgaon, Namti and Khelua. Set `MCP_ENABLED_BLOCKS` to a
comma-separated subset when deploying if a narrower allowlist is needed. The same server-side
allowlist gates agent application submissions, the officer queue, case lookup and
decisions. The normal web portal retains its existing block coverage.

## Deploying on a server

Set `PUBLIC_BASE_URL` to the address people will use (for example
`https://sewasetu.example.in`). It is stamped into sign-in tokens and the discovery documents
described below, so a wrong value means other applications cannot find the sign-in page. Put
the portal behind HTTPS; some applications refuse to sign in over plain HTTP.

```
PUBLIC_BASE_URL=https://sewasetu.example.in ADMIN_PASSWORD=pick-something docker compose up --build -d
```

## What the portal does

Citizen flow: mobile → OTP → personal details → address → bank details →
age-proof upload → preview and declaration → application number. A nightly
job (`scripts/deemed_approval.py`, via cron in the `scheduler` container)
deems approved any application still pending after the 15-day statutory SLA.

Rules enforced at submission, in `validate_application()` in `app/app.py`:
applicant is 60 years of age or above; required fields and an age-proof
document are present; the scheme window is open; and the mobile number holds
no other active (pending / approved / deemed-approved) application. A pending
application can be withdrawn from the status portal, after which the same
mobile may apply again.

Departmental users see a dashboard, list and filter applications, approve or
reject pending ones, and see the audit trail per application.

## Layout

```
app/          Flask application, templates, config, nightly job
dashboard/    evaluation dashboard served by the portal
evals/        synthetic personas, real-MCP-client evaluation runner and results
mcp_servers/  separate citizen and officer streamable-HTTP MCP servers
seed/         seed.sql — production data snapshot
smsgw/        simulated SMS gateway
```

## AI assistant connections

The MCP servers use Sewa Setu OAuth 2.1 bearer tokens; they do not accept OTPs,
passwords, or caller-supplied mobile identities. A citizen signs in and consents
to the citizen resource, and an officer signs in and consents to the officer
resource. Each server verifies the portal signature, issuer, audience, role and
scope. Keep the citizen and officer URLs separate in the assistant configuration.

Citizen tools:

- `pension_scheme_information` - read the current portal rules and enabled blocks.
- `submit_pension_application` - submit for the token's verified mobile; the
  portal applies its normal age, required-field, block, scheme-window, document,
  IFSC and duplicate-active-application checks. The user must explicitly confirm
  the declaration, and an age-proof file (PDF/JPG/PNG, at most 5 MB) is required.
  The citizen assistant must treat the file as opaque and must not judge its
  authenticity or evidentiary value; an authorized officer verifies the document.
- `check_my_application` - check the latest application tied to that mobile,
  including personal, address and bank fields, document availability, status,
  decision details and audit trail. The age-proof file contents are not returned.
- `withdraw_application` - withdraw a specified pending application belonging to
  the authenticated citizen. The tool requires explicit confirmation; withdrawn
  applications are recorded in the audit log and the citizen can apply again.

Officer tools:

- `list_pending_applications` - see the pending queue for enabled blocks.
- `review_application` - read the case fields and audit trail.
- `decide_on_application` - approve or reject a pending case with a required
  reason. The portal records the authenticated officer and decision in the
  existing application row and audit log.
- `list_sla_risk_applications` and `list_deemed_approved_applications` -
  review pending cases nearing the configured SLA and cases deemed approved
  recently.
- `search_applications` and `get_applicant_history` - find enabled-block cases
  by name, mobile, application number or bank account, and review history by
  mobile.
- `check_duplicate_claims` and `verify_bank_details` - find matching mobile,
  bank account or household/village records and check account/IFSC formats.
  Duplicate results list the exact matched fields and comparison values.
  Matches are review leads, not proof of fraud; bank checks do not connect to a
  live bank or defunct-branch registry. Officer responses should report only
  fields returned by these tools and must not infer missing case details.
- `get_application_document` - return an authorized case's age-proof file as
  base64. Treat the returned identity document as sensitive data.
- `add_field_verification_note` - append a note attributed to the authenticated
  officer without making a final decision.
- `batch_decide_applications` - atomically approve or reject up to 50 explicitly
  identified pending cases in enabled blocks, with a required reason.
- `reassign_or_flag_case` - add or clear review flags on pending cases. The
  current portal has no reassignment/routing workflow; flags are audit notes and
  do not suspend the statutory deemed-approval job.
- `get_queue_summary` and `get_village_breakdown` - report enabled-block
  workload and village counts. The queue summary includes current-month outcomes
  and an all-time status breakdown with `APPROVED`, `DEEMED_APPROVED`, `REJECTED`
  and `WITHDRAWN` counted separately.

An officer's program token can be issued at `/admin/tokens` with the officer MCP
resource URL as its audience. For user-consented sessions use the OAuth flow in
the assistant client; citizens and officers must never share their access tokens.
On deployment, set `PUBLIC_BASE_URL`, `CITIZEN_RESOURCE_URL` and
`OFFICER_RESOURCE_URL` to the exact HTTPS addresses used by people and MCP clients.
The audience URL must exactly match the resource used when issuing each token.

## Persona scenarios and portal data

The dashboard at `/eval-dashboard` shows the synthetic personas from
`evals/personas.json` and a live, read-only aggregate breakdown of current
application statuses from the portal database. It does not display applicant
identities or use the saved `evals/results/results.json` file as evidence. The
database totals are real portal records, not proof that an assistant completed
the persona scenarios; the page says so explicitly. Viewing the dashboard needs
no citizen or officer MCP tokens.

The optional `evals/run_evals.py` runner makes real streamable-HTTP MCP tool
calls and checks Postgres records, but it does not run a conversational model
against the personas. It creates and approves a synthetic application, so run
it only in an isolated evaluation deployment. Running that tool requires fresh
citizen and officer tokens and a disposable evaluation database; those
credentials are not needed by the dashboard. Do not put live tokens in source
control or evaluation results. The runner's existing JSON output is not used
as dashboard evidence.

## Letting other applications act for a citizen or an officer

Sewa Setu is also an **OAuth 2.1 authorization server**. A third-party application can act
for a citizen (scope `citizen`) or for a block officer (scope `officer`) once that person
signs in on the portal and approves it. Citizens sign in by mobile and OTP, officers with the
departmental login. The application never handles the OTP or the password.

- Discovery: `/.well-known/oauth-authorization-server` (RFC 8414)
- Registration: `/oauth/register` (RFC 7591), or a client ID metadata document URL as `client_id`
- Authorization: `/oauth/authorize` (authorization code with PKCE S256; the `resource` the
  application asks for becomes the token's audience, RFC 8707)
- Tokens: `/oauth/token`. Access tokens are RS256 JWTs. A sign-in is a **session with an
  absolute end**: 2 hours for a citizen, 8 hours for an officer, counted from the consent.
  Access tokens are short (20 / 30 minutes) and refresh tokens rotate within the session but
  never extend it; after it ends the person signs in again. Portals are used on shared phones
  and kiosks.
- Sign-out: `/oauth/revoke` (RFC 7009). People can also end any sign-in from the status
  portal (**Connected applications**) or, for officers, the admin dashboard.
- Keys: `/.well-known/jwks.json`

A service that receives one of these tokens verifies it with the JWKS, checks `iss` is this
portal, checks `aud` is its own canonical URL, and reads `scope`, `role` and `sub`
(`citizen:<mobile>` or `officer:<username>`).

Long-lived **program tokens** for unattended use (role officer) are issued by an
administrator at `/admin/tokens`.
