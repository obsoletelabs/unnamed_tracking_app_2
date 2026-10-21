# Authentication protection

Authentication quotas use the application's PostgreSQL `auth_request_limits` table. They apply across backend processes and replicas sharing that database, `SECRET_KEY` and configuration. No Redis service or schema migration is required. Apply the normal application migrations before serving requests.

## Default limits

The windows below are fixed UTC windows calculated from the database clock, so clock skew between workers cannot create extra budgets. At a window boundary a fresh budget becomes available. There is no permanent account lockout. A burst can straddle adjacent windows; the longer login window limits repeated short bursts.

| Operation | Caller limit | Additional limit |
| --- | --- | --- |
| Local sign-in | 120 requests/minute; 50 failed or in-flight attempts/15 minutes | 8 failed or in-flight attempts/minute and 20/15 minutes, both for the normalized submitted identifier and the resolved account ID |
| OIDC initiation | 60/5 minutes | Default and named-provider routes share a budget |
| OIDC callback | 120/5 minutes | Default and named-provider routes share a budget |
| Recovery request | 30/hour | 5/hour per normalized submitted identifier, including missing or ineligible accounts |
| Recovery completion; invitation acceptance | 30/15 minutes each | Existing one-use token, eligibility and password checks remain in force |
| Account administration/password-policy writes | 120/hour | 100/day per administrator |
| Invitation issuance/reissue | Administration quota for HTTP admin actions | Existing 100/day per issuer and 5/day per recipient also apply to plugin gateway issuance |
| Profile changes | 120/hour | 60/hour per account |
| API key creation | 60/hour | 10/hour per account; browser session still required |
| Failed browser/API/delegated/management credential checks | 300/minute | One shared failed-check budget per caller, across supported credential namespaces |
| Integration consent creation, client registration, decisions and credential rotation | 60/5 minutes | Decisions and rotations additionally allow 60/hour per account |
| Integration credential exchange polling | 3,600/5 minutes | Existing verifier and one-use exchange checks remain in force |
| Public-app session creation | 120/5 minutes | Existing app-specific identity/permission checks remain in force |
| Steam sign-in/OpenID | 60/5 minutes | 30/5 minutes per account |
| Steam sign-in polling | 3,600/5 minutes | 600/5 minutes per account |
| First-run setup submission | 10/hour | Existing first-user-only transaction and setup lock remain in force |

Successful local sign-ins refund their failed-attempt reservations. Username and email aliases still charge the same resolved-account budget on failure. Unknown and inactive accounts perform dummy password verification and return the same invalid-credentials message. Successful credential checks and permission denials after valid authentication refund the failed-check reservation. Other endpoint quotas count all requests reaching their check, including malformed requests; retries and polling therefore still consume their endpoint budget.

These are reservations: concurrent requests count before expensive work begins. A cancelled request or crashed process can leave a reservation until its window expires. Expired rows are removed in bounded batches during subsequent checks. Login handling and password work also have process-local capacity bounds; those bounds protect resources and do not replace the shared quotas.

## Caller addresses and proxies

The application reads the ASGI server's effective peer address. It does not trust caller-supplied `X-Real-IP` or forwarded headers itself. IPv4 uses the exact address, IPv4-mapped IPv6 is normalized to IPv4, and IPv6 shares a `/64` budget to cover privacy addresses. Missing/invalid peers share an `unknown` budget. Large shared NATs may need higher caller thresholds, while account limits remain independently enforced.

The supplied production Nginx overwrites `X-Forwarded-For` with its validated `$remote_addr`; Uvicorn trusts that local proxy. With an external reverse proxy, configure **Setup → Server / Nginx** real-IP header and trusted proxy ranges to match that deployment. Trust only actual proxy addresses. The outer proxy must sanitize its incoming forwarded headers; keep the application port inaccessible to untrusted peers when using a private proxy hop.

For a custom standalone ASGI deployment, configure Uvicorn's proxy-header trust for the actual reverse proxy and supply a sanitized `X-Forwarded-For`. Sending only `X-Real-IP` does not change the peer. An incorrect proxy configuration combines callers into a proxy's budget or, if overly broad, lets callers spoof addresses. The network boundary is a deployment responsibility.

## Overrides and capacity

These settings are environment-only and hidden from setup. Restart every backend worker after changing them; use identical values across replicas.

| Variable | Default | Accepted range | Effect |
| --- | --- | --- | --- |
| `AUTH_RATE_LIMIT_PERCENT` | `100` | 10–10,000 | Scales every quota ceiling, rounding up; 200 doubles the documented thresholds. Does not scale windows, body limits or password worker counts. |
| `AUTH_LOGIN_ACCOUNT_PER_MINUTE` | `8` | 1–100,000 | Base minute ceiling for both submitted identifier and resolved account. |
| `AUTH_LOGIN_ACCOUNT_PER_15_MINUTES` | `20` | 1–100,000 | Base longer-window ceiling for both login account dimensions. |
| `AUTH_LOGIN_IP_PER_MINUTE` | `120` | 1–100,000 | Base unconditional login caller request ceiling. |
| `AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES` | `50` | 1–1,000,000 | Base failed/in-flight login caller ceiling. |
| `AUTH_PASSWORD_WORKERS` | `2` | 1–16 | Maximum concurrent password verification/hash threads per backend process. |

