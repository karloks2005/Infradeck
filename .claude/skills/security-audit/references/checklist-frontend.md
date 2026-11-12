# Checklist: frontend (`src/frontend/**`)
 
Read `src/App.jsx` (large), `StatisticsPage.jsx`, `FleetStatistics.jsx`,
`ContainerStatistics.jsx`, `ServerInsights.jsx`, `charts.jsx`, `distributions.jsx`,
`*Logic.js`, `chartMath.js`, `main.jsx`, `index.html`, `vite.config.js`, `package.json`.
 
## 1. XSS
 
The dashboard displays attacker-controlled data (container names, mount paths, event
messages, assistant text, server names). **Stored XSS in an admin context = dashboard
takeover**, so this is the main item.
 
- [ ] Search for: `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`,
      `document.write`, `eval`, `new Function`, `setTimeout("string")`, `srcdoc`,
      `postMessage` without an origin check. Trace every occurrence back to the data source.
- [ ] **Markdown/rich text** (assistant output, descriptions): is a library used? Is HTML
      disabled or sanitized (DOMPurify with a restrictive configuration)? Links from markdown
      filter out `javascript:`/`data:`.
- [ ] **URL attributes from data:** `href`, `src`, `action`, `formAction`: `http(s)` scheme
      only (plus `mailto` if needed). `target="_blank"` has `rel="noopener noreferrer"`.
- [ ] SVG from data (charts that embed strings in SVG, `<text>`, `<title>`, `<a>`): React
      escapes text, but check hand-built SVG strings and `style` objects with data.
- [ ] CSS injection: data values in `style`/`className` built from strings.
- [ ] Prototype pollution: merging objects from collector JSON (`Object.assign`, spread into
      keys from data, `obj[key] = ...` with a key from data, `__proto__`).
## 2. Authentication and secrets in the browser
 
- [ ] Tokens/sessions are **not** kept in `localStorage`/`sessionStorage`/the URL (the session
      is an HttpOnly cookie). Check what is stored in `localStorage` (sensitive data, tokens,
      TOTP, recovery codes).
- [ ] Recovery codes and the TOTP secret: shown only once, not stored in state that survives
      navigation, never end up in the URL or the console log (`console.log`).
- [ ] No API keys, `SECRETS_KEY` or internal URLs baked into the bundle
      (`import.meta.env.VITE_*` is **public**). Search for `VITE_`.
- [ ] The frontend makes no security decisions that the backend does not check (hiding an
      admin button is not access control; the backend checklist covers that, here just note
      it).
- [ ] Logout clears in-memory state; sensitive responses are not cached (nginx/`Cache-Control`
      for the API).
## 3. Generating install commands ("Connect a server")
 
The panel generates a command that the user copies and runs on a remote machine. It
contains the join token and the image digest.
 
- [ ] Every value in the command (token, digest, URL, `COLLECTOR_BIND`, name) is **validated
      or shell-quoted**. A value containing `;`, `$(...)`, `` ` ``, `'`, a newline or a space
      must not break out of the quotes. Check where the values come from (dashboard
      configuration, API response) and how they are concatenated into the string.
- [ ] The command does not use `curl | sh` or unpinned images; the digest is mandatory, and
      the display makes it clear what the user is running.
- [ ] The token is shown once, has a visible expiry, and "Copy" does not leave it in state
      history.
## 4. Network call sites and CSRF
 
- [ ] State-changing `fetch` calls send the cookie (`credentials`) only to the app's own
      origin; no calls to arbitrary URLs taken from data.
- [ ] No open redirect (`window.location = param`), no `redirect`/`next` parameter without a
      check that it is a relative path.
- [ ] Error handling does not show stack traces/secrets to the user; API responses containing
      third-party strings are displayed as text.
## 5. CSP and build
 
- [ ] The frontend works under a strict CSP without `unsafe-inline` for scripts (no inline
      `<script>` in `index.html`, no `eval` in the bundle). New sources that require widening
      the CSP must be documented.
- [ ] `vite.config.js`: the dev proxy/`server.host` does not expose the dev server;
      `build.sourcemap` is off in production or intentional; no `define` with secrets.
- [ ] `index.html`: no external scripts/fonts without SRI; no inline handlers.
- [ ] Dependencies (`package.json`): unusual or unnecessary ones, `postinstall` scripts; see
      the infra checklist for audit tools.
## 6. Availability as security
 
- [ ] Rendering does not crash or freeze the browser on hostile data: very long strings,
      thousands of containers, `NaN`/`Infinity`/negative numbers in charts (`chartMath.js`,
      `fleetLogic.js`, `containerLogic.js`), empty arrays, unknown fields. A whole-app crash
      caused by one malformed record = an attacker with a collector can disable the
      dashboard.
 