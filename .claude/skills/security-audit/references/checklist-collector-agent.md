# Checklist: collector, docker proxy, system helper (`src/collector/**`), incident agent (`src/agent/**`)
 
Read `collector.py`, `docker_proxy.py`, `system_helper.py` and, for the agent, `src/agent/`
(`tools/`, `investigate.py`, `diagnosis.py`, `chat.py`, `masking.py`, `safety.py`,
`providers/`, `worker.py`, `usage.py`, both prompts) in full. The docker proxy and the system
helper hold privileges: they get the most attention.
 
## 1. Docker proxy (`docker_proxy.py`): holds root-equivalent access
 
- [ ] **The allowlist is a literal comparison of the whole request target.** No decoding,
      normalization, `startswith`, unanchored regex (`re.match` without `$`/`fullmatch`), or
      `in`. Bypass attempts: `?all=1&x=y`, `//containers/json`, `/containers/json/`, `%2e`,
      `;`, `#`, `\`, space, tab, `\r\n`, upper/lower case method, `HEAD`/`OPTIONS`, `GET`
      with a body, conflicting `Transfer-Encoding`/`Content-Length`, HTTP/1.0, multiple
      requests on one connection (pipelining/smuggling), a non-hex container ID (`../`,
      `x/exec`).
- [ ] The ID in `/containers/<id>/json` and `/stats` is strictly validated (hex, length)
      *before* being forwarded to Docker. The proxy does not build the request to Docker by
      concatenating an unchecked string.
- [ ] The proxy **does not forward** the client's headers or method; it builds the request to
      Docker itself.
- [ ] **Response filtering:** returns only a fixed list of fields; `Env`, `Labels`, `Mounts`,
      `Config`, `HostConfig`, `Error` never leave. Check both `/containers/json` and stats
      (stats contain no secrets, but check). New fields = a deliberate change + a test.
- [ ] Response/request size is limited; timeouts; one slow-reading connection does not block
      everything (DoS against the collector); Docker errors do not return raw text.
- [ ] Unix socket: mode 0660, directory/volume shared **only** with the collector; the socket
      is not created in a world-writable directory; no race on `bind`/`chmod` (a short window
      with wider permissions; default `umask`).
- [ ] No TCP listener, no network (`network_mode: none`, see the infra checklist).
- [ ] The 403 log does not allow log injection (sanitized request target).
## 2. System helper (`system_helper.py`): privileged, output untrusted
 
- [ ] Read-only (dmesg *read-all*, wtmp); never writes/clears the kernel log.
- [ ] wtmp parsing is binary: check record size, bounds, a truncated record, a huge number of
      records; it reads only boot/shutdown records, not usernames or addresses.
- [ ] Kernel log lines: fixed patterns, reduced to printable characters, truncated, a capped
      number of events; no `eval`/shell/format string with that content; regexes without
      catastrophic backtracking on a 100 kB line.
- [ ] Output file: atomic write (temp + rename), permissions, only in its own volume.
- [ ] Nothing is executed from the content; no `subprocess` with attacker-controlled input
      (and if there is a call, the arguments are a list, no `shell=True`).
## 3. Collector (`collector.py`, 974 lines): runs on the host, reads the host FS
 
- [ ] **Reading files from the host:** a fixed list of paths; `O_NOFOLLOW`; capped read size
      (1 MB firewall file, the helper's output); a check that the helper output is fresh and
      from the current boot; no paths built from host or dashboard data (`os.path.join` with
      an absolute path silently replaces the base!). TOCTOU between `stat` and `open`.
- [ ] `/proc/1/net` and other parsers: an incomplete read → "unknown", never a partial result
      that would mark ports "closed"; huge files do not exhaust memory.
- [ ] **What comes from the dashboard** (the register/poll response) is treated as untrusted:
      no execution, no paths, no probe targets from the response. Internet connectivity
      check targets come only from the collector's environment
      (`INFRADECK_CONNECTIVITY_TARGETS`, ≤5, hostname or IPv4), with a hard deadline.
- [ ] Probing: sends no data, only TCP connect + DNS; cannot be pointed at an arbitrary
      target; time-limited so the check-in does not hang.
- [ ] **Storage of the collector's credentials** (server token): a file with mode 0600, not in
      the image, not in logs, not in process arguments; the collector does not print the
      token on error.
- [ ] Transport: TLS verification when using HTTPS (no `verify=False`); when it is plain HTTP
      inside a VPN, that is documented. No redirects that would carry the `Authorization`
      header to another host. Timeouts on every network call.
- [ ] A failure in one sample does not crash the whole collector in a way that silences it
      without a trace (can an attacker who triggers an exception "turn off" monitoring?).
- [ ] `subprocess`, `os.system`, `eval`, `exec`, `pickle`, `yaml.load`: inspect every
      occurrence; arguments as a list; no host data in the command.
- [ ] Limits on the amount of reported data (containers, ports, events) on the collector
      side, not only the backend.
## 4. Incident agent (`src/agent/`)
 
Documented (`docs/assistant.md`, `docs/agent-investigation.md`, SECURITY.md): the agent has no
shell and no host access. It has a closed list of read-only tools (`tools/__init__.py`:
`server_info`, `host_metrics`, `list_containers`, `container_history`, `disk_usage`, `events`,
`compare_to_baseline`, `find_incidents`, `fleet_snapshot`, `check_results`, `network_state`)
that read only the Infradeck database. The loop (`investigate.py`) has a call budget, a
deadline and a monthly cost limit. Recommended commands are only text that `safety.py`
checks; nothing is executed.
 
- [ ] **The tools really are fixed:** `run` rejects an unknown name, arguments are validated
      against the schema before reading; no argument ends up in a command, path, URL or SQL
      outside the ORM. The result is capped (`bounds.py`, `MAX_RESULT_CHARS`) and contains
      nothing that only admins see (IP addresses, users, audit log), because everyone gets
      the same answer.
- [ ] **Masking:** everything sent to the provider (tool results, incident context, user chat
      messages, errors in `usage`/`worker`) goes through `masking.py`. Look for a path that
      bypasses it (a new tool, a new field, a provider error message that is stored or
      displayed).
- [ ] **Prompt injection:** data entering the model's context (container names, events,
      check errors, user text) can contain instructions. What is the worst the model can do
      if it follows them? (Burn the budget, cite false facts, recommend a harmful command.)
      Check that `check_answer` accepts only fact IDs from real results, that `safety.py`
      catches destructive and hidden characters (bidi, zero-width), and that a command that
      deletes data is never recommended.
- [ ] **Provider and key:** the key is encrypted (`secrets_box`), never in an API response,
      log or audit record; provider addresses are fixed in code (the user cannot set
      `base_url`); changing the provider or key, and enabling chat, require reauth
      (password + 2FA).
- [ ] **Background jobs:** the worker (`jobs.py`, `worker.py`) has an overall deadline and
      cannot leave a job in the "running" state forever; the Redis queue does not accept
      untrusted data as code.
- [ ] **Model output is not treated as trusted:** it is displayed as text (the frontend does
      not use `dangerouslySetInnerHTML`/unchecked markdown with HTML), and it never goes into
      SQL, commands or URLs.
- [ ] **Conversations are bound to the user** (IDOR: reading/deleting another user's
      conversations by ID); limits on message length and message count; limits on tool-loop
      steps and time (DoS and API cost); per-user rate limit.
- [ ] The API key for the LLM provider: from the environment, not in logs, not in responses,
      not in conversations that are stored or sent back.
- [ ] The system prompt contains no secrets (assume the model will print it sooner or later).
- [ ] Calls to the Docker API go only through the proxy (`DOCKER_SOCKET_PATH`); the backend
      does not mount `docker.sock`. Check compose and the code.
- [ ] The agent has no outbound connections except to the provider; notifications are sent
      by the backend (`notify.py`, see `checklist-backend.md` §6), and the model chooses
      neither the destination nor the content.
 