For example, a large NAT deployment can raise `AUTH_LOGIN_IP_PER_MINUTE` and `AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES` while retaining the default account ceilings. There is no disable switch or in-memory fallback. Raising the percentage affects recovery, invitations and credential-check budgets too; prefer the specific login controls when only shared-NAT sign-in needs adjustment.

Each backend process has a separate quota pool of **two PostgreSQL connections**, no overflow, and a two-second checkout timeout, in addition to the normal application pool. Budget PostgreSQL connection capacity for every worker/replica. Each quota transaction has a three-second overall deadline, a two-second statement timeout and a 0.5-second row-lock timeout. Keeping counters separate prevents a request holding a business connection from starving its own quota check/refund.

Password work uses Argon2id (see [Password hashing](#password-hashing)) in a bounded thread pool. Excess work receives a retry response instead of joining an unbounded queue. Cancellation keeps a slot occupied until its actual hashing thread finishes. Local login handlers holding user rows are separately limited to `min(4, AUTH_PASSWORD_WORKERS)` per process. Increasing workers also increases concurrent hashing memory/CPU demand; size this for the deployment.

Known authentication submissions require a declared body of at most **32 KiB**, including authenticated callers. Chunked/unsized authentication submissions and invalid Content-Length values return 413. Rejected bodies drain at most 1 MiB/five seconds before the connection is closed. Local sign-in and explicit new/current password fields accept at most 1,024 characters. General authenticated media/document uploads keep their existing separate limits.

## Password hashing

New passwords (setup, admin-created accounts, invitations, password changes and recovery, and the
unusable placeholder passwords of single sign-on accounts) are hashed with Argon2id. Each stored
hash is tagged `argon2id$memory$iterations$lanes$salt$digest`, so its parameters travel with it.
Accounts created before this policy keep their `scrypt$...` hashes and sign in as before. After a
successful sign-in, a hash that is legacy or weaker than the current policy is replaced under the
same row lock that already serializes sign-ins and password changes, so a concurrent change is never
overwritten. If the hashing pool is busy the sign-in still succeeds and the upgrade waits for the next
one. Dormant accounts stay on scrypt until they sign in; nobody is forced to reset a password.

| Variable | Default | Range | Meaning |
| --- | --- | --- | --- |
| `ARGON2_MEMORY_KIB` | `19456` | 8192–262144 | Memory per hash (19 MiB is the OWASP minimum for `t=2`, `p=1`) |
| `ARGON2_TIME_COST` | `2` | 1–10 | Iterations |
| `ARGON2_PARALLELISM` | `1` | 1–8 | Lanes |

The defaults take about 40 ms on a development machine. Memory use scales with
`AUTH_PASSWORD_WORKERS` (each concurrent hash holds `ARGON2_MEMORY_KIB`), so raise memory with that in
mind. Raising a setting upgrades accounts on their next sign-in. Stored parameters are bounded when
verifying (256 MiB, 10 iterations, 8 lanes), so a corrupt or hostile record is rejected rather than
allowed to exhaust the server; unknown or malformed records never verify.

## Responses and diagnostics

A quota denial returns **429**, integer-seconds `Retry-After`, `Cache-Control: no-store`, and the constant detail `Too many authentication requests. Try again later.` Wait at least that duration before retrying. The retry is the longest blocked window involved in that decision. No sign-in session/cookie is issued for a denied request. Recovery acknowledgements remain identical for unknown, disabled, SSO and ineligible accounts until their shared submitted-identifier quota is exhausted; exhaustion returns the same 429 for each.

Unavailable, saturated or locked quota storage fails closed with **503**, `Retry-After: 5`, and a generic protection-unavailable message. Saturated password capacity returns **503**, `Retry-After: 1`. A failed successful-attempt refund also fails closed; a login may have created an undisclosed server-side session row, but the failed response issues no cookie. Normal sessions/keys remain stored and usable once protection recovers. Health endpoints stay available.

The Plugin API gateway retains its versioned error envelope: quota denials use `rate_limited`/429 and capacity/storage failures use `unavailable`/503, with `Retry-After`. Clients must handle these responses rather than retrying immediately.

Counter keys use HMAC-SHA256 with the deployment secret; submitted usernames, emails, tokens and raw IPs are not stored in the quota table. Aggregate warning messages contain only fixed operation/outcome labels and counts, at most once per minute for each pair. Administrator-only `GET /api/settings/system-info` exposes `authentication_checks`, containing allowed, limited, unavailable, refunded and busy counts for this process since restart. These diagnostic counters are not cross-worker totals or the enforcement store; concurrent in-flight reservations can also make allowed minus refunded temporarily positive. They contain no account/IP labels. Existing account audit and credential policies remain separate.

## Verification

With a dedicated migrated development database:

```bash
docker compose exec -e PYTHONPATH=/app backend python -m pytest \
  tests/test_auth_rate_limits.py tests/test_auth_policy.py \
  tests/test_auth_throttle_http.py tests/test_request_bodies.py \
  tests/test_oidc_callback.py -q
```

The PostgreSQL tests exercise concurrent transactions and separate subprocesses with deliberately different local clocks, quota expiry/refunds, lock/store failures and recovery, exhausted business-pool connections, and cancellation-safe password capacity. HTTP tests exercise generic login/recovery responses, username/email aliases, real credentials across namespaces, valid-auth permission denials, OIDC checks before provider work, IPv6 grouping, spoofed headers, key issuance, and body/time bounds. OIDC completion tests still provision and persist real session rows with a mocked identity-provider transport.
