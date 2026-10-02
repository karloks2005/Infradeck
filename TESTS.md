# Tests

Vexion ships with an automated test suite. This page explains how to run it, what it
covers (and what it doesn't), and what every single test is for, so you can decide
whether a failure matters before you upgrade or change your setup.

- **302 test cases** from 179 test functions (some functions run once per input).
- **About 15 seconds** to run. No Docker, PostgreSQL, Redis, network or root access needed.
- **Safe to run next to a live install.** The tests use a throwaway in-memory database
  and fakes; they never touch your running containers or your data. (Two collector
  tests read this machine's `/proc` and root filesystem, read-only.)

## Contents

- [Running the tests](#running-the-tests)
- [How the tests work](#how-the-tests-work)
- [What the tests do not cover](#what-the-tests-do-not-cover)
- [Words used below](#words-used-below)
- [What each test file protects](#what-each-test-file-protects)
- [Every test, explained](#every-test-explained)
- [Adding a test](#adding-a-test)
- [When something fails](#when-something-fails)

## Running the tests

You need Python 3.12 on the machine where you cloned the repository (not inside the
containers; the images don't include the test tools).

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt        # the app's requirements plus pytest
pytest
```

A healthy run ends with a line like:

```text
302 passed, 3 warnings in 14.1s
```

The warnings are harmless notices from the libraries (FastAPI deprecating the
`on_event` startup hook, and a Starlette test-client notice). Anything marked
`FAILED` or `ERROR` is a real problem.

Useful variations:

| Command | What it does |
|---|---|
| `pytest -x` | Stop at the first failure. |
| `pytest -v` | List every test case with its result. |
| `pytest tests/test_rules.py` | Run one file. |
| `pytest -k "oom"` | Run only tests whose name contains `oom`. |
| `pytest tests/test_rules.py::test_single_spike_does_not_alert` | Run one test. |
| `pytest --lf` | Re-run only the tests that failed last time. |
| `pytest --collect-only -q` | List all tests without running them. |

`python -m pytest` works the same as `pytest`. Run it from anywhere inside the repository.

**When to run them:** after `git pull` and before `docker compose up -d --build`, and
after changing code or configuration you care about. A green run means the code's own
behaviour is intact; it does not prove your deployment is healthy (see below).

## How the tests work

Each test builds just what it needs and checks one behaviour:

- **A fresh in-memory SQLite database** per test (`DATABASE_URL=sqlite://`, set by the
  tests themselves). Your PostgreSQL is never used.
- **A fake Redis.** The code is told Redis is "there", but nothing connects to one.
- **Faked system access.** Tests that read `/proc`, mounted filesystems, the Docker
  API, the kernel log or `wtmp` point the collector at temporary files or stubs, so
  results don't depend on the machine running them.
- **Endpoint functions called directly**, with a pretend logged-in user, instead of
  starting a web server. A few tests start the real Docker proxy on a temporary Unix
  socket, and one drives the real FastAPI app through its test client.
- **Hostile input on purpose.** Security-sensitive code (the Docker proxy, the system
  helper, join tokens, the cross-site check) is tested with malformed, oversized and
  malicious values, not just the happy path.

Because of this, the same tests pass on a laptop, a VPS or a Raspberry Pi.

## What the tests do not cover

Being clear about the limits matters, especially for a security-focused project:

- **The web interface** (React) has no automated tests. Build errors show up when you
  run `docker compose up -d --build`; look at the dashboard after upgrading.
- **Your deployment**: Compose files, Traefik labels, nginx configuration, your VPN,
  firewall rules and the collector gate are not exercised by the suite. They were
  checked by hand against a running stack when written, not by an automated test.
- **Real Docker, kernel and disks.** Behaviour on your real hardware (an actual OOM
  kill, a real reboot, your filesystems) is covered by fakes, not by the real thing.
- **Image publishing** (the GitHub workflow) only runs when you push a version tag.

A quick check of a live install after an upgrade:

```bash
curl -s http://localhost:5173/api/health      # database and Redis status
docker compose ps                              # everything Up / healthy
docker compose logs --tail 20 collector        # "Sent host metrics and N container records"
```

## Words used below

| Term | Meaning |
|---|---|
| **Collector** | The small program on each monitored machine that reports to the dashboard. |
| **Check-in** | One report from a collector (host numbers, filesystems, containers, system state). The code calls it *ingest*. |
| **Incident / alert** | A problem shown on the dashboard. At most one is open per problem, and it closes by itself when the problem goes away. |
| **Sustained** | A value must break its limit for several check-ins in a row (3 by default) before an incident opens, so brief spikes are ignored. |
| **Join token** | The single-use code the **Connect a server** command carries so only an admin can add a machine. |
| **Gate / VPN listener** | The separate door collectors use in the server stack; the public side refuses collector traffic. |
| **System helper** | The optional container that reads the kernel log and `wtmp` for OOM process names and crash detection. |
| **Docker proxy** | The read-only middleman that is the only thing touching the Docker socket. |
| **Strict / open mode** | `COLLECTOR_ACCESS=strict` (server stack) or `open` (development stack). |

## What each test file protects

| File | Cases | Protects |
|---|---|---|
| [`test_admin.py`](#test_adminpy) | 18 | Who may do what: admin-only actions, roles, retention cleanup, the chat on/off switch. |
| [`test_agent_errors.py`](#test_agent_errorspy) | 2 | Helpful, non-leaking error messages from the AI assistant. |
| [`test_agent_service.py`](#test_agent_servicepy) | 2 | The assistant can only run the fixed read-only checks. |
| [`test_alerts.py`](#test_alertspy) | 10 | The incident engine: one incident per problem, resolve, container rules. |
| [`test_auth_backend.py`](#test_auth_backendpy) | 50 | Accounts, passwords, the first-account setup token, and the cross-site request check. |
| [`test_collector_mounts.py`](#test_collector_mountspy) | 1 | Which filesystems the collector reports. |
| [`test_container_detail.py`](#test_container_detailpy) | 5 | Per-container details and parsing of Docker's stats. |
| [`test_conversations.py`](#test_conversationspy) | 1 | Deleting one assistant conversation leaves the others alone. |
| [`test_dashboard_host.py`](#test_dashboard_hostpy) | 8 | The "this machine" logic, history charts, container list and incident counts. |
| [`test_docker_proxy.py`](#test_docker_proxypy) | 91 | The read-only Docker proxy: what it allows, refuses and hides. |
| [`test_enrollment.py`](#test_enrollmentpy) | 10 | Enrollment keys, collector credentials and offline-server detection. |
| [`test_health_checks.py`](#test_health_checkspy) | 4 | The assistant's fixed health checks and the critical-only webhook. |
| [`test_pending_collectors.py`](#test_pending_collectorspy) | 28 | Adding servers: approval, join tokens, strict mode, re-pairing safety. |
| [`test_rules.py`](#test_rulespy) | 20 | Alert thresholds: sustained breaches, per-mount disks, settings validation. |
| [`test_system_checks.py`](#test_system_checkspy) | 17 | Reboot, OOM, kernel-fault, clock and pending-reboot detection, and the System row's event list. |
| [`test_system_helper.py`](#test_system_helperpy) | 35 | The optional system helper: kernel-log parsing, `wtmp`, crash detection, hostile input. |

## Every test, explained

Each table row is one test function. Where a function is marked *(N inputs)* it runs
once per input, which is why the totals above are larger than the number of rows.

### test_admin.py

Roles and permissions. If these fail, someone might be able to do something they
shouldn't, or an admin might be locked out.

| Test | What it checks |
|---|---|
| `test_first_registered_user_is_admin_and_later_ones_are_not` | The first account created becomes the admin; the second is a regular user. |
| `test_ensure_admin_exists_promotes_oldest_user_only_when_no_admin` | Upgrading an install from before roles existed promotes the oldest account to admin, only if there is no admin yet, and running it twice changes nothing more. |
| `test_require_admin_rejects_regular_and_disabled_users` | Admin-only actions check the role in the database: regular users and disabled admins are refused with 403. |
| `test_admin_can_change_retention_within_limits` | An admin can change how long metrics are kept; an invalid value (0 days) is rejected with 400. |
| `test_purge_old_metrics_deletes_only_samples_past_retention` | The hourly cleanup deletes only host and container samples older than the retention period and keeps recent ones. |
| `test_regular_user_sees_servers_but_not_pending_collectors` | Everyone signed in sees the servers, but only an admin sees the **New servers detected** list. |
| `test_regular_user_cannot_manage_servers_or_collectors` *(9 inputs)* | A regular user is refused (403) for every admin action: viewing, rotating or revoking the enrollment key, approving or dismissing a pending server, adding or deleting a server, changing a check interval, and reading admin settings. |
| `test_enrollment_key_stops_working_when_owner_is_no_longer_admin` | An enrollment key created by someone who was later demoted stops working. |
| `test_admin_can_turn_agent_chat_off_and_on` | The admin switch for the AI assistant works, regular users can see whether it is on, and the settings response has the expected shape. |
| `test_agent_chat_is_rejected_while_disabled` | With chat turned off, the chat endpoint refuses requests (403) and the AI is never called. |

### test_agent_errors.py

| Test | What it checks |
|---|---|
| `test_overloaded_provider_error_is_explained_to_the_user` | When the AI provider reports it is overloaded, the user gets a readable explanation. |
| `test_provider_error_redacts_api_keys` | Error text from the provider never leaks an API key to the chat or logs; it is replaced with `[redacted]`. |

### test_agent_service.py

| Test | What it checks |
|---|---|
| `test_predefined_health_check_runs_and_is_audited` | When the assistant asks for one of the fixed checks (such as `server_health`), the check runs, the result is fed back to it, and both the request and the result are written to the audit log. |
| `test_arbitrary_command_is_rejected_before_execution` | If the assistant ever asks for anything outside the allow-list (such as `rm -rf /`), it is refused before anything runs. |

### test_alerts.py

The incident engine and the container rules.

| Test | What it checks |
|---|---|
| `test_raise_alert_dedupes_and_refreshes` | Raising the same problem twice keeps one incident and updates its severity and text instead of duplicating it. |
| `test_raise_alert_rejects_unknown_severity` | Only the defined severities (info, warning, critical) are accepted. |
| `test_resolved_rule_can_fire_again_as_new_alert` | After an incident resolves, the same problem coming back opens a new incident and the old one stays in the history. |
| `test_unhealthy_container_alerts_once_and_resolves` | A container whose Docker healthcheck fails opens one critical incident, however many check-ins repeat it, and it closes when the container is healthy again. |
| `test_restart_loop_alerts_once_when_count_climbs` | A container whose restart count keeps rising within the window opens a restart-loop incident that reports how many restarts happened. |
| `test_restart_loop_ignores_old_restarts_and_status_alone` | A large restart count from long ago, or a single "restarting" sample, is not treated as a loop. |
| `test_removed_container_alert_resolves_when_others_report` | If a container disappears and other containers still report, its incident resolves. |
| `test_empty_container_list_keeps_container_alerts` | If a check-in has no containers at all (Docker unreachable), existing container incidents stay open instead of being wrongly closed. |
| `test_container_sync_leaves_other_rules_alone` | Updating container incidents never touches other incidents, such as "server offline". |
| `test_overview_lists_critical_first_with_hostname_and_true_count` | The dashboard's incident list shows the most severe first, includes the server name, and reports the true total even when the list is capped. |

### test_auth_backend.py

Accounts, passwords, first-run protection and the cross-site check.

| Test | What it checks |
|---|---|
| `test_register_user_creates_record_and_hashes_password` | Registering stores the account and a bcrypt hash, never the password itself. |
| `test_authenticate_user_returns_session_for_valid_credentials` | Signing in with the right email and password returns a session. |
| `test_record_audit_log_persists_database_entries` | Audit-log entries, including assistant events with steps and conversation IDs, are saved. |
| `test_registration_closed_still_allows_first_account` | With registration switched off, the first account can still be created on an empty install; once an account exists, registration closes and a stranger is refused (403). |
| `test_registration_open_by_default` | Registration is open when `ALLOW_REGISTRATION` is true. |
| `test_fresh_install_generates_a_token_and_stores_only_its_hash` | An install with no accounts generates a setup token and keeps only its hash in the database. |
| `test_the_token_is_regenerated_at_each_start_until_an_account_exists` | Each backend start issues a new token (the old one stops working) until the first account exists. |
| `test_configured_token_is_used_and_not_printed` | If you set `ADMIN_BOOTSTRAP_TOKEN`, that value is used and nothing is generated or printed. |
| `test_first_account_is_refused_without_the_right_token` *(5 inputs)* | Missing, empty, blank, wrong or huge setup tokens are all refused (403) and no account is created. |
| `test_first_account_with_the_token_becomes_admin_and_the_token_is_removed` | The right token creates the admin, the token is deleted, and it cannot create a second account. |
| `test_without_a_stored_token_the_first_account_is_refused` | If no token exists (the backend didn't start up properly), the first account is refused rather than allowed. |
| `test_existing_installs_need_no_token_and_leftovers_are_cleaned_up` | Installs that already have accounts need no token, and a leftover token is deleted. |
| `test_passwords_under_16_characters_are_refused` *(5 inputs)* | Passwords shorter than 16 characters are rejected. |
| `test_acceptable_passwords_pass` *(5 inputs)* | Passphrases of 16 characters or more are accepted, including spaces, up to 72 bytes, and non-English characters. |
| `test_passwords_over_bcrypts_72_byte_limit_are_refused_not_truncated` *(3 inputs)* | Passwords over 72 bytes are refused with a clear message instead of being silently cut short. |
| `test_registration_enforces_the_policy_and_creates_no_account` | A too-short password at registration is rejected and no account is created. |
| `test_existing_accounts_with_short_passwords_can_still_sign_in` | The 16-character rule applies to new passwords only; older accounts keep working. |
| `test_an_overlong_password_at_login_is_just_invalid_not_an_error_with_internals` | A very long password at login gets the normal "Invalid email or password", not an internal error message. |
| `test_a_request_from_another_site_is_refused` *(4 inputs)* | State-changing requests (POST, PUT, PATCH, DELETE) from a different website are refused. |
| `test_same_host_origin_is_allowed_whatever_the_scheme` | A request from the dashboard's own address is allowed, whether or not the proxy in front hides the `https`. |
| `test_the_port_must_match_too` | The same host on a different port counts as another site; the dashboard's own port is allowed. |
| `test_a_sibling_subdomain_is_a_different_site_for_this_purpose` | Another app on a sibling subdomain, or a look-alike such as `dashboard.example.com.evil.io`, is refused. |
| `test_origins_from_the_configured_list_are_allowed` | Origins you listed in `CORS_ORIGINS` are allowed. |
| `test_odd_origins_are_refused` *(7 inputs)* | Strange origins (`null`, empty, `file://`, `javascript:`, other schemes, missing host, bare hostnames) are refused. |
| `test_without_an_origin_the_fetch_metadata_header_decides` | When a browser sends no `Origin`, its cross-site hint is used: cross-site and same-site are refused, same-origin and direct navigation are allowed. |
| `test_non_browser_clients_and_safe_methods_are_not_affected` | Collectors and `curl` (which send neither header) and read-only requests are never blocked by this check. |
| `test_the_real_app_refuses_a_cross_site_post_before_any_handler_runs` | The real application rejects a forged cross-site request with 403 before any endpoint logic runs. |

### test_collector_mounts.py

| Test | What it checks |
|---|---|
| `test_collect_mounts_keeps_real_filesystems_once` | The collector reports real disks and network shares (such as NFS), decodes mount names containing spaces, and skips `/proc`, `tmpfs`, snaps, Docker's internal mounts and filesystems mounted after it started. Usage and inode percentages are computed correctly. |

### test_container_detail.py

| Test | What it checks |
|---|---|
| `test_container_detail_returns_latest_and_chronological_history` | A container's detail view returns its latest numbers and a history sorted oldest to newest. |
| `test_container_detail_is_visible_to_regular_users` | Regular users can open container details (they are read-only). |
| `test_container_detail_returns_404_for_unknown_server` | Asking for an unknown server gives a clean 404. |
| `test_container_usage_parses_docker_stats` | Docker's raw statistics are converted correctly into CPU %, memory used and percentage (ignoring reclaimable cache), network and disk totals, and process count. |
| `test_format_ports_lists_published_bindings` | Published ports are shown as `8080->80/tcp`, once even if both IPv4 and IPv6 are bound, and unpublished ports are skipped. |

### test_conversations.py

| Test | What it checks |
|---|---|
| `test_conversation_records_are_independent_for_safe_deletion` | Deleting one assistant conversation removes only its own messages and audit entries and leaves other conversations untouched. |

### test_dashboard_host.py

| Test | What it checks |
|---|---|
| `test_dashboard_host_is_listed_first_and_flagged` | The machine running the dashboard is listed first and flagged; other servers follow, newest first. |
| `test_only_latest_claimant_is_dashboard_host_and_flag_can_be_dropped` | Only one machine can be the dashboard host at a time, and a collector that stops claiming it loses the flag. |
| `test_history_returns_recent_samples_oldest_first` | The charts' history contains only the requested window, for the right server, in time order. |
| `test_history_of_unknown_server_is_404` | History for an unknown server is a clean 404. |
| `test_collector_sends_flag_only_when_configured` | The collector marks itself as the dashboard host only when `HOMELAB_DASHBOARD_HOST` is set. |
| `test_overview_lists_only_containers_from_latest_check_in` | The container list shows what the latest check-in reported, not every container ever seen. |
| `test_overview_lists_no_containers_when_latest_check_in_had_none` | If the latest check-in had no containers, none are shown even though older samples exist. |
| `test_overview_counts_active_alerts_per_server` | Each server's incident badge counts only its own open incidents, not resolved ones. |

### test_docker_proxy.py

The read-only Docker proxy is the one component that holds the Docker socket, so it is
tested hardest (91 cases). If any of these fail, do not run the proxy until it is fixed.

| Test | What it checks |
|---|---|
| `test_the_three_read_requests_are_allowed` *(5 inputs)* | The exact requests the collector needs (list containers, inspect one, one stats snapshot) are allowed. |
| `test_only_get_is_allowed` *(8 inputs)* | Every method except GET (POST, PUT, DELETE, PATCH, HEAD, OPTIONS, lowercase `get`, CONNECT) is refused. |
| `test_everything_else_is_refused` *(63 inputs)* | Everything outside the allow-list is refused: other endpoints (`/info`, images, exec, start, kill, export, archive, logs), extra or changed query strings, path traversal, encoded or padded paths, injected line breaks, odd container IDs. |
| `test_inspect_output_drops_environment_and_everything_unneeded` | The container description is cut down to the needed fields; environment variables (often secrets), mounts, labels and host configuration never leave the proxy. |
| `test_list_and_stats_output_are_cut_down` | The same filtering applies to the container list and stats. |
| `test_unexpected_upstream_shapes_do_not_crash_the_filters` *(7 inputs)* | Strange or malformed answers from Docker don't crash the proxy. |
| `test_collector_works_through_the_proxy_and_never_sees_secrets` | End to end: the real collector gets correct container data through the proxy and no secret reaches it. |
| `test_refused_requests_never_reach_the_docker_daemon` | Attack requests are rejected with 403 and Docker is never contacted. |
| `test_upstream_failures_become_a_generic_502` | If Docker errors or is down, the caller gets a plain "Upstream error" with no details. |
| `test_oversized_upstream_answers_are_rejected` | A huge answer from Docker is rejected instead of being passed on. |
| `test_proxy_socket_is_not_world_accessible_and_replaces_a_stale_one` | The proxy's socket file is created with owner and group access only, and a leftover socket from a crash is replaced. |
| `test_collector_still_supports_the_plain_socket_path` | The collector still works without Docker access (it just reports no containers). |

### test_enrollment.py

| Test | What it checks |
|---|---|
| `test_enrollment_key_is_shown_once_and_stored_hashed` | A new enrollment key is returned once, only its hash is stored, and the key's existence can still be shown. |
| `test_enroll_creates_server_owned_by_key_owner` | Using a valid key creates a server owned by the key's owner with a fresh token. |
| `test_reenroll_same_machine_reuses_server_and_replaces_token` | Enrolling the same machine again reconnects it to its existing server and history with a new token. |
| `test_different_machines_get_separate_servers` | Different machines never share a server entry. |
| `test_rotated_or_revoked_key_is_rejected` | Old keys stop working after rotation (401), and a revoked key is gone. |
| `test_offline_alert_raised_once_and_resolved_on_ingest` | A server that misses its check-ins gets one "offline" incident, not a new one each time, and it resolves when the server reports again. |
| `test_recently_seen_server_is_not_offline` | A server that checked in recently is not marked offline. |
| `test_collector_prefers_saved_credentials_over_enrolling` | A collector that already has saved credentials uses them and doesn't enroll again. |
| `test_collector_enrolls_and_saves_when_no_credentials` | Without credentials, the collector enrolls, saves its token, and the file is readable only by its owner (mode 600). |
| `test_machine_id_read_from_host_root` | The collector identifies the machine from the host's `/etc/machine-id`. |

### test_health_checks.py

| Test | What it checks |
|---|---|
| `test_server_health_uses_python_system_interfaces` | The assistant's server check reads memory and other facts through Python system interfaces, not shell commands. |
| `test_container_health_reports_unhealthy_container` | The container check marks the result critical and names an unhealthy container. |
| `test_unknown_check_is_rejected` | Asking for any check that isn't one of the fixed ones raises an error. |
| `test_daily_checkup_alerts_only_for_critical_findings` | The daily check-up notifies the webhook only when something is critical. |

### test_pending_collectors.py

Adding servers: approval, join tokens and strict mode. These protect your install
from machines or people you didn't invite.

| Test | What it checks |
|---|---|
| `test_registration_waits_until_approved_then_hands_out_token` | A joining collector waits as *pending*; after an admin approves it, it receives its token exactly once and the request is consumed. |
| `test_approved_server_appears_in_fleet_before_first_checkin` | An approved server shows up on the dashboard immediately, before its first report. |
| `test_pending_list_shows_live_requests_only` | Requests from collectors that stopped asking disappear from **New servers detected**. |
| `test_dismissed_request_stays_listed_and_can_still_be_added` | A dismissed request moves to the dismissed list and can still be approved later. |
| `test_restarted_collector_replaces_its_earlier_request` | A restarted collector replaces its own earlier request instead of piling up duplicates, while the same machine ID from another address stays separate. |
| `test_approving_known_machine_reuses_existing_server` | Approving a request that matches an existing server needs explicit confirmation (otherwise 409), and the warning names that server; once confirmed it reuses the existing server. |
| `test_unknown_poll_secret_is_rejected` | A wrong poll secret is refused (404). |
| `test_purge_removes_abandoned_requests` | Requests abandoned for a day or more are deleted. |
| `test_collector_waits_for_approval_without_restart` | The collector keeps polling until approved, then continues without being restarted. |
| `test_collector_without_key_or_credentials_requests_approval` | With no key or saved credentials, the collector asks to join and saves its credentials afterwards. |
| `test_collector_sends_its_join_token_when_registering` | The collector sends the join token from the install command (trimmed) when it registers. |
| `test_collector_without_a_join_token_sends_none` | Without a token configured, the collector sends none. |
| `test_collector_explains_a_refused_registration` | If registration is refused, the collector prints the backend's reason, such as an expired token, instead of failing silently. |
| `test_join_token_is_minted_hashed_and_expires` | Join tokens are random, stored only as hashes, and expire. |
| `test_join_token_minting_requires_admin_and_is_capped` | Only admins can create tokens, and no more than 20 unused ones can be open at once. |
| `test_registration_with_a_valid_token_is_marked_verified` | A request that presents a valid token is shown as verified. |
| `test_vpn_listener_refuses_registration_without_a_token` | Over the VPN listener, registration without a token is refused (403) and nothing is stored. |
| `test_wrong_tokens_are_refused` *(3 inputs)* | Blank, made-up or oversized tokens are refused. |
| `test_expired_token_is_refused` | An expired token cannot be used. |
| `test_token_is_consumed_when_the_server_is_approved` | Approving the server uses up its token; it cannot register another machine. |
| `test_same_host_can_retry_with_its_token_before_approval` | The same host can retry with its token until approval without creating duplicates. |
| `test_strict_mode_hides_collector_endpoints_from_the_public_listener` | In strict mode, requests that did not come through the VPN or internal listener get 404 on every collector endpoint, whatever they claim. |
| `test_internal_listener_needs_no_token_so_the_dashboards_own_collector_can_join` | The dashboard machine's own collector, using the internal listener, can join without a token (an admin still approves it). |
| `test_open_mode_keeps_working_without_tokens` | In the development stack (open mode) joining works without tokens, as before. |
| `test_a_bad_token_is_refused_even_in_open_mode` | Even in open mode, a token that is presented but wrong is refused. |
| `test_install_info_reports_the_configured_addresses` | **Connect a server** receives the configured VPN address and image names, and whether strict mode is on. |

### test_rules.py

The alert thresholds, and the settings that control them.

| Test | What it checks |
|---|---|
| `test_host_alert_needs_sustained_breach` | An incident opens only after the threshold is broken for 3 check-ins in a row, with the right wording and severity. |
| `test_single_spike_does_not_alert` | A brief spike that drops back down opens nothing. |
| `test_open_alert_stays_open_until_value_drops_and_escalates_only_when_sustained` | An open incident stays open while the value stays over the warning level, becomes critical only when the critical level is sustained, and resolves when the value drops. |
| `test_load_is_compared_per_core` | Load is judged per CPU core, so a busy 4-core machine and a busy 64-core machine are treated fairly. |
| `test_swap_only_warns` | Swap use produces a warning, never a critical incident. |
| `test_missing_metric_does_not_alert` | A metric the collector couldn't read never raises an incident. |
| `test_saved_thresholds_apply` | Thresholds saved by an admin take effect on the next check-in. |
| `test_threshold_update_is_validated_and_audited` | Invalid thresholds (warning above critical, zero, wrong types, unknown names) are rejected with 400, and valid changes are written to the audit log. |
| `test_corrupt_saved_thresholds_fall_back_to_defaults` | If the saved settings are damaged, the defaults are used instead of crashing. |
| `test_each_mount_gets_its_own_disk_alert` | Every filesystem gets its own disk and inode incidents with its own wording and sizes. |
| `test_unmounted_disk_alert_resolves` | When a disk disappears, its incident resolves. |
| `test_mount_needs_sustained_breach_of_its_own` | A newly seen disk must also stay over the limit for 3 check-ins before alerting. |
| `test_mount_rows_are_stored_and_bad_entries_ignored` | Malformed filesystem entries from a collector are ignored. |
| `test_mount_path_with_braces_does_not_break_titles` | An unusual mount name (containing `{braces}`) cannot break incident titles. |
| `test_mount_override_replaces_global_disk_thresholds_for_that_mount_only` | A per-mount override applies to that mount and no other. |
| `test_mount_override_can_be_looser_than_global` | A per-mount limit can be higher than the default, for disks you want to run nearly full. |
| `test_mount_override_applies_to_root_for_old_collectors` | Overrides for `/` also apply to older collectors that report only the root disk. |
| `test_mount_thresholds_are_validated_audited_and_replaced` | Per-mount settings are validated (absolute paths, 1 to 100, warning below critical, no extras), audited, and replace the old set; an empty set clears them. |
| `test_corrupt_mount_thresholds_are_ignored` | Damaged per-mount settings are ignored. |
| `test_overview_shows_latest_mounts_and_disk_thresholds` | The dashboard shows the latest filesystems and the thresholds used to colour them. |

### test_system_checks.py

Reboots, OOM kills, kernel faults, clock sync and pending reboots, using only what the
kernel exposes (no helper needed).

| Test | What it checks |
|---|---|
| `test_first_check_in_records_no_events` | The very first report cannot be compared with anything, so it creates no events or incidents. |
| `test_new_boot_id_records_a_reboot_and_raises_info_alert` | A new boot ID between check-ins records a reboot event and an informational incident. |
| `test_reboot_alert_clears_after_the_event_window` | The reboot incident closes by itself once the time window has passed. |
| `test_oom_counter_rise_raises_alert_but_a_reboot_resetting_it_does_not` | A rising kernel OOM-kill counter records an event and a warning; the counter dropping to zero after a reboot is not mistaken for an OOM event. |
| `test_repeated_oom_kills_are_critical` | Repeated OOM kills in the window escalate to critical. |
| `test_kernel_fault_taint_alerts_until_reboot_but_module_taint_is_ignored` | Kernel fault flags (machine check, oops and so on) raise a warning that clears on reboot; flags for proprietary or out-of-tree modules are ignored. |
| `test_ntp_alert_needs_sustained_unsynced_checks_and_resolves` | An unsynchronised clock must persist for several check-ins before alerting, and resolves when synced. |
| `test_unknown_values_never_alert` | If the collector couldn't read a value, nothing alerts. |
| `test_malformed_values_from_a_collector_are_dropped` | Wrong-typed or oversized system values are discarded instead of stored. |
| `test_reboot_required_raises_info_alert` | A pending-reboot flag raises an informational incident and clears when gone. |
| `test_collector_reads_kernel_state_from_proc` | The collector reads boot ID, OOM counter, kernel flags and the reboot-required file from the right places. |
| `test_collector_reports_unknown_when_files_are_missing` | Missing files give *unknown*, never a false "OK". |
| `test_repeated_kernel_errors_collapse_into_one_row_with_a_count` | The same kernel message seen on several boots is shown once with a count (the row's time is the latest occurrence; the first is kept for the tooltip), while different messages stay separate. |
| `test_reboots_and_oom_kills_are_never_collapsed` | Reboots and OOM kills stay one row per occurrence, so the timeline is never merged. |
| `test_noisy_kernel_errors_cannot_crowd_out_real_events` | Even with dozens of different kernel messages, an older OOM kill is still listed and kernel messages are capped at 10 rows. |
| `test_events_are_listed_newest_first_across_kinds` | Reboots, OOM kills and kernel messages are interleaved newest first. |
| `test_events_older_than_seven_days_are_not_shown` | Events older than 7 days are left out of the System row. |

### test_system_helper.py

The optional system helper reads text that an attacker can influence (process names,
log lines, user names), so most of these tests feed it hostile input.

| Test | What it checks |
|---|---|
| `test_oom_kill_is_parsed_with_process_name_and_wall_time` | An OOM line from the kernel log becomes an event with the killed process name and the correct wall-clock time. |
| `test_cgroup_oom_is_recognised` | OOM kills inside a container's memory limit are recognised too. |
| `test_kernel_errors_are_kept_but_info_lines_are_not` | Only error-level kernel messages are kept; ordinary informational lines are dropped. |
| `test_hostile_log_content_is_neutralised` | Escape codes, control characters and over-long names in log lines are made harmless and truncated. |
| `test_malformed_and_untimestamped_lines_are_ignored` | Garbage and unparseable lines are skipped. |
| `test_output_is_capped_to_the_newest_events` | Only the newest few events are published, so output size stays bounded. |
| `test_utmp_layout_is_384_bytes` | The `wtmp` record layout matches the real 384-byte format. |
| `test_clean_shutdown_is_detected` | A shutdown record before the latest boot means the previous shutdown was clean. |
| `test_missing_shutdown_record_is_unclean_when_shutdowns_were_recorded_before` | No shutdown record, on a system that normally records them, means a crash or power loss. |
| `test_never_recorded_shutdowns_are_unknown_not_unclean` | A system that never records shutdowns is reported as *unknown*, not as crashing on every reboot. |
| `test_unknown_when_wtmp_is_missing_short_or_out_of_date` | A missing, too-short or outdated `wtmp` gives *unknown*. |
| `test_truncated_or_symlinked_wtmp_is_safe` | A half-written `wtmp` is handled, and a symlinked one is refused. |
| `test_snapshot_is_written_atomically_with_a_fixed_schema` | The summary file is replaced atomically, with a fixed layout and fixed file permissions (readable by others, writable only by the helper), so a reader never sees a half-written file. |
| `test_collector_uses_a_fresh_file_from_the_current_boot` | The collector accepts the helper's file when it is recent and from the current boot. |
| `test_collector_ignores_stale_or_foreign_files` *(3 inputs)* | Old files, files from another boot, and files with a bad timestamp are ignored. |
| `test_collector_refuses_missing_symlinked_oversized_and_garbage_files` | Missing, symlinked, oversized, malformed or wrongly shaped files are refused. |
| `test_helper_events_are_stored_once_even_when_republished` | The helper republishes the same events every run, but each is stored once. |
| `test_with_the_helper_the_oom_counter_does_not_add_a_second_event` | With the helper running, one OOM kill produces one event, not two. |
| `test_malformed_helper_events_are_dropped` *(10 inputs)* | The backend rejects events with a disallowed kind, bad key, impossible time, wrong types or old age. |
| `test_helper_event_count_and_text_are_bounded` | At most 50 events per check-in are accepted, and long text is trimmed. |
| `test_unclean_shutdown_makes_the_reboot_critical` | A reboot after an unclean shutdown opens a critical incident whose text says so. |
| `test_clean_shutdown_keeps_the_reboot_informational` | A reboot after a clean shutdown stays informational. |
| `test_helper_arriving_after_the_reboot_was_noticed_updates_the_event_and_alert` | If the helper finishes after the reboot was first noticed, the event and incident are updated to the right answer. |
| `test_invalid_last_shutdown_values_are_dropped` | An unexpected value for "last shutdown" is discarded. |

## Adding a test

1. Put it in the file for the area it belongs to, or a new `tests/test_<area>.py`
   (files must start with `test_`, functions with `test_`).
2. Follow the existing style: build a fresh in-memory database, call the code
   directly, assert on the result. Look at a similar test and copy its setup.
3. For anything that handles outside input, add the awkward cases too (empty,
   oversized, wrongly typed, malicious). `pytest.mark.parametrize` runs one test
   over many inputs.
4. Run `pytest -x`, then the whole suite.
5. Add a row to this page for the new test.

## When something fails

| What you see | What to do |
|---|---|
| `ModuleNotFoundError: No module named 'pytest'` | Install the test tools: `pip install -r requirements-dev.txt`. |
| `ModuleNotFoundError: No module named 'src'` | Run `pytest` from inside the repository folder. |
| `ModuleNotFoundError` for another package | Install the app's requirements: `pip install -r requirements.txt` (or the dev file above). |
| One test `FAILED` | Read the assertion line (`assert ... == ...`) printed under it: the left side is what the code did, the right side is what was expected. Use `pytest -x -v` to focus on the first failure. |
| Failures after you edited code | The test name tells you what rule you broke. If the rule changed on purpose, update the test and its row on this page. |
| Failures after `git pull` with no changes of your own | Please open an issue with the output of `pytest -x`. |
