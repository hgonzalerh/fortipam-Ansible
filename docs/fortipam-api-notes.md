# FortiPAM REST API — Working Notes

Empirically derived against FortiPAM v1.3.0 (build 862). FortiPAM is built on
the FortiOS codebase, so its API conventions mirror FortiGate's: same URL
shapes, same response envelope, same bearer-token auth, same filter syntax.
There is no Swagger/OpenAPI spec or browsable schema documentation — most
endpoint discovery has to happen by watching what the GUI does in the browser
DevTools Network tab.

## Authentication

Two mechanisms exist in principle:

1. **API user with static API key** — admin creates a user of type "API User"
   in `User Management > User List`, system generates an API key. Sent as a
   bearer token. This is the recommended path for automation. Long-lived
   unless regenerated. Regenerating in the GUI invalidates the old key
   immediately with no overlap window.

2. **JWT exchange** — introduced in FortiPAM 1.6.0. Admin creates a JWT Key
   configuration; clients sign JWTs against it and POST them to
   `/auth/jwt/login` to get a short-lived access token. Not relevant on
   versions before 1.6.0.

Username/password against `/logincheck` works but is not intended for API
automation; it returns a session cookie meant for the GUI.

### Bearer token usage

```
Authorization: Bearer <api_key>
```

The token can also be passed as a query parameter (`?access_token=<key>`) for
quick browser-based testing, but this leaks the key into browser history,
HTTP referer headers, and TLS-terminating proxy logs. Use only for ephemeral
exploration and regenerate the key afterward.

### Trusted hosts

API users have a Trusted Host field controlling which source IPs can
authenticate. Empty or misconfigured trusted hosts cause 401s from
unexpected IPs. Worth checking when an API key works from one machine but
not another — especially relevant when moving from a laptop to an AAP/AWX
execution environment.

## Response envelope

All successful CMDB and monitor endpoints return a JSON envelope of this
shape:

```json
{
  "results": [ ... ] | { ... },
  "status": "OK",
  "http_status": 200,
  "http_method": "GET",
  "vdom": "root",
  "path": "<path segment>",
  "name": "<table or endpoint name>",
  "serial": "<device serial>",
  "version": "v1.3.0",
  "build": 862
}
```

`results` is a list for collection endpoints, a dict for single-record
fetches and most `/monitor/...` endpoints. `status` is `"OK"` on success.

### Error responses

Errors return the same envelope with a non-OK status and an explanatory
`cli_error`:

```json
{
  "status": "Forbidden",
  "http_status": 403,
  "error": 2,
  "cli_error": "Insufficient permissions to get secret",
  ...
}
```

Important: the HTTP status code on the wire matches `http_status` in the
body, but the body always parses as JSON. There is no HTML error page for
authenticated API errors. If you get an HTML response, you've hit the login
flow — authentication failed silently and you're being redirected to the GUI.

## Useful endpoints

### Health/identity check

```
GET /api/v2/monitor/system/status
```

Returns model, hostname, version, build, serial. Good for verifying both
connectivity and authentication in one shot. Should be 200/OK for any
authenticated user regardless of role.

Example response:

```json
{
  "results": {
    "model_name": "FortiGate",
    "model_number": "KVM",
    "model": "FPAKVM",
    "hostname": "FPA-Central",
    "log_disk_status": "available"
  },
  "status": "success",
  "serial": "FPAVULTM26001135",
  "version": "v1.3.0",
  "build": 862
}
```

Note: `model_name` reports "FortiGate" even on FortiPAM. This reflects the
shared codebase, not a misconfiguration.

### Secrets — list and retrieve

The table is named `database` under the `secret` path. This is **not** a
category or template type — it is the literal table name for all secrets
regardless of template (SSH, Windows, database, etc.). The Jenkins example
in some Fortinet documentation uses `/api/v2/cmdb/secret/database/<id>`,
which led me to initially mis-model `database` as a category. It is not.
There is only one table.

```
GET /api/v2/cmdb/secret/database
GET /api/v2/cmdb/secret/database/<id>
```

