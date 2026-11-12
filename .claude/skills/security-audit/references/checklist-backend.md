# Checklist: backend (`src/backend/**`)
 
Read `main.py`, `auth.py`, `totp.py`, `secrets_box.py`, `rate_limit.py`, `rules.py`,
`models.py`, `db.py`, `config.py`, `history.py`, `rollups.py`, `stats.py`, `alerts.py`,
`seed_user.py`, `reset_2fa.py` in full. For each item, find the code that enforces it.
 
## 1. Authentication and authorization (highest priority)
 
- [ ] **Every route has a deliberate answer** to "who may call this?" (route inventory from
      step 1). A route that forgets the auth `Depends(...)` is **open** by default: look for
      these.
- [ ] The *anonymous / logged in / admin / collector* distinction is enforced on the server,
      never only in the frontend. Admin-only routes actually check the role (not just login).
- [ ] A **restricted session** (admin without 2FA while the policy is on) can really reach
      only the profile, 2FA setup and logout. Check *every* route, not just what the test
      covers, including new routes added after that feature.
- [ ] **IDOR / horizontal authorization:** routes with IDs (server, container, conversation,
      session, token, user, rule, alert) check ownership/role. In particular: listing
      sessions by public ID, deleting another user's session, another user's conversations.
- [ ] **Mass assignment:** request models do not accept fields like `is_admin`, `role`,
      `require_2fa`, `server_id`, `owner_id`. Check the Pydantic models and `**body`/`setattr`.
- [ ] The last admin cannot delete/demote themselves into a state with no admin (or this is
      intentional and documented). An admin cannot escalate another user without checks.
- [ ] The first account becomes admin: check **all** paths (setup token,
      `ALLOW_REGISTRATION`, a race between two concurrent requests, `ADMIN_BOOTSTRAP_TOKEN`
      comparison, token removal after the first account). Is the token comparison
      constant-time (`hmac.compare_digest`)?
## 2. Sessions, cookies, CSRF
 
- [ ] Session ID: from `secrets` (not `random`/`uuid1`), enough entropy, stored hashed or at
      least never returned to the browser in a response body. Rotated on privilege change
      (login, 2FA, password change).
- [ ] Cookie flags: `HttpOnly`, `Secure` (and under what conditions it is turned off: check
      that the default is not insecure), `SameSite`, `Path`, expiry.
- [ ] Expiry and logout actually delete the session on the server; logout-everywhere works.
- [ ] **`Origin`/`Sec-Fetch-Site` check** on POST/PUT/PATCH/DELETE: try to bypass it with an
      `Origin` sharing the prefix (`https://dash.example.com.evil.tld`), a different
      port/scheme, `Origin: null`, a missing `Origin` + `Sec-Fetch-Site: same-site`, an
      attacker-controlled `Host` header, `CORS_ORIGINS` with wildcards. Does the check apply
      to **all** methods and all routes (middleware or per route)? Is there an exemption for
      collector paths, and can it be abused to bypass the check on a user route?
- [ ] CORS: `allow_origins` is not `*` together with `allow_credentials=True`; no reflection
      of an arbitrary `Origin`.
## 3. Passwords, 2FA, tokens
 
- [ ] Password hashing: bcrypt/argon2 with a reasonable cost; the 72-byte limit is enforced
      (rejects, does not truncate); no passwords in logs; changing the password requires the
      old password (or a fresh session) and invalidates other sessions.
- [ ] **Timing/account enumeration:** login, reset and registration return the same responses
      and similar timing for a nonexistent email and a wrong password (dummy hash for
      nonexistent accounts?).
- [ ] **Rate limit** (`rate_limit.py`): the key (IP, email) cannot be bypassed by changing
      headers (`X-Forwarded-For` from an untrusted source), upper/lower case or whitespace in
      the email, or unicode normalization; memory is bounded (an attacker cannot inflate the
      structure with millions of keys); do the limits hold with multiple worker processes
      (per-process in-memory state = effective limit × number of processes)?
- [ ] **TOTP** (`totp.py`): constant-time comparison; ±1 window; **the ban on reusing the same
      code is atomic** (unique index/conditional UPDATE, not read-then-write); the per-account
      and per-address attempt counter is not reset by restarting the password step; the
      intermediate token (5 min) is single-use, bound to the user, and never valid as a
      session anywhere.
- [ ] **Recovery codes:** entropy, hashed, single-use *atomically*, deleted/regenerated when
      2FA is disabled.
- [ ] `secrets_box.py`: Fernet key from the environment; behaviour without a key must not
      leave secrets in plaintext; a decryption error does not leak details; the key is never
      logged.
