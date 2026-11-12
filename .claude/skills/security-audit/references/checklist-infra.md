# Checklist: infrastructure, repository, dependencies
 
Read `docker/*` (Dockerfiles, `nginx*.conf`, `Caddyfile`, `postgres/init/*.sql`),
`compose.yml`, `docker-compose.server.yml`, `collector-compose.yml`, `example.env`,
`.dockerignore`, `.gitignore`, `.github/workflows/*.yml`, `requirements*.txt`,
`src/frontend/package.json` + lockfile.
 
## 1. Dockerfiles
 
- [ ] `USER` is unprivileged in every image that allows it (docker-proxy and system-helper are
      exceptions because of the socket/capabilities: check that these are the only places and
      that they are justified).
- [ ] Base images: pinned (digest or at least an exact version), not `latest`; minimal.
- [ ] No secrets in layers (`ARG`/`ENV` with tokens, `COPY .env`, `.git`, `dev.db`, keys);
      `.dockerignore` excludes `.env*`, `.git`, `venv`, `*.db`, tests, `node_modules`.
- [ ] No `curl | sh`, no `pip install` without pins/hashes for production dependencies, no
      `apt` without `--no-install-recommends` and cleanup; the build does not execute
      untrusted code from the network.
- [ ] `HEALTHCHECK`/`CMD` does not start debug mode (`--reload`, `--debug`).
- [ ] Frontend image: built multi-stage; production has no `node_modules`, dev server, or
      source maps exposing the source code unless intended.
## 2. Compose files
 
For **every service**, record: user, `cap_drop`/`cap_add`, `read_only`,
`security_opt: no-new-privileges`, `privileged`, `network_mode`, `pid`, `ports`, `volumes`,
`environment`/`secrets`.
 
- [ ] **Published ports** are bound to a specific address (`127.0.0.1:` or the VPN address),
      not `0.0.0.0`, except for the intentionally public frontend. Docker bypasses `ufw`: a
      firewall rule is not protection. The backend, Postgres and docker-proxy have no
      published ports in the server stack.
- [ ] **`/var/run/docker.sock` is mounted only into docker-proxy**, nowhere else (backend,
      collector, frontend, helper). The same goes for `/`, `/etc`, `/root`, `/home`,
      `/var/lib/docker`.
- [ ] Host mounts are `:ro` wherever possible; `/hostfs` is read-only; no `rw` mounts of
      sensitive directories.
- [ ] The volume holding the docker-proxy Unix socket is shared **only** by the proxy and the
      collector.
- [ ] `cap_drop: [ALL]` by default; every `cap_add` justified (only `CAP_SYSLOG` for the
      helper). No `privileged`, `pid: host`, `network_mode: host`, `ipc: host` or `devices`
      without a reason.
- [ ] `read_only: true` + `tmpfs` where needed; `no-new-privileges` on everything.
- [ ] Network segmentation: the collector gate is not on the reverse proxy's network; the
      backend is not on a network that other apps can reach; Postgres only on an internal
      network; `internal: true` where egress is not needed.
- [ ] **Default/placeholder secrets:** compose/`example.env` contain no real or weak default
      passwords (`changeme`, `postgres`, `admin`) that would remain in production; required
      values use `${VAR:?message}` so the stack does not start without them.
- [ ] Images in compose for remote hosts are pinned by digest (`COLLECTOR_IMAGE`,
      `DOCKER_PROXY_IMAGE`), and the panel does not generate a command without them.
- [ ] Postgres: passwords not in plaintext in compose if `secrets:`/env from `.env` is
      possible; no published 5432.
- [ ] Healthchecks do not reveal secrets; restart policies do not hide a crash loop.
## 3. nginx (`nginx.conf`, `nginx.strict.conf`, `nginx.collector.conf`)
 
- [ ] **The public side returns 404 for all collector endpoints** (register, poll, enroll,
      ingest, helper). Check the exact `location` blocks: prefix vs. exact, regex, trailing
      slash differences, upper/lower case, `//`, `/./`, `%2f`, `..;/`. Worst case: a path that
      nginx does not recognize as forbidden but that the backend normalizes into a collector
      endpoint.
- [ ] `X-Collector-Via` (and similar internal headers) is **set** only by the gate, and the
      public side **clears/overwrites** it (`proxy_set_header X-Collector-Via "";`).
- [ ] `real_ip` / `X-Forwarded-For`: `set_real_ip_from` only for private/Docker ranges,
      `real_ip_recursive` correct; no trust in XFF from the public side; the backend gets the
      real client IP for rate limiting.
- [ ] **`add_header` inheritance:** an `add_header` in a `location` block *discards* all
      `add_header` directives from the `server`/`http` level. Check that every `location`
      with its own `add_header` (cache, CORS) repeats the security headers (CSP, nosniff,
      frame, HSTS), and that they are added with `always` so they apply to errors too.