The list endpoint returns a paginated collection. Single-record endpoint
returns one record in `results[0]`.

### Filtering

Server-side filtering with the `filter` query parameter, FortiOS-style:

```
GET /api/v2/cmdb/secret/database?filter=name==Hugo
```

The `==` operator is **exact match** (verified against v1.3.0 — searching
`filter=name==H` returns zero results, not all H-prefixed names).

Other documented operators include `!=`, `=@` (contains), `!@` (doesn't
contain), but these were not directly tested for FortiPAM.

Multiple `filter=` parameters AND together. Filter values containing
spaces or special characters need URL encoding.

### Pagination and metadata

```
?start=0&with_meta=true&vdom=root
```

`with_meta=true` returns additional metadata fields per record. `vdom=root`
selects the virtual domain (always `root` on single-VDOM deployments, which
FortiPAM appears to be by default).

## Secret record structure

Secrets are not flat — sensitive content lives in a nested `field` array
where each entry has a `name`, `type`, and `value`:

```json
{
  "id": 8,
  "name": "Hugo",
  "folder": 5,
  "folder-name": "Employee Secrets",
  "template": "Unix Account (SSH Password)",
  "field": [
    {"id": 1, "name": "Host",     "type": "target-address", "value": "10.0.0.1"},
    {"id": 2, "name": "Username", "type": "username",       "value": "root"},
    {"id": 3, "name": "Password", "type": "password",       "value": "redhat1234"}
  ],
  "user-permission": [ ... ],
  "group-permission": [ ... ],
  "permissions": {
    "user": "view",
    "checkout_status": "none",
    "approval_status": "none",
    "passwd_change": "forbidden",
    "passwd_verify": "allowed",
    "clear_credential": "allowed",
    "checkout_renew_left": 0
  },
  ...
}
```

To read a password, the consumer needs to walk `results[0].field[]` and
match by `name` (matches GUI labels) or `type` (matches FortiPAM's internal
field-type classification). The GUI shows the `name` value, so users will
think in those terms — `Password`, `Host`, `Username`. The `type` values
are more stable across templates (always lowercase, FortiPAM-defined).

The top-level record has many other fields — recording settings, RDP
policy, checkout config, approval workflow, ZTNA tags, password rotation
schedule — but for read-only secret retrieval, only the nested `field`
array matters.

### A note on the `?fieldname=<field>` query parameter

The Jenkins example uses `?fieldname=password`. On v1.3.0 this query
parameter appears to be a no-op: the same response is returned with or
without it. It may serve a function on older or newer versions, or may
interact with permission enforcement in a way I didn't observe. The
nested `field[]` structure is always present in the response either way.

## Permissions

Permission enforcement happens at two layers, and both must clear:

1. **Admin profile / access profile** — determines which API endpoints the
   user can hit. Failures here yield 403 at the endpoint level (or for some
   resources, an empty `results` list with `matched_count: 0`).

2. **Per-secret or per-folder permissions** — determines which specific
   secrets are visible and at what level (metadata only vs. password
   visible). Each secret has `user-permission` and `group-permission`
   arrays controlling this.

Permission levels include `view`, `edit`, `owner`. Folder permissions
cascade to contained secrets unless overridden.

A user can:
- Authenticate successfully (token valid)
- Hit the right endpoint (`/cmdb/secret/database/<id>`)
- Get HTTP 200 on the *list* (sees the secret exists)
- Get HTTP 403 on the *detail* with `Insufficient permissions to get secret`

This three-state outcome (auth ok / endpoint ok / record forbidden) is
common and is the most likely cause of confusing failures. An API user
created with broad admin-profile rights but no explicit secret permissions
will pass the first checks and fail on detail retrieval.

## API discoverability

There is no `/api-docs`, no `?action=schema` endpoint reliably exposed (at
least on v1.3.0), and no Swagger/OpenAPI. Approaches that work:

