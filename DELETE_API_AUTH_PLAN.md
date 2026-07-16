# Delete API Authentication Plan

Implementation plan for securing the public delete endpoints exposed through the Cloudflare quick tunnel.

**Do not implement yet** — this is a design backlog item to pick up later.

Related existing pieces:

- Delete HTTP API: `api.py` (`POST /delete`, `DELETE /contacts/{employee_id}`, `GET /health`)
- Tunnel: `docker-compose.yml` profile `tunnel`, forwarding to `delete-api:8080`
- Power Automate: calls `POST /delete` with `{"employeeId":"..."}`
- Operations documentation: `docs/OPERATIONS.md` and `README.md`

## Recommendation

Use a long, randomly generated application API key sent by Power Automate in an `X-API-Key` header. Validate it in FastAPI before either delete route can invoke the delete service.

This is a reasonable temporary production safeguard for one trusted caller. The free Cloudflare quick tunnel provides HTTPS transport but not Cloudflare Access authentication. The tunnel URL being difficult to discover or changing after restart is not authentication.

This approach does not replace Cloudflare Access, a WAF, rate limiting, per-user authorization, or a stable named hostname.

## Goals

1. Reject unauthenticated delete requests with HTTP `401`.
2. Allow Power Automate to authenticate when calling `POST /delete`.
3. Apply the same protection to `DELETE /contacts/{employee_id}`.
4. Keep `GET /health` unauthenticated for container health checks.
5. Fail closed if the server API key is missing or blank.
6. Never log, return, or commit the key.
7. Disable or protect the default OpenAPI documentation endpoints.

## Non-goals

- Cloudflare Access or Zero Trust policies
- Per-user authentication, OAuth, or JWTs
- Automated key rotation
- Protecting the local `admin.py delete-external-id` command
- Resolving concurrent-delete races involving the shared staging CSV

## Current risk

The quick tunnel currently exposes these destructive routes without inbound authentication:

- `POST /delete`
- `DELETE /contacts/{employee_id}`

FastAPI also exposes `/docs`, `/redoc`, and `/openapi.json` by default. Anyone who learns the tunnel URL can discover and call the delete API.

## Proposed request flow

```text
Power Automate
  POST https://<quick-tunnel-host>/delete
  X-API-Key: <secret>
  {"employeeId": "1234"}
       |
       v
Cloudflare quick tunnel (HTTPS)
       |
       v
FastAPI delete service
  1. Require API key
  2. Compare it to DELETE_API_KEY
  3. Reject invalid requests
  4. Invoke the existing delete service only after authentication
```

## Authentication design

- Environment variable: `DELETE_API_KEY`
- Request header: `X-API-Key`
- Comparison: `hmac.compare_digest`
- Missing or incorrect request key: HTTP `401` with a generic `Unauthorized` response
- Missing or blank configured key: fail API startup
- Health route: remain public
- Delete routes: use one shared FastAPI authentication dependency

Do not reuse `EVERBRIDGE_API_KEY`; that name is reserved for the planned outbound Everbridge API transport and represents a different credential.

The authentication dependency should be attached directly to both destructive routes. It should not exist only inside `_run_delete()`, where a future route could accidentally bypass it.

## Implementation steps

### 1. Configuration and secret handling

- Add `DELETE_API_KEY` to configuration loading.
- Add an empty, documented placeholder to `.env.example`.
- Pass the variable to the `delete-api` container without embedding it in an image or Compose file.
- Verify `.env` remains excluded from Git and the Docker build context.
- Generate the key using a cryptographically secure generator, for example:

  ```powershell
  .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- Store the real key only in:
  - The deployment host's protected `.env` or Docker secret mechanism
  - A secure Power Automate environment variable or secret-capable configuration

Avoid putting the key in documentation, source code, Teams messages, screenshots, test fixtures, or visible flow action names. Configure Power Automate secure inputs and outputs where supported so the value is hidden from run history.

### 2. FastAPI authentication dependency

In `api.py`:

- Read `DELETE_API_KEY` from validated configuration.
- Read `X-API-Key` from the incoming request using FastAPI's header/security support.
- Reject absent, blank, or mismatched values.
- Compare secrets with `hmac.compare_digest`.
- Do not include the received key in logs or exception text.
- Attach the dependency to:
  - `POST /delete`
  - `DELETE /contacts/{employee_id}`

### 3. Fail-closed startup

During API startup, validate that `DELETE_API_KEY` is non-empty. If it is absent, log a clear configuration error and terminate startup.

Failing at startup is preferable to returning `503` from delete requests because Docker and operators can immediately see that the service is misconfigured, and the API cannot accidentally run unprotected.

### 4. Reduce API discovery

Disable FastAPI's public documentation surfaces for this service:

- `/docs`
- `/redoc`
- `/openapi.json`

If documentation must remain available, protect it separately. Disabling it reduces reconnaissance but is not a substitute for authentication.

### 5. Power Automate

Update the HTTP action that calls the tunnel:

- Method: `POST`
- URL: `https://<quick-tunnel-host>/delete`
- Header: `X-API-Key` with the secure configured value
- Body: retain the existing `{"employeeId":"..."}` payload