- [ ] CSP: no `unsafe-inline`/`unsafe-eval` for `script-src`, `default-src 'self'`,
      `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`,
      `form-action 'self'`; allowing inline `style` is a known cost (React style attributes).
- [ ] HSTS only on the strict/public configuration; `server_tokens off`.
- [ ] `client_max_body_size` limited per location; `client_body_timeout`,
      `proxy_*_timeout`, `limit_req`/`limit_conn` on sensitive routes (login, enroll) as a
      second layer.
- [ ] `proxy_pass`: watch the trailing slash (it changes path normalization); only the
      necessary headers are forwarded; `proxy_set_header Host` is correct (the backend uses
      `Host` for the Origin check); WebSocket upgrade only where needed.
- [ ] No `autoindex on`, no `alias` without a trailing slash (traversal); is anything served
      besides the SPA's static files (`.map`, `.env`, `.git`)? `try_files` does not expose
      hidden files.
- [ ] TLS configuration (if nginx terminates it): protocols ≥ TLS 1.2, no weak ciphers. If
      Traefik terminates TLS, state that as an assumption, not a finding.
- [ ] `Caddyfile`: is it a leftover/dev configuration? If it is on the production path,
      review it as strictly as nginx.
## 4. Postgres (`docker/postgres/init/*.sql`)
 
- [ ] The application connects as a user with **minimal privileges** (not superuser, not the
      schema owner if migrations are not needed at runtime), separate from the
      administrative one.
- [ ] Passwords in init scripts are not committed to the repository; no `trust`
      authentication from all addresses; `pg_hba` does not allow external connections.
- [ ] No `CREATE EXTENSION` or `GRANT ALL` without a reason.
## 5. GitHub Actions (`.github/workflows/images.yml`)
 
This is the supply chain for images that hosts run with root-equivalent access.
 
- [ ] `permissions:` least required (`contents: read`, `packages: write`, `id-token: write`
      only for the signing job); no broad default permissions.
- [ ] **Actions pinned to a full commit SHA**, not a tag (`@v4`), for actions that touch
      secrets or signing; check who maintains them.
- [ ] No `pull_request_target` with a checkout of PR code; the trigger is only a `v*` tag
      and/or manual, not every push/PR from a fork.
- [ ] **Script injection:** no `${{ github.event.* }}` / `${{ github.head_ref }}` / branch
      names or commit messages inserted directly into `run:` (pass them via `env:`).
- [ ] cosign signing: *keyless* identity bound to the workflow and ref; the digest output is
      correct; images are built from the tagged commit; the build does not pull unpinned
      dependencies without verification. Describe how the host **verifies** the signature
      (documented; if `cosign verify` is not part of the generated command, that is a
      weakness: a signature nobody verifies protects nothing).
- [ ] Secrets are not printed (`set -x`, `echo`); no `secrets: inherit` without a reason.
## 6. Repository and history
 
- [ ] `.gitignore` covers `.env`, `*.db`, `*.sqlite`, keys, `venv`, `node_modules`.
      `git ls-files | grep -Ei '\.env$|\.db$|\.sqlite|\.pem$|\.key$|id_rsa|secret|credential'`:
      what is **tracked by git**? (`dev.db` was at one point.) Both `.env` and `example.env`
      exist: check that `example.env` contains no real values.
- [ ] **History:** `git log --all --diff-filter=A --name-only | grep -Ei '\.env|\.db|key|secret'`
      and a search for secret patterns (`git log -p --all -S'SECRET' -S'PASSWORD' -S'TOKEN'`
      over a reasonable range). A secret that was ever in the history is considered
      compromised even if it was later deleted; report it with a recommendation to rotate.
      Do not print values.
- [ ] Pattern search in the working tree: keys (`AKIA`, `ghp_`, `sk-`, `-----BEGIN`),
      passwords in code, hardcoded tokens, `localhost` authentication bypasses,
      `TODO`/`FIXME`/`HACK` next to the words `auth`, `security`, `bypass`, `skip`.
- [ ] `TESTS.md`, `docs/`, `README.md` contain no real secrets/domains/IP addresses of the
      owner.
- [ ] `SECURITY.md` has a vulnerability reporting path (it does) and accurate claims.
## 7. Dependencies
 
- [ ] `requirements*.txt` and `package-lock.json`: pinned? Is the lockfile in sync with
      `package.json`?
- [ ] If the tools are available (`pip-audit`, `npm audit --omit=dev`), run them **only with
      the user's confirmation** (they send the package list to the registry). If they were
      not run, say so in the coverage section: do not claim the dependencies have "no known
      vulnerabilities".
- [ ] Unnecessary/abandoned dependencies, dependencies from a URL/git, `postinstall` scripts,
      typosquat names. Dev dependencies do not end up in the production image.
 