1. **Browser DevTools, Network tab.** Log into the GUI, perform the action
   you want to automate, and capture the resulting XHR/Fetch requests.
   Right-click → "Copy as cURL" gives the exact URL, query params, and
   headers. Strip the session cookie, substitute a bearer token. This is
   the canonical method for FortiOS-family API exploration.

2. **Known-good URL shapes.** FortiOS conventions are stable:
   `/api/v2/cmdb/<path>/<table>` for config tables and
   `/api/v2/monitor/<path>/<endpoint>` for runtime/status queries. Use
   FortiGate documentation as a starting point for likely paths and verify
   against your FortiPAM build.

3. **Already-logged-in browser session.** Once authenticated in the GUI,
   typing API URLs directly into the address bar of that browser tab
   returns JSON (cookies auth the request). Useful for hitting known
   endpoints; doesn't help discover them.

## Patterns and gotchas

**TLS certificates.** Default and demo deployments ship with self-signed
certificates. `curl -k` and `validate_certs: false` are common in
exploration. Use proper CA-issued certs and verification in production.

**`size` in collection responses.** The `size` field in list responses
appears to reflect schema field count or some internal table sizing, not
the count of records. `matched_count` is the field that actually reports
how many records matched. Don't confuse the two — I did, early on.

**Single VDOM.** FortiPAM seems to run with a single root VDOM by default
unlike multi-VDOM FortiGate. The `vdom=root` query parameter is often
implicit but can be added defensively.

**`q_origin_key` fields everywhere.** Every record has these synthetic
identifiers. They're internal to FortiOS' configuration tracking and can
be ignored when consuming the API.

**The `database` table name is confusing.** Path is `/cmdb/secret/database`.
It is not a database-template-specific subtable; it is THE secrets table.
All secret templates (SSH, Windows, FortiGate, etc.) live here. The
`template` field on each record identifies the template type.

**HTTP method matters for CMDB writes.** GET reads, POST creates, PUT
updates, DELETE removes — standard REST. Writes require additional CSRF
handling via `X-CSRFTOKEN` header when using session cookies, but not when
using bearer tokens.

## Reference: example curl invocations

Verifying connectivity and auth:

```bash
PAM=https://your-fortipam.example.com:443
export FPAM_TOKEN=<api-key>

curl -ksS -H "Authorization: Bearer $FPAM_TOKEN" \
  "$PAM/api/v2/monitor/system/status" | jq '.'
```

Listing all secrets:

```bash
curl -ksS -H "Authorization: Bearer $FPAM_TOKEN" \
  "$PAM/api/v2/cmdb/secret/database" | jq '.results[] | {id, name, template}'
```

Resolving a secret name to an ID:

```bash
curl -ksS -H "Authorization: Bearer $FPAM_TOKEN" \
  "$PAM/api/v2/cmdb/secret/database?filter=name==Hugo" \
  | jq '.results[] | {id, name}'
```

Retrieving a single secret's full record:

```bash
curl -ksS -H "Authorization: Bearer $FPAM_TOKEN" \
  "$PAM/api/v2/cmdb/secret/database/8" \
  | jq '.results[0].field'
```

Extracting just one field value from a secret:

```bash
curl -ksS -H "Authorization: Bearer $FPAM_TOKEN" \
  "$PAM/api/v2/cmdb/secret/database/8" \
  | jq -r '.results[0].field[] | select(.name=="Password") | .value'
```

## What I have not verified

- POST/PUT/DELETE on `/cmdb/secret/database` (creating, updating, deleting
  secrets via API)
- Secret check-out and check-in workflows via API
- Approval workflows via API
- Folder management endpoints (`/cmdb/secret/folder` or similar)
- Behavior of the JWT exchange flow on versions >= 1.6.0
- Whether `?fieldname=` does anything meaningful on versions other than 1.3.0
- The full set of filter operators supported beyond `==`
- Other CMDB tables under `/cmdb/secret/` if any exist (my probing only
  confirmed `database`)

Anyone extending these notes should verify against their specific FortiPAM
version, as response shapes and available endpoints have shifted across
FortiOS versions historically.
