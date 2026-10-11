[← Documentation](README.md)

# Accounts and roles

- The first account created is the **admin**. It needs the install's **setup
  token** (printed in the backend log at start, or `ADMIN_BOOTSTRAP_TOKEN`). Later
  accounts are regular users. Registration is closed after the first account unless you set
  `ALLOW_REGISTRATION=true` (the backend then prints a security warning at every start, because anyone
  who can reach the dashboard could create an account).
- **Passwords must be at least 16 characters** (and at most 72 bytes). A few random
  words make a good passphrase. The rule applies to new accounts; existing
  accounts keep working with their current password.
- **Account page** (sidebar → Account), for every user: change your password (needs
  the current one; signs out your other devices), turn on **two-factor sign-in**,
  and review or end your sessions (**Sign out everywhere else**). See
  [Two-factor sign-in](accounts.md#two-factor-sign-in).
- Everyone signed in can see all servers, containers and incidents.
- Only admins can add, approve, dismiss or delete servers, change check
  intervals, manage the enrollment key and open the **Admin** page.
- **Admin** page settings that are connected today: alert thresholds (including
  per-mount disk thresholds), how long metrics are kept (default 30 days; older
  check-in samples and system events are deleted hourly, but only after the check-ins have
  been summarised; the summaries behind the Statistics page are not deleted yet, and a
  retention setting for each level is planned), and turning
  chat with the assistant on or off. The other sections on the page show
  placeholder data and aren't saved yet.

## Two-factor sign-in

Two-factor uses a one-time code from any authenticator app (TOTP, the same standard
as Google Authenticator, Aegis or a password manager).

1. **Create a key once.** Secrets are stored encrypted, so the stack needs a key.
   Generate one and put it in `.env` as `SECRETS_KEY` (see `example.env`):

   ```bash
   docker compose run --rm --no-deps backend python -m src.backend.secrets_box
   ```

   Back this key up with your other secrets. **If you lose it, authenticator codes
   stop working** (recovery codes still do, and so does the reset command below).
   To rotate it, put the new key first and keep the old one after a comma.
2. **Turn it on** under Account → Two-factor sign-in. From a normal session it first asks for your
   password again (the forced first enrolment of an admin doesn't). You get a setup key and an
   `otpauth://` link to paste into your authenticator app (no QR code yet), confirm
   with a code, and are shown **10 recovery codes once**. Each works one time.
3. **Signing in** then asks for the code after the password. A recovery code can be
   typed instead.

**Admins must use it by default.** *Admin → Users → Require two-factor for admins* is
**on**. An admin without two-factor can still sign in, but only to the Account page
until they finish setting it up, so nobody is locked out. A member's two-factor is
optional. The setting is only enforced when `SECRETS_KEY` is set; without it the
backend prints a warning at start and the policy does nothing.

**Locked out?** An admin who lost both the authenticator and the recovery codes can
reset an account from the host:

```bash
docker compose exec backend python src/backend/reset_2fa.py someone@example.com
```

It turns two-factor off for that account and signs it out everywhere. With the
policy on, the account is asked to set up two-factor again at its next sign-in.