Configure the action's secure input/output settings where available. Avoid copying the key into ordinary Compose actions or variables that expose it in run history.

Updating a quick-tunnel URL does not rotate the API key. If the key is exposed, generate a new one and update both the server and Power Automate.

### 6. Automated tests

Add tests covering:

- Valid key allows `POST /delete` to invoke the mocked delete service.
- Valid key allows `DELETE /contacts/{employee_id}`.
- Missing key returns `401` for both routes.
- Incorrect key returns `401` for both routes.
- An empty configured server key cannot result in open access.
- `GET /health` remains available without authentication.
- Error responses and logs do not contain either configured or supplied keys.
- OpenAPI documentation endpoints are unavailable if disabled.

### 7. Documentation

Update `docs/OPERATIONS.md` and the relevant README tunnel instructions with:

- Key generation and installation steps
- Required Power Automate header
- Startup behavior when the key is missing
- Key rotation procedure
- A warning that tunnel URL secrecy is not authentication

Do not include the real key in documentation or example commands.

## Verification checklist

1. Set a generated key in the host environment.
2. Add the same key to Power Automate's protected configuration.
3. Rebuild or restart the `delete-api` and tunnel containers.
4. Confirm `GET /health` succeeds without a key.
5. Confirm both delete routes return `401` without a key.
6. Confirm both delete routes return `401` with an incorrect key.
7. Confirm an authorized test request reaches the mocked or controlled delete path.
8. Confirm the Power Automate flow succeeds with the header.
9. Confirm `/docs`, `/redoc`, and `/openapi.json` are unavailable.
10. Inspect logs and flow run history to ensure the key was not exposed.

Use a controlled test ID or mocked SFTP operation during verification to avoid an unintended production deletion.

## Threat model

The API key materially protects against:

- Internet users who discover or scan the quick-tunnel URL
- Callers who know the URL but not the secret
- Accidental unauthenticated calls

It does not protect against:

- Disclosure of the key through flow exports, logs, screenshots, or chat
- A compromised Power Automate account or deployment host
- An authorized caller deleting the wrong ID
- Application-level denial-of-service or brute-force traffic without rate limiting
- Concurrent delete requests racing over shared staging files

TLS protects the key in transit. The key should still be long and random so online guessing is impractical.

## Operational key rotation

If the key is suspected to be exposed:

1. Generate a new random key.
2. Pause the Power Automate delete flow if practical.
3. Update `DELETE_API_KEY` on the deployment host.
4. Restart the delete API.
5. Update the Power Automate secret.
6. Resume and test the flow.
7. Confirm the old key receives `401`.

This creates a short interruption because the initial implementation uses one active key. Supporting overlapping current and next keys can be added later if zero-downtime rotation becomes necessary.

## Later upgrade path

When moving beyond a quick tunnel:

1. Create a named Cloudflare Tunnel with a stable hostname.
2. Place Cloudflare Access in front of the API.
3. Use a Cloudflare Access service token or equivalent machine-to-machine policy for Power Automate.
4. Add rate limiting and appropriate edge protections.
5. Keep the application API key as defense in depth, or remove it only after verifying Cloudflare Access cannot be bypassed at the origin.

The application-level API key can coexist with Cloudflare Access, so this temporary work remains useful during the upgrade.