- [ ] **Server tokens and join tokens:** generated from `secrets`, hashed (SHA-256 is fine for
      high-entropy tokens), constant-time comparison or lookup by hash, single-use and expiry
      are **atomic**, the cap on open tokens (20) holds under concurrent requests, deleting a
      server invalidates its token.
- [ ] Re-pairing an existing machine (same machine ID) requires `replace: true` on the server,
      not only in the UI; can another server's credentials be overwritten without it?
## 4. Inputs sent by the collector (untrusted)
 
- [ ] Every collector route (register, poll, enroll, ingest, helper events) is rejected when
      the request did not come through the gate (`X-Collector-Via` /
      `COLLECTOR_ACCESS=strict`); check that a client cannot send that header itself from the
      public side (nginx must overwrite/strip it, and the backend must not trust it blindly if
      the backend is reachable from other containers).
- [ ] Collector authentication: one token per server; one server's collector cannot write
      another server's data (`server_id` from the token, not from the request body!).
- [ ] **Validation:** types, lengths, ranges, strict time parsing, `sha256:` + 64 hex, max 500
      containers, names ≤128, event limits per check-in and per hour. Look for fields that
      are stored without sanitizing (fields added later often slip past).
- [ ] Request body size is limited (nginx `client_max_body_size` **and** Pydantic
      `max_length`), JSON depth/size, number of list elements.
- [ ] Idempotency/replay: a repeated check-in does not create duplicate incidents or unbounded
      table growth. Hostile data cannot "silence" other servers' alerts.
## 5. Database and injection
 
- [ ] Every `text(...)`, `execute(f"...")`, `.format`, `%` or string concatenation in SQL: are
      the values bound as parameters? Dynamic `ORDER BY`, column/metric names and `LIMIT`
      from the request go only through an **allowlist**.
- [ ] Container names and mount paths go into incident keys/cache keys: can they collide, or
      can a separator (`:`, `|`, newline) be injected to merge two incidents?
- [ ] LIKE queries with user input (escape `%`/`_`), regex from user input (ReDoS; `rules.py`
      has 593 lines of rules, check which expressions it evaluates and whether that is safe).
- [ ] **`rules.py`: is anything executed dynamically** (`eval`, `exec`, `compile`, `getattr`
      with a user-supplied name, `importlib`, string templates with a user-supplied format)?
      If rules/alerts are user-configurable, what can the configuration express?
- [ ] Page/`limit` caps, row counts, time ranges on `history`/`stats` (a fixed list of ranges
      and metrics, bounded cache, keys built from validated values).
- [ ] Transactions: no state where a failure mid-operation leaves an account without 2FA or a
      session that survives a password change.
## 6. Outbound connections (SSRF) and integrations
 
- [ ] Notification channels (`notify.py`: ntfy, Discord, webhook, email) are configured via
      the API per user: http(s) scheme only, loopback/link-local/metadata always rejected,
      private LAN addresses for admins only, check after DNS resolution and connect to the
      checked address (DNS rebinding), no following redirects, timeout. Check the SMTP path
      too.
- [ ] Outgoing message content contains no secrets (tokens, hashes) and no unsanitized
      third-party strings in a context where they are interpreted (Slack/markdown/HTML/email
      headers → injection).
- [ ] No `requests`/`httpx` calls to a URL from the request without the protections above.
## 7. Configuration, secrets, logging, errors
 
- [ ] `config.py`/environment: insecure defaults (empty `SECRETS_KEY`,
      `ALLOW_REGISTRATION=true`, `COLLECTOR_ACCESS` ≠ strict, `DEBUG`, `CORS_ORIGINS=*`). The
      default must be secure, and anything insecure must require a deliberate change.
- [ ] Secrets are not written to logs, exceptions or responses (the setup token is printed
      **on purpose** at startup while there are no accounts; check that it is not printed
      afterwards). No stack traces or SQL in HTTP responses; no `/docs`/`/openapi.json`
      exposed on the public side unless intended (check `FastAPI(docs_url=...)` and nginx).
- [ ] **Log injection:** user and collector strings in logs with newlines/ANSI sequences.
- [ ] `seed_user.py` and `reset_2fa.py`: no built-in credentials, no printing of passwords,
      cannot be triggered via the API, do not leave secrets in shell history (arguments).
- [ ] Startup: the backend does not run with `--reload`/debug in the production image; it runs
      as an unprivileged user (see the infra checklist).
## 8. Availability (single-user DoS)
 
- [ ] Expensive endpoints (statistics, fleet, history, export) have work limits and caching;
      no unbounded loops over collector-controlled data.
- [ ] Long-running operations do not block the event loop (synchronous DB/CPU work in an
      `async def` route blocks every request: one slow request = a stall).
- [ ] Data growth: tables filled from check-ins have an upper bound or retention; note it if
      growth is unbounded (known for history rollups).
 