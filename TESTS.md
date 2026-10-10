# Tests

Vexion ships with an automated test suite. This page explains how to run it, what it
covers (and what it doesn't), and what every single test is for, so you can decide
whether a failure matters before you upgrade or change your setup.

- **798 test cases** from 469 test functions, plus 16 small tests of the chart helpers (see [Frontend helper tests](#frontend-helper-tests)) (some functions run once per input).
- **About a minute and a half** to run. No Docker, PostgreSQL, Redis, network or root access needed.
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
507 passed, 3 warnings in 17.0s
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
- **A fake Redis** (`tests/fakes.py`). The code is told Redis is "there", but nothing connects to one.
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
  run `docker compose up -d --build`; look at the dashboard after upgrading. This
  includes the Account page and the two-factor prompts: the endpoints behind them are
  tested, the screens are not. The Account page, the sign-in code step and the Statistics
  page (including container cards, the all-servers view, histograms, the heatmap and storage
  charts) were checked by hand in a real browser (headless Chromium against a running stack,
  desktop and phone width). Only the plain helper functions behind the charts have automated
  tests.
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
| [`test_account_security.py`](#test_account_securitypy) | 121 | Two-factor sign-in, recovery codes, encrypted secrets, sessions you can review and end, and password change. |
| [`test_admin.py`](#test_adminpy) | 18 | Who may do what: admin-only actions, roles, retention cleanup, the chat on/off switch. |
| [`test_agent_errors.py`](#test_agent_errorspy) | 2 | Helpful, non-leaking error messages from the AI assistant. |
| [`test_agent_service.py`](#test_agent_servicepy) | 2 | The assistant can only run the fixed read-only checks. |
| [`test_alerts.py`](#test_alertspy) | 10 | The incident engine: one incident per problem, resolve, container rules. |
| [`test_auth_backend.py`](#test_auth_backendpy) | 50 | Accounts, passwords, the first-account setup token, and the cross-site request check. |
| [`test_collector_mounts.py`](#test_collector_mountspy) | 1 | Which filesystems the collector reports. |
| [`test_container_detail.py`](#test_container_detailpy) | 18 | Per-container details and parsing of Docker's stats. |
| [`test_conversations.py`](#test_conversationspy) | 1 | Deleting one assistant conversation leaves the others alone. |
| [`test_container_checks.py`](#test_container_checkspy) | 126 | Docker health, container exit codes, inventory and image changes, memory-leak trends, and the validation of everything a collector sends about containers. |
| [`test_dashboard_host.py`](#test_dashboard_hostpy) | 8 | The "this machine" logic, history charts, container list and incident counts. |
| [`test_docker_proxy.py`](#test_docker_proxypy) | 111 | The read-only Docker proxy: what it allows, refuses and hides. |
| [`test_enrollment.py`](#test_enrollmentpy) | 10 | Enrollment keys, collector credentials and offline-server detection. |
| [`test_health_checks.py`](#test_health_checkspy) | 4 | The assistant's fixed health checks and the critical-only webhook. |
| [`test_history.py`](#test_historypy) | 36 | The history API behind long-range charts: which level of detail answers each range, point limits, gaps, and agreement with the raw samples. |
| [`test_indexes.py`](#test_indexespy) | 9 | The database indexes on the metrics tables: only the ones queries use, none duplicated. |
| [`test_network_checks.py`](#test_network_checkspy) | 94 | The host's real network totals, listening-port inventory, optional internet check, firewall fingerprints, and the validation of all of it. |
| [`test_pending_collectors.py`](#test_pending_collectorspy) | 28 | Adding servers: approval, join tokens, strict mode, re-pairing safety. |
| [`test_rollups.py`](#test_rollupspy) | 31 | Summarised history: hourly, daily, weekly and monthly rollups, catching up, gaps, and purging only what has been summarised. |
| [`test_rules.py`](#test_rulespy) | 20 | Alert thresholds: sustained breaches, per-mount disks, settings validation. |
| [`test_stats.py`](#test_statspy) | 46 | Fleet statistics (incidents over time, availability) and the busy-hours heatmap: grouping, ranges, caching and validation. |
| [`test_system_checks.py`](#test_system_checkspy) | 17 | Reboot, OOM, kernel-fault, clock and pending-reboot detection, and the System row's event list. |
| [`test_system_helper.py`](#test_system_helperpy) | 35 | The optional system helper: kernel-log parsing, `wtmp`, crash detection, hostile input. |

## Every test, explained

Each table row is one test function. Where a function is marked *(N inputs)* it runs
once per input, which is why the totals above are larger than the number of rows.

### test_account_security.py

Two-factor sign-in, recovery codes, encrypted secrets, sessions and password change.
If these fail, an account could be easier to take over, or an admin could be locked
out of their own install.

| Test | What it checks |
|---|---|
| `test_codes_match_the_rfc_6238_reference_vectors` *(6 inputs)* | The one-time code generator gives the exact answers published in the TOTP standard (RFC 6238), so it will match any authenticator app. |
| `test_a_code_is_accepted_in_its_own_period_and_one_either_side_but_not_further` | A code works in its own 30-second period and one either side (clock drift), not further away. |
| `test_a_code_cannot_be_replayed` | A code that just signed you in is refused if used again in its window; the next period's code works. |
| `test_a_code_cannot_be_used_twice_or_after_a_newer_one` | A code that was already used, or an older one, is refused. |
| `test_malformed_codes_are_rejected` *(12 inputs)* | Empty, short, long, non-numeric, huge and non-string codes are refused without errors. |
| `test_spaces_and_a_hyphen_in_a_typed_code_are_ignored` | `123 456` and `123-456` are accepted as `123456`. |
| `test_new_secrets_are_random_and_authenticator_friendly` | Every secret is different and uses the base32 letters authenticator apps expect. |
| `test_the_otpauth_link_names_the_account_and_issuer_safely` | The link you paste into an authenticator carries the account and "Vexion", with odd characters escaped. |
| `test_recovery_codes_are_random_formatted_and_normalised` | Ten distinct recovery codes in `XXXX-XXXX-XXXX-XXXX` form; typing them in lower case or without hyphens still matches. |
| `test_things_that_are_not_recovery_codes_are_refused` *(7 inputs)* | Wrong length, wrong characters and non-strings are not treated as recovery codes. |
| `test_secrets_are_encrypted_and_round_trip` | A secret is encrypted with `SECRETS_KEY` and decrypts back; the stored text doesn't contain it. |
| `test_without_a_key_nothing_can_be_stored` | With no `SECRETS_KEY`, nothing is stored (never in plain text). |
| `test_an_invalid_key_is_refused_not_used` *(6 inputs)* | A malformed key is treated as no key at all. |
| `test_a_wrong_key_or_tampered_value_cannot_be_decrypted` | Another key, or an edited stored value, fails to decrypt instead of returning garbage. |
| `test_keys_can_be_rotated_by_listing_the_new_one_first` | Listing a new key first encrypts with it while values made with the old key still open. |
| `test_session_ids_are_long_random_and_distinct` | Session IDs are long, random and never repeat. |
| `test_a_session_is_indexed_under_its_user_and_shows_only_public_ids` | Each user's sessions are listed under opaque public IDs; the real session ID never appears. |
| `test_expired_sessions_drop_out_of_the_list` | Sessions that expired no longer show up. |
| `test_one_session_can_be_revoked_without_touching_the_others` | Ending one session leaves the user's others signed in. |
| `test_a_user_cannot_revoke_or_see_someone_elses_session` | Another user's session can neither be seen nor ended. |
| `test_malformed_public_ids_are_never_looked_up` *(8 inputs)* | Odd session identifiers are rejected before touching Redis. |
| `test_all_other_sessions_can_be_revoked_keeping_the_current_one` | "Sign out everywhere else" keeps only the current session. |
| `test_logging_out_removes_the_session_from_the_index` | Logging out also removes the session from the list. |
| `test_logging_out_a_session_that_already_expired_is_still_fine` | Logging out with an expired session doesn't fail. |
| `test_sessions_created_before_stages_existed_count_as_full` | Sessions from before this feature stay working as normal sessions. |
| `test_limited_sessions_are_refused_unless_a_caller_opts_in` | A limited (set-up-only) session is refused by default; only the two-factor setup endpoints opt in. |
| `test_every_route_that_needs_a_session_refuses_a_limited_one` | Sweeps every route that needs a sign-in and checks each refuses a limited session, except the short allowed list. |
| `test_a_limited_session_is_refused_everywhere_except_the_two_factor_setup` | A limited session can reach only its own profile, two-factor setup and logout. |
| `test_a_limited_session_cannot_disable_regenerate_or_change_the_password` | A limited session can't turn off two-factor, make new recovery codes or change the password. |
| `test_signing_in_with_two_factor_needs_both_steps_and_creates_no_session_before_the_second` | After the password alone there is no cookie and no session; only a correct code creates one. |
| `test_a_wrong_or_malformed_code_is_refused_with_one_generic_message` | Every kind of wrong code gets the same "Invalid code." answer. |
| `test_a_wrong_code_during_sign_in_is_audited_without_the_code` | Failed codes are logged without recording what was typed. |
| `test_a_pending_login_is_destroyed_after_five_wrong_codes` | Five wrong codes end that sign-in attempt; the right code no longer helps. |
| `test_a_successful_second_step_resets_the_failure_count` | A good code clears the attempt count. |
| `test_a_pending_login_token_is_single_use_and_unknown_tokens_are_refused` | A login token works once; invented ones are refused. |
| `test_an_expired_pending_login_cannot_be_completed` | If the code isn't entered within five minutes, the password step has to be repeated. |
| `test_guessing_across_many_pending_logins_locks_the_account_for_a_while` | Restarting the password step doesn't reset the guess count; five wrong codes in five minutes lock the account's code step. |
| `test_the_per_address_limit_on_code_attempts_applies_too` | One IP address can't guess codes for many accounts. |
| `test_hostile_second_step_bodies_are_rejected_before_any_work` *(7 inputs)* | Missing, oversized or wrong-typed fields are rejected right away. |
| `test_unknown_emails_cost_the_same_work_as_wrong_passwords` | Sign-in does the same password work for unknown emails, so timing doesn't reveal which accounts exist. |
| `test_a_member_signs_in_with_just_a_password` | Members without two-factor sign in as before. |
| `test_the_code_from_the_enrollment_step_cannot_be_reused_to_sign_in` | The code used to switch two-factor on can't be used again straight away to sign in. |
| `test_a_wrong_code_does_not_enable_two_factor` | A wrong confirmation code leaves two-factor off. |
| `test_enabling_without_starting_setup_is_refused` | Confirming a code without first requesting a secret is refused. |
| `test_setting_up_again_before_confirming_replaces_the_pending_secret` | Starting setup twice gives a new secret; the abandoned one is useless. |
| `test_setup_is_refused_while_two_factor_is_already_on` | You can't replace a working secret without turning two-factor off first. |
| `test_the_secret_is_stored_encrypted_and_recovery_codes_only_as_hashes` | In the database the secret is encrypted and recovery codes are hashes; neither is readable. |
| `test_secrets_never_appear_in_api_responses` | After setup is done, no response includes the secret or the recovery codes again. |
| `test_recovery_codes_work_once_and_the_count_drops` | A recovery code signs you in once; the remaining count goes down. |
| `test_regenerating_recovery_codes_replaces_the_old_ones` | Making new codes (needs password and a code) invalidates all old ones. |
| `test_turning_two_factor_off_needs_the_password_and_a_code_and_ends_other_sessions` | Turning two-factor off needs the password plus a code, and signs out other sessions. |
| `test_if_the_key_is_lost_totp_codes_stop_working_but_recovery_codes_still_do` | If `SECRETS_KEY` is lost, authenticator codes fail but recovery codes still get you in. |
| `test_when_no_key_is_configured_two_factor_is_not_enforced_and_cannot_be_set_up` | Without a key the "require 2FA" policy isn't enforced (no lock-out) and setup says why it can't proceed. |
| `test_the_policy_can_be_turned_off_and_defaults_to_on` | "Require two-factor for admins" is on by default and an admin can switch it off. |
| `test_admins_can_change_the_policy_and_it_is_audited` | Policy changes are admin-only and recorded in the log. |
| `test_the_policy_only_applies_to_administrators` | Members are never forced to enrol. |
| `test_an_admin_without_two_factor_gets_a_limited_session_when_it_is_required` | An admin who hasn't enrolled can sign in, but only to set up two-factor. |
| `test_enrolling_upgrades_the_session_and_rotates_its_id` | Finishing enrolment turns the limited session into a full one with a new ID. |
| `test_the_first_account_on_a_fresh_install_must_enrol_when_the_policy_is_on` | The first admin lands on the two-factor setup straight after registering. |
| `test_an_admin_cannot_turn_two_factor_off_while_the_policy_requires_it` | While the policy is on, an admin can't disable their own two-factor. |
| `test_a_members_two_factor_is_optional_but_available` | A member can turn two-factor on and off. |
| `test_a_reset_admin_is_asked_to_enrol_again_when_it_is_required` | After a command-line reset, the admin signs in with the password and is asked to enrol again. |
| `test_resetting_two_factor_from_the_command_line_helper` | The reset command clears the secret and recovery codes and signs the account out everywhere. |
| `test_the_reset_script_asks_for_exactly_one_email` | The reset command refuses missing or extra arguments. |
| `test_bad_password_changes_are_refused_and_change_nothing` *(5 inputs)* | Wrong current password, short, over-long or unchanged new passwords are refused and nothing changes. |
| `test_changing_the_password_signs_out_other_sessions_and_rotates_this_one` | After a password change, other devices are signed out and this one gets a new session ID. |
| `test_password_change_attempts_are_rate_limited` | Repeated password-change attempts are rate limited. |
| `test_the_account_page_lists_sessions_and_can_end_them` | The sessions list shows device, address and time, and ending one works. |
| `test_sign_out_everywhere_else_keeps_only_this_session` | The endpoint behind "Sign out everywhere else" keeps the current session only. |
| `test_ending_the_current_session_through_the_list_clears_the_cookie` | Ending your own session from the list signs you out and clears the cookie. |
| `test_logout_ends_the_session_everywhere_and_the_cookie_is_cleared` | Logging out deletes the session on the server as well as the cookie. |
| `test_malformed_session_ids_in_the_url_are_not_found` *(4 inputs)* | Odd identifiers in the session URL get "not found". |
| `test_one_user_cannot_end_another_users_session_by_guessing_its_id` | Even a valid public ID can only be ended by the account that owns it. |
| `test_cross_site_requests_to_the_new_endpoints_are_refused` | All the new state-changing endpoints reject requests from another site. |

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
| `test_removed_container_alert_resolves_when_others_report` | If an unhealthy container disappears and others still report, its unhealthy incident resolves and only the informational "no longer present" notice remains. |
| `test_empty_container_list_keeps_container_alerts` | If a check-in has no containers at all (Docker unreachable), existing container incidents stay open instead of being wrongly closed. |
| `test_container_sync_leaves_other_rules_alone` | Updating container incidents never touches other incidents, such as "server offline". |
| `test_overview_lists_critical_first_with_hostname_and_true_count` | The dashboard's incident list shows the most severe first, includes the server name, and reports the true total even when the list is capped. |

### test_auth_backend.py

Accounts, passwords, first-run protection and the cross-site check.

| Test | What it checks |
|---|---|
| `test_register_user_creates_record_and_hashes_password` | Registering stores the account and a bcrypt hash, never the password itself. |
| `test_valid_credentials_identify_the_user_and_open_a_session` | The right email and password identify the account and a session can then be opened for it. |
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
| `test_the_quick_one_shot_request_is_tried_first` | Stats are fetched with the fast one-shot request first. |
| `test_cpu_is_the_change_since_the_previous_check_in_and_the_first_has_none` | CPU % is worked out from the change since the last check-in; the first check-in shows no figure rather than a false 0. |
| `test_containers_do_not_share_cpu_history` | Each container's CPU is measured against its own earlier reading. |
| `test_a_counter_that_goes_backwards_skips_that_sample_and_recovers` | A container restart (counters reset) skips one sample, then measures from the new baseline. |
| `test_the_normal_request_is_used_when_one_shot_is_refused_and_remembered` | With an older proxy that refuses one-shot, the collector falls back to the normal request and stops retrying the quick one. |
| `test_a_container_that_vanished_does_not_make_one_shot_look_unsupported` | A container removed mid-collection doesn't switch the quick request off. |
| `test_hostile_cpu_counters_give_no_figure_and_no_crash` *(6 inputs)* | Text, null, boolean, list, object and NaN counters give no CPU figure and no error. |
| `test_cpu_state_for_containers_that_are_gone_is_dropped` | Remembered readings for containers that no longer exist are discarded. |
| `test_container_usage_parses_docker_stats` | Docker's raw statistics are converted correctly into CPU %, memory used and percentage (ignoring reclaimable cache), network and disk totals, and process count. |
| `test_format_ports_lists_published_bindings` | Published ports are shown as `8080->80/tcp`, once even if both IPv4 and IPv6 are bound, and unpublished ports are skipped. |

### test_conversations.py

| Test | What it checks |
|---|---|
| `test_conversation_records_are_independent_for_safe_deletion` | Deleting one assistant conversation removes only its own messages and audit entries and leaves other conversations untouched. |

### test_container_checks.py

Container-level checks: Docker health, how containers ended, what appeared or
disappeared, image changes, and memory-leak trends. A large part of this file tests the
validation of container data, because everything a collector reports is untrusted input.

**Docker health (collector side)**

| Test | What it checks |
|---|---|
| `test_a_host_without_docker_is_not_configured_not_broken` | A host with no Docker socket is reported as *not configured*, not as a failure. |
| `test_a_configured_but_missing_proxy_socket_is_unreachable` | If the Docker proxy is configured but its socket is missing, that is a failure (reason *missing*). |
| `test_a_stale_socket_nobody_listens_on_is_reported_as_refused` | A socket file left behind by a stopped proxy is reported as *refused*. |
| `test_failures_map_to_a_fixed_vocabulary_never_raw_text` *(7 inputs)* | Errors become one of five fixed reasons (timeout, refused, missing, denied, error); raw error text is never sent. |
| `test_a_working_daemon_reports_ok_with_its_latency` | A working Docker reports `ok` and how long listing containers took. |
| `test_a_daemon_error_during_listing_is_unreachable` | A Docker error while listing containers is reported as unreachable, with no containers. |
| `test_one_container_vanishing_mid_collection_does_not_fail_the_rest` | A container removed between listing and inspecting is skipped without failing the others. |
| `test_when_nothing_can_be_inspected_the_daemon_is_failing_not_empty` | If containers are listed but none can be inspected, Docker is treated as failing rather than empty. |

**Exit status and image ID (collector side)**

| Test | What it checks |
|---|---|
| `test_a_stopped_container_reports_how_it_ended` | A stopped container reports its exit code, whether it was out-of-memory killed, when it finished (to the second) and its image ID. |
| `test_a_running_container_does_not_report_a_stale_exit_status` | A running container never reports the exit status of an earlier run. |
| `test_malformed_exit_details_become_unknown` *(10 inputs)* | Strings, booleans, out-of-range or fractional exit codes, a non-boolean OOM flag and impossible finish times all become *unknown*. |
| `test_only_a_well_formed_image_id_is_passed_on` *(8 inputs)* | Only a real `sha256:` plus 64 hex characters image ID is passed on; tags, short or malformed values are dropped. |

**Docker health incidents (backend side)**

| Test | What it checks |
|---|---|
| `test_docker_unreachable_alerts_only_after_it_worked_and_only_when_sustained` | A critical incident opens after Docker worked before and then fails several check-ins in a row, and resolves when it recovers. |
| `test_a_host_that_never_had_working_docker_never_alerts` | A host that never had working Docker, or has none, never gets an incident. |
| `test_an_open_docker_alert_survives_the_purge_of_the_samples_that_proved_it_worked` | An open incident stays open even after retention deletes the old samples that showed Docker once worked. |
| `test_slow_docker_warns_when_sustained_and_clears_when_fast` | Slow container listings over the threshold, several times in a row, raise a warning that clears when Docker is fast again. |
| `test_missing_docker_data_from_old_collectors_never_alerts` | Collectors that send no Docker status never trigger Docker incidents. |
| `test_malformed_docker_fields_are_dropped` | Unknown status values, free-text reasons and absurd latencies are discarded. |

**Exit-code incidents (backend side)**

| Test | What it checks |
|---|---|
| `test_stopped_containers_are_classified_by_how_they_ended` *(5 inputs)* | Exit code 1 and 127 give a warning (127 explained as "command not found"), 137 and 143 an informational notice (usually a deliberate stop), and an out-of-memory kill is critical. |
| `test_clean_exits_unknown_and_running_containers_never_alert` *(5 inputs)* | Clean exits (0), unknown exit codes or times, and running or merely created containers never raise an incident. |
| `test_an_old_exit_is_not_an_incident_and_a_restart_clears_it` | An exit older than the event window raises nothing, and a container that runs again clears its incident. |

**What a collector may send about containers**

| Test | What it checks |
|---|---|
| `test_unusable_container_records_are_dropped` *(11 inputs)* | Records that are not objects, or lack a usable ID or status, are dropped. |
| `test_container_text_is_made_printable_and_bounded` | Control characters are replaced and every text field is trimmed and capped (names at 128 characters). |
| `test_a_missing_name_falls_back_to_the_container_id` | A container without a name is shown by the start of its ID. |
| `test_numeric_fields_must_be_real_in_range_numbers` *(9 inputs)* | Strings, booleans, NaN, infinity, negative or absurdly large values become *unknown* instead of being stored. |
| `test_sane_numbers_pass_through` | Normal values are stored unchanged. |
| `test_finish_times_must_be_exact_recent_utc_timestamps` *(9 inputs)* | Only exact UTC timestamps between the year 2000 and a few minutes from now are accepted. |
| `test_a_valid_finish_time_becomes_an_aware_datetime` | A valid finish time is stored as a timezone-aware date. |
| `test_unknown_fields_are_not_passed_to_the_database` | Extra fields (including attempts to set `id`, `server_id` or `time`) are ignored. |
| `test_the_api_schema_already_refuses_container_entries_that_are_not_objects` | The API itself rejects container lists with non-object entries. |
| `test_hostile_container_records_never_break_ingest_or_other_data` | Odd records are cleaned or dropped, and the host's own numbers in the same check-in are still recorded. |
| `test_no_more_than_500_containers_are_accepted_per_check_in` | At most 500 containers are stored per check-in. |
| `test_a_collector_too_slow_for_its_interval_warns_when_sustained_and_clears` | Gathering data over 80% of the check interval for 3 check-ins in a row opens a warning, and it clears when collection is fast again. |
| `test_collection_within_the_limit_never_warns` | Collection just under the limit never warns. |
| `test_one_slow_check_in_among_fast_ones_does_not_warn` | A single slow check-in isn't enough. |
| `test_old_collectors_that_do_not_send_the_duration_never_trigger_it` | Collectors that don't report the duration (or skip it sometimes) never trigger the warning. |
| `test_the_limit_follows_the_servers_check_interval` | The limit is a share of that server's own interval, so a 15 s interval is stricter than a 60 s one. |
| `test_the_limit_is_an_editable_validated_threshold` | The 80% default, its 1–100 bounds and rejection of text, booleans and null. |
| `test_a_hostile_collection_time_is_dropped_not_stored` *(8 inputs)* | Text, negative, absurd, boolean, fractional, null, list and NaN durations are discarded. |
| `test_a_sane_collection_time_is_stored` | A normal duration is saved with the check-in. |
| `test_the_longest_container_name_still_fits_every_incident_key` | Even the longest allowed name keeps every incident key within the database limit, so a crafted name cannot make check-ins fail. |

**Inventory changes and image drift**

| Test | What it checks |
|---|---|
| `test_the_first_check_in_is_only_a_baseline` | The first report creates no events. |
| `test_new_and_missing_containers_are_recorded` | Containers that appeared or disappeared since the last check-in are recorded. |
| `test_a_removed_container_opens_an_informational_incident_that_clears_with_the_window` | A removed container opens an informational incident that clears after the event window. |
| `test_a_container_that_comes_back_clears_its_removal_incident` | A container that returns clears its removal incident. |
| `test_containers_are_matched_by_name_so_recreation_is_not_a_change` | Recreating a container (new ID, same name, same image) is not reported. |
| `test_an_empty_list_never_looks_like_everything_vanished_or_reappeared` | An empty list (Docker unreachable) neither removes everything nor makes everything look new when it returns. |
| `test_a_changed_image_is_recorded_and_raises_an_informational_incident` | A container that comes up on a different image build is recorded with the short IDs, and raises an informational incident. |
| `test_the_same_image_or_an_unknown_one_is_not_a_change` | The same image, or an unknown image ID on either side, is not a change. |
| `test_a_burst_of_changes_is_capped_per_check_in_with_one_notice` | A burst of changes records at most 10 events per check-in plus a single notice. |
| `test_sustained_churn_is_capped_per_hour` | Containers constantly coming and going cannot flood the database: events are capped per hour with one notice. |
| `test_container_changes_have_their_own_list_and_do_not_crowd_the_system_row` | Container changes appear in the Containers section, not in the System row, so they cannot push reboots out of it. |
| `test_the_container_change_list_is_bounded` | The dashboard's container-change list is capped at 15 rows. |
| `test_container_events_follow_the_normal_retention` | Container change events are deleted with the other old data after the retention period. |

**Memory-leak trends**

| Test | What it checks |
|---|---|
| `test_steady_growth_is_flagged_as_a_possible_leak` | A container whose memory rises steadily over the window gets a warning with the rate and, when a memory limit is set, the time until it is reached. |
| `test_flat_noisy_and_sawtooth_memory_is_not_a_leak` | Flat, noisy or repeatedly rising-and-falling memory is not flagged. |
| `test_a_restart_or_recreation_in_the_window_resets_the_trend` | A restart or recreation inside the window disqualifies the trend, since it resets memory. |
| `test_too_little_history_or_a_slow_or_proportionally_small_rise_is_not_flagged` | Missing data, a rise below the MB-per-hour minimum, or a tiny proportional rise on a large container are not flagged. |
| `test_containers_that_are_not_running_now_are_not_analysed` | Containers that are not running now are skipped. |
| `test_the_flag_clears_when_the_growth_stops` | The warning clears on the next hourly analysis once growth stops. |
| `test_analysis_is_bounded_to_100_containers_per_server` | At most 100 containers per server are analysed. |
| `test_a_fit_needs_distinct_points` | The straight-line fit refuses degenerate input and computes slope and fit quality correctly. |
| `test_the_new_thresholds_have_safe_defaults_and_are_validated` | The new thresholds (slow Docker, leak window, rate and growth) have safe defaults, and out-of-range, fractional, text or boolean values are rejected. |

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
| `test_the_four_read_requests_are_allowed` *(6 inputs)* | The exact requests the collector needs (list containers, inspect one, one stats snapshot, and the quick one-shot stats form) are allowed. |
| `test_only_get_is_allowed` *(8 inputs)* | Every method except GET (POST, PUT, DELETE, PATCH, HEAD, OPTIONS, lowercase `get`, CONNECT) is refused. |
| `test_everything_else_is_refused` *(63 inputs)* | Everything outside the allow-list is refused: other endpoints (`/info`, images, exec, start, kill, export, archive, logs), extra or changed query strings, path traversal, encoded or padded paths, injected line breaks, odd container IDs. |
| `test_inspect_output_drops_environment_and_everything_unneeded` | The container description is cut down to the needed fields (including exit code, out-of-memory flag, finish time and image ID); environment variables (often secrets), mounts, labels, host configuration and Docker's free-text error message never leave the proxy. |
| `test_list_and_stats_output_are_cut_down` | The same filtering applies to the container list and stats. |
| `test_unexpected_upstream_shapes_do_not_crash_the_filters` *(7 inputs)* | Strange or malformed answers from Docker don't crash the proxy. |
| `test_the_one_shot_request_is_passed_on_and_answers_are_cut_down` | The quick stats request reaches Docker unchanged and the answer is cut down to the usual fields. |
| `test_cpu_comes_from_the_change_between_two_collections_through_the_proxy` | Through the real proxy the collector uses only the quick request and remembers the CPU counters for next time. |
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

### test_history.py

The history endpoints that charts will use for any range from the last hour to all time. The key promise is
that the answer is the same whether or not the background summarising has caught up.

| Test | What it checks |
|---|---|
| `test_the_last_hour_is_one_point_per_check_in_with_its_average_and_maximum` | The last hour is one point per check-in (both ends included), each with average and maximum, oldest first, with UTC times. |
| `test_a_week_of_samples_is_grouped_into_at_most_five_hundred_points_without_losing_anything` | A week is grouped into at most 500 points; the check-in count, the overall average and the highest maximum are unchanged by grouping. |
| `test_grouping_keeps_the_exact_average_and_maximum_of_each_group` | Three check-ins grouped into one five-minute point give their exact average and maximum. |
| `test_a_gap_stays_a_gap` | Two bursts of data hours apart have no points between them. |
| `test_a_metric_that_was_never_reported_is_null_not_zero` | A metric the collector never sent comes back as null, not 0. |
| `test_a_server_without_data_gives_an_empty_series` | A server with no data gives an empty series for every range, without errors. |
| `test_thirty_days_use_hourly_summaries_and_match_the_samples` | 30 days is answered from hourly summaries whose figures equal the samples they summarise. |
| `test_the_hour_that_has_not_been_summarised_yet_is_still_in_the_answer` | The newest, not yet summarised hour is built from the samples so the right edge is current. |
| `test_the_answer_does_not_depend_on_how_far_the_rollup_job_has_got` *(4 inputs)* | For 30 days, 90 days, 1 year and all time, the answer is identical with no summaries, part-way through catching up, and fully caught up. |
| `test_every_sample_is_counted_exactly_once_in_the_all_time_view` | All time is monthly and every check-in is counted exactly once. |
| `test_weekly_points_start_on_mondays_and_the_current_week_is_partial` | Weekly points start on Mondays and the unfinished week is included. |
| `test_a_long_series_is_thinned_to_the_point_limit` | 720 hourly rows are merged into at most 500 points without losing check-ins. |
| `test_history_starts_at_the_first_check_in_not_at_a_bucket_boundary` | "History starts" is the first check-in, not the midnight or hour boundary of a summary, even after summarising. |
| `test_when_old_samples_are_gone_the_oldest_summary_that_reaches_back_decides` | Once old samples are purged, the oldest hourly (then daily) summary that reaches further back gives the start. |
| `test_a_series_with_no_data_has_no_start` | A series without data has no start. |
| `test_a_container_series_has_cpu_memory_limit_and_down_counts` | A container series has CPU, memory, its limit and restarts (limit and restarts only with a maximum), and counts the check-ins when it wasn't running. |
| `test_a_mount_series_is_picked_by_path` | A filesystem is chosen by its path, and other mounts are excluded. |
| `test_container_summaries_agree_with_the_samples` *(2 inputs)* | Container history is identical before and after summarising. |
| `test_the_endpoint_returns_the_series_for_a_range` | The server endpoint returns the requested metrics for a range. |
| `test_without_a_range_the_original_answer_is_unchanged` | Without a range, the dashboard's original answer is unchanged. |
| `test_unknown_ranges_and_metrics_are_refused` *(9 inputs)* | Unknown ranges, empty, doubled, oversized or odd metric lists are refused (422). |
| `test_unknown_servers_mounts_and_containers_are_not_found` | Unknown servers, mounts and containers give 404; a relative mount path, a missing mount or an oversized id are refused. |
| `test_the_container_and_mount_endpoints_return_their_series` | The container and mount endpoints return their series with names. |
| `test_signing_in_is_required` | All the history endpoints refuse requests without a session. |

### test_indexes.py

The indexes on the metrics tables. The live migration is checked by hand against PostgreSQL
(these tests use SQLite and check what the models declare).

| Test | What it checks |
|---|---|
| `test_the_metrics_tables_carry_exactly_the_indexes_they_need` | Each metrics table has only the planned indexes: `time` for the purge, `(server_id, time)` (or `(server_id, container_id, time)` for containers) for reads. |
| `test_deleting_a_server_and_purging_old_rows_can_both_use_an_index` *(4 inputs)* | Every metrics table keeps an index starting with `server_id` (deleting a server cascades through it) and one on `time` alone (the hourly purge). |
| `test_a_primary_key_is_never_indexed_a_second_time` *(4 inputs)* | No table gets a redundant index on its own `id`. |

### test_network_checks.py

Network checks: the host's real traffic totals, which ports it listens on, whether it can
reach the internet, and whether its firewall configuration changed. The collector parts read
`/proc` and files, so most tests build a fake host in a temporary folder and feed the
parsers odd, huge or hostile content.

**Host network totals (collector)**

| Test | What it checks |
|---|---|
| `test_network_totals_come_from_the_hosts_physical_interfaces` | Totals come from the host's real network cards only. The collector container's own interface is never used, and Docker's virtual interfaces are skipped so traffic isn't counted twice. |
| `test_without_sysfs_virtual_interfaces_are_skipped_by_name` | If the hardware list isn't visible, loopback, bridge and virtual interfaces are skipped by name. |
| `test_inside_a_container_an_unreadable_host_table_is_unknown_not_the_containers_own` | If the host's table can't be read, the totals are *unknown* instead of silently reporting the container's own traffic as the host's. |
| `test_only_a_collector_on_the_host_itself_may_fall_back_to_proc_net` | Only a collector running directly on the host may fall back to its own network view. |
| `test_garbage_and_oversized_network_tables_are_survived` | Malformed lines are skipped, and an oversized table is refused. |

**Listening ports (collector)**

| Test | What it checks |
|---|---|
| `test_listeners_are_read_with_their_scope` | Listening ports are read for TCP and UDP, IPv4 and IPv6, and classified as loopback only, all interfaces or one address; ordinary connections and connected UDP sockets are not listeners. |
| `test_the_same_port_on_ipv4_and_ipv6_is_one_listener` | The same port on IPv4 and IPv6 counts once. |
| `test_ephemeral_ports_are_skipped_but_exposed_high_tcp_ports_are_not` *(9 inputs)* | Random high ports used by programs talking out (loopback ones, and all UDP ones) are skipped, while high TCP ports reachable from other machines (such as Docker's random published ports) are kept. The collector and backend apply the same rule. |
| `test_missing_or_garbage_tables_are_unknown_or_ignored` | Nothing readable means *unknown*; junk lines and impossible ports are ignored. |
| `test_one_unreadable_table_makes_the_whole_answer_unknown_not_partial` | If any one of the four tables can't be read, the whole answer is unknown, so a partial picture can never look like closed ports. |
| `test_a_table_longer_than_the_cap_is_unknown_because_a_truncated_read_could_fake_closed_ports` | A table over the line limit is treated as unknown, not truncated. |
| `test_a_busy_hosts_connection_table_does_not_hide_its_listeners` | Thousands of ordinary connections don't hide the real listeners, and the table is streamed rather than loaded into memory. |
| `test_a_container_never_reports_its_own_sockets_as_the_hosts` | A collector in a container never mistakes the container's own sockets for the host's. |
| `test_the_listener_report_is_capped` | At most 512 listeners are reported. |

**Internet check (collector, off by default)**

| Test | What it checks |
|---|---|
| `test_connectivity_is_off_unless_targets_are_configured` | With no targets configured the check does nothing and sends no data. |
| `test_valid_targets_are_parsed_deduplicated_and_capped` *(3 inputs)* | `host:port` targets (hostname or IPv4) are accepted, deduplicated and capped at 5. |
| `test_malformed_targets_are_ignored` *(19 inputs)* | URLs, spaces, shell characters, bad ports, IPv6 literals, mistyped IPs, usernames, paths and over-long names are all ignored. |
| `test_results_are_summarised` *(5 inputs)* | The results become ok, degraded, DNS failing or down. |
| `test_a_reachable_target_reports_ok_with_its_latency` | Reachable targets report ok and the typical connection time. |
| `test_unresolvable_names_are_reported_as_dns_failure` | Names that don't resolve are reported as a DNS failure, not as the internet being down. |
| `test_a_hung_probe_cannot_stall_the_check_in` | A probe that hangs cannot delay the collector's report beyond a fixed deadline. |
| `test_probes_only_connect_and_never_send_data` | The probes open a connection and send nothing. |

**Firewall files (collector)**

| Test | What it checks |
|---|---|
| `test_firewall_files_are_reduced_to_a_hash_a_rule_count_and_the_ufw_flag` | Each known firewall file is reduced to a short hash and a rule count, plus whether ufw is enabled. |
| `test_the_firewall_report_never_contains_the_rules_themselves` | No rule text, address or comment ever appears in what is reported. |
| `test_ufw_disabled_is_reported_and_changes_the_fingerprint` | Turning ufw off is visible, and changes the fingerprint. |
| `test_no_known_firewall_file_means_none` | No firewall files means no report. |
| `test_symlinked_oversized_and_binary_firewall_files_are_handled_safely` | Symlinks are not followed, files over 1 MB are skipped, and binary content doesn't crash anything. |

**Validation and state (backend)**

| Test | What it checks |
|---|---|
| `test_a_listener_report_must_be_a_list` *(5 inputs)* | Anything but a list is rejected. |
| `test_listener_entries_are_validated_deduplicated_and_filtered` | Unknown protocols and scopes, bad or non-integer ports, junk entries and ephemeral ports are dropped, and duplicates merged. |
| `test_the_listener_list_is_capped` | The backend accepts at most 512 listeners per report. |
| `test_firewall_reports_are_validated` | Only known file names with a proper 16-hex-character hash and a sane rule count are accepted; the ufw flag must be a real boolean. |

**Listening-port changes and incidents (backend)**

| Test | What it checks |
|---|---|
| `test_the_first_listener_report_is_only_a_baseline` | The first report creates no events. |
| `test_a_new_exposed_listener_is_recorded_and_raises_a_warning` | A new port reachable from other machines is recorded and raises a warning incident. |
| `test_a_new_loopback_listener_is_an_event_without_an_incident` | A new loopback-only port is recorded, but raises no incident. |
| `test_a_closed_listener_is_recorded_and_clears_its_incident` | A closed port is recorded and clears its incident. |
| `test_the_listener_incident_clears_with_the_event_window` | The incident clears by itself after the event window. |
| `test_unchanged_listeners_create_no_events_or_duplicate_rows` | Unchanged listeners create nothing and never duplicate stored rows. |
| `test_an_empty_or_missing_listener_report_never_looks_like_everything_closed` | An empty list, a missing field or garbage is ignored rather than read as every port closing. |
| `test_a_burst_of_new_listeners_is_capped_with_one_notice` | A burst of new ports records at most 10 events per check-in plus one notice. |
| `test_sustained_port_flapping_is_capped_per_hour` | Ports opening and closing constantly cannot flood the database. |
| `test_listener_state_is_removed_with_the_server` | Deleting a server removes its stored listeners and firewall fingerprints. |

**Firewall changes and incidents (backend)**

| Test | What it checks |
|---|---|
| `test_the_first_firewall_report_is_only_a_baseline` | The first report creates no events. |
| `test_unchanged_firewall_files_report_nothing` | Unchanged files report nothing. |
| `test_changed_rules_are_informational_and_fewer_rules_are_a_warning` | Changed rules are informational; a drop in the rule count is a warning. |
| `test_disabling_ufw_is_a_warning_and_enabling_it_is_informational` | Turning ufw off is a warning; turning it on is informational. |
| `test_a_firewall_file_disappearing_is_a_warning_and_one_appearing_is_informational` | A firewall file vanishing is a warning; a new one appearing is informational. |
| `test_all_firewall_files_vanishing_is_noticed` | If every firewall file disappears, that is noticed. |
| `test_missing_or_malformed_firewall_reports_are_ignored` | Missing or malformed reports are ignored. |
| `test_the_firewall_incident_clears_with_the_event_window` | The firewall incident clears after the event window. |

**Internet incidents (backend)**

| Test | What it checks |
|---|---|
| `test_sustained_internet_loss_warns_and_recovery_clears` | The internet being down for several check-ins in a row raises a warning that clears on recovery. |
| `test_sustained_dns_failure_is_its_own_warning` | A sustained DNS failure is reported separately. |
| `test_degraded_unchecked_and_malformed_internet_status_never_alerts` | Partly reachable, unchecked and malformed statuses never raise an incident. |
| `test_internet_latency_must_be_a_sane_number` | Absurd latency values are dropped. |

**Dashboard data and limits (backend)**

| Test | What it checks |
|---|---|
| `test_the_overview_shows_listeners_firewall_and_network_events` | The dashboard receives the listeners, firewall fingerprints, events and internet status. |
| `test_network_events_have_their_own_list_and_do_not_crowd_the_other_rows` | Network events appear in the Network row and cannot push reboots or container changes out of theirs. |
| `test_the_network_lists_are_bounded` | The dashboard's listener list is capped at 200 and the event list at 15. |
| `test_network_events_follow_the_normal_retention` | Old network events are deleted with the other old data. |
| `test_crafted_event_keys_cannot_raise_incidents_for_closed_or_loopback_ports` | Malformed or misleading stored event keys cannot raise an incident for a port that isn't open or isn't exposed. |

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

### test_rollups.py

The background job that summarises check-in samples into hourly, daily, weekly (Monday to Sunday) and
monthly rows, all in UTC. If these fail, long-range history could be wrong, duplicated, or deleted before
it was kept.

| Test | What it checks |
|---|---|
| `test_buckets_start_on_utc_boundaries_and_weeks_on_monday` | Hours, days and months start on the boundary and a week starts on Monday (Sunday 23:59 belongs to the week before the next Monday). |
| `test_a_time_with_another_offset_is_converted_to_utc_first` | A time with another offset (or none) is converted to UTC before choosing its bucket. |
| `test_the_following_bucket_handles_month_ends_and_leap_years` | The next month after December is January, February in a leap year ends correctly, and weeks cross new year. |
| `test_an_hour_is_summarised_with_average_maximum_and_count` | An hour's row has the correct check-in count, average and maximum, and a metric that was never reported stays unknown (not zero). |
| `test_a_metric_missing_from_some_samples_is_averaged_over_the_ones_that_have_it` | A metric missing from some check-ins is averaged over those that have it. |
| `test_a_history_of_nothing_but_unknowns_still_counts_check_ins_and_does_not_break` | Check-ins that report nothing still produce rows with unknown averages and don't break the job. |
| `test_only_completed_buckets_are_written_after_a_short_grace_period` | An hour is written only after it ended plus a five-minute grace, so check-ins in flight are not missed. |
| `test_running_twice_changes_nothing` | A second run with nothing new builds nothing and leaves every row as it was. |
| `test_rebuilding_a_bucket_replaces_it_instead_of_duplicating_it` | Rebuilding a bucket (for example after losing the watermark) replaces its rows and includes late samples. |
| `test_servers_mounts_and_containers_are_summarised_separately` | Each server, mount and container gets its own rows. |
| `test_down_samples_count_the_check_ins_where_a_container_was_not_running` | A container's row records how many check-ins it was not running. |
| `test_a_day_is_the_samples_weighted_average_of_its_hours_and_keeps_the_maximum` | A day is the weighted average of its hours (one reading of 100 and three of 0 average 25, not 50) and keeps the highest maximum. |
| `test_each_average_remembers_how_many_check_ins_reported_it` | Every averaged metric records how many check-ins reported it. |
| `test_a_container_that_was_stopped_part_of_the_time_is_averaged_over_the_time_it_ran` | A container stopped for part of the day isn't averaged as if it were idle: its average is over the time it ran. |
| `test_hours_that_never_reported_a_metric_do_not_pull_a_day_toward_zero` | Hours without a value for a metric don't drag a day's average toward zero. |
| `test_a_week_runs_monday_to_sunday_and_is_written_only_when_sunday_has_ended` | A week covers Monday to Sunday, excludes the next Monday, and is written only once Sunday is over. |
| `test_a_month_is_built_from_its_days_including_a_leap_february` | A month is built from its days, including the 29 days of a leap February. |
| `test_data_either_side_of_new_year_lands_in_different_months_and_weeks` | New Year's Eve and New Year's Day land in different months but in the same week. |
| `test_every_tier_is_built_for_mounts_and_containers_too` | Filesystems and containers get all four tiers, not just servers. |
| `test_a_gap_stays_a_gap` | Hours, days or servers with no data get no row; nothing is filled in. |
| `test_the_first_run_summarises_the_history_that_already_exists` | On upgrade, existing samples are summarised, and only complete weeks are written. |
| `test_new_samples_are_picked_up_by_the_next_run` | Later runs continue from where the last one stopped. |
| `test_after_downtime_the_job_catches_up_a_chunk_at_a_time` | After downtime the job catches up in limited chunks until nothing is left. |
| `test_a_long_empty_stretch_cannot_stall_the_job` | A long stretch with no data at all still moves the job forward. |
| `test_the_watermark_is_stored_per_tier_and_a_damaged_one_is_ignored` | The progress marker is saved per tier, and a damaged one makes the job start over harmlessly. |
| `test_without_any_summaries_the_purge_behaves_as_before` | With no summaries yet, retention alone decides what the purge deletes (unchanged behaviour). |
| `test_samples_that_were_not_summarised_yet_are_never_purged` | The purge never deletes a sample newer than the last summarised hour, even when retention says it is old enough. |
| `test_maintenance_summarises_before_it_purges` | Maintenance summarises first, then purges, so history is kept before it is deleted. |
| `test_a_failing_rollup_job_stops_the_purge` | If summarising fails, nothing is purged that run. |
| `test_between_purges_maintenance_only_summarises` | Between hourly purges, maintenance only summarises. |
| `test_deleting_a_server_deletes_its_summaries_but_not_another_servers` | Deleting a server removes its summaries and leaves other servers' alone. |

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

### test_stats.py

The all-servers statistics and the busy-hours heatmap behind the Statistics page.

| Test | What it checks |
|---|---|
| `test_rule_keys_become_readable_groups` *(15 inputs)* | Alert rule keys become readable groups ("Disk space", "Server offline"); unknown, empty or odd keys become "Other". |
| `test_long_or_odd_rule_keys_never_leak_through` | A huge or unknown rule key is never shown as is. |
| `test_incidents_are_counted_per_day_and_severity_for_a_week` | Incidents opened are counted per day and severity, and per kind of problem. |
| `test_the_bucket_size_follows_the_range` | Hours up to a day, days up to a month, weeks up to a year and months beyond; weeks start on Monday. |
| `test_incidents_older_than_the_range_are_left_out` | Only incidents that opened inside the range are counted. |
| `test_mean_time_to_resolve_uses_only_resolved_incidents` | The average time to resolve counts resolved incidents only. |
| `test_open_incidents_are_counted_by_severity_whatever_the_range` | Incidents open right now are counted by severity, however old they are. |
| `test_an_install_without_incidents_gives_empty_answers` | No incidents gives empty, well-formed answers for every range. |
| `test_an_unknown_severity_is_counted_as_info_not_dropped` | An unexpected severity is counted as info instead of being lost. |
| `test_the_amount_of_data_read_is_capped_and_says_so` | Reading is capped and the answer says when it was cut. |
| `test_a_server_that_always_reported_is_fully_available` | A server that reported every time is 100% available; one that never reported is unknown, not 0%. |
| `test_a_server_that_was_silent_for_half_the_time_is_half_available` | A long silence lowers availability accordingly. |
| `test_a_new_server_is_not_blamed_for_the_time_before_it_existed` | A new server is judged from its first check-in. |
| `test_availability_is_exact_even_after_the_history_has_been_summarised` | Availability stays correct once history is summarised (a first version counted from midnight). |
| `test_availability_never_exceeds_one_hundred_percent` | Reporting more often than expected still caps at 100%. |
| `test_availability_works_for_every_range_without_data` | Every range works for servers without data. |
| `test_the_endpoint_returns_incidents_and_availability` | The endpoint returns both parts. |
| `test_answers_are_cached_for_a_minute_per_range_only` | Answers are cached for a minute, one entry per range. |
| `test_a_broken_cache_does_not_break_the_page` | If Redis is down the page still works. |
| `test_unknown_ranges_are_refused_and_never_become_cache_keys` *(5 inputs)* | Odd ranges are refused (422) and leave nothing in the cache. |
| `test_signing_in_is_required` | The fleet endpoint needs a session. |
| `test_the_heatmap_puts_each_hour_in_its_weekday_and_hour_cell` | Each hour lands in the right weekday and hour cell, averaged. |
| `test_the_heatmap_follows_the_viewers_time_zone` | The viewer's time zone moves cells across midnight correctly. |
| `test_the_heatmap_ignores_samples_older_than_thirty_days_and_unknowns` | Samples older than 30 days and missing readings are left out. |
| `test_the_heatmap_is_the_same_before_and_after_summarising` | The heatmap is identical before and after the history is summarised. |
| `test_the_heatmap_endpoint_validates_everything_and_caches_per_server_metric_and_offset` | Unknown metric, offset out of range or unknown server are refused and cache nothing new. |
| `test_the_heatmap_endpoint_needs_a_session` | The heatmap endpoint needs a session. |

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

## Frontend helper tests

The chart helpers (axis ticks, gaps, histograms, the hour-by-weekday grid, donut slices, the
"full in about N days" estimate, container and fleet rankings) are plain JavaScript with no
dependencies, so they have tests that run with Node's built-in runner:

```bash
cd src/frontend && node --test        # Node 20 or newer; nothing to install
```

| File | Tests | What they protect |
|---|---|---|
| `tests/chartMath.test.js` | 9 | Histogram bins and weights, the hour-by-weekday grid, donut fractions, the disk-full estimate (clear trends only), axis ticks, gaps staying gaps, weighted summaries, time labels and forecast wording. |
| `tests/fleetLogic.test.js` | 7 | Container states, top-N rankings, disk ranking and colours, online detection, availability rows, readable durations, and incident columns that fill quiet days with zeros. |

The React components themselves (the page layout, tooltips, selection) have no automated tests;
they were checked by hand in a real browser.

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
