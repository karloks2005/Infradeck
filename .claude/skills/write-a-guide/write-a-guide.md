---
name: write-guide
description: Write or update a step-by-step guide for infradeck — deploying the system, installing a collector, setting up or enabling a feature (TLS, Traefik/Caddy, the incident agent, notifications), upgrading, backup and restore. Not for explaining how something works internally (use write-docs).
---
 
# Write a guide
 
A guide gets a reader from a starting state to a working result. They follow it while doing the task, often in a terminal, sometimes when something is already broken. Explanations of internals belong in docs (write-docs); link to them instead.
 
## Before writing
- **Take everything from the repo.** Commands, file names, environment variables, ports, defaults and image names come from `compose.yml`, `docker-compose.server.yml`, `docker/`, `.env.example`, config code and existing docs. Never invent a flag or variable. If something can't be confirmed, ask me or mark it `TODO: verify`.
- **Run what you can.** If a step can be run in this environment, run it and use the real output. Say which steps you couldn't test.
- **Check what exists.** Update an existing guide in `docs/` rather than adding a parallel one. New guides go in `docs/` with a task-based kebab-case name (`deploy-server.md`, `install-collector.md`).
- **Pin down the reader and the setup.** Default reader: a homelab user comfortable with Linux and Docker, new to infradeck. Name the setups the guide covers (single LAN, multi-site/VPS, behind Traefik or Caddy) and don't mix them in one path; branch clearly or write separate guides.
## Structure
1. **Title**: the task, as an action: "Deploy the infradeck server", "Enable the incident agent".
2. **Result**: one sentence: what works when you're done.
3. **Before you start**: requirements: OS, Docker/Compose versions, open ports, DNS, access, and anything that must already be done (link to that guide).
4. **Steps**: numbered. Each step:
   - one action, with the exact command or file change in a code block;
   - where it runs (which machine: dashboard server or collector host) when that isn't obvious;
   - values the reader must replace, as `<placeholder>`, with what to put there;
   - one line of why, only when the step looks odd or skipping it would hurt;
   - how to tell it worked (expected output, a URL that responds, a log line).
5. **Verify**: the end-to-end check that the result works.
6. **Troubleshooting**: only real, likely failures: symptom → cause → fix. No generic "check your logs".
7. **Undo / roll back**: for upgrades, migrations and anything that changes data or exposes services.
8. **Next**: one or two links (related guide, the docs page explaining it).
## Style
- Imperative and direct: "Run", "Open", "Set". No filler ("simply", "just", "easily", "In this guide we will…").
- One way to do it. If there are alternatives, pick the recommended one and mention the other in one line or a separate guide.
- Complete, copy-pasteable commands: no `...` inside commands, no prompts (`$`) in the code block, outputs in a separate block.
- Show full file contents or a clear diff for config changes, plus the file's path.
- Warnings go right before the step they apply to, not at the top or bottom: deleting data, exposing a port to the internet, disabling TLS verification, storing secrets.
- Never real secrets, tokens, hostnames or IPs; placeholders only. Tell the reader where secrets belong (`.env`, never committed).
- Security defaults stay on. Don't write steps that weaken them (CORS `*`, `--insecure`, disabled TLS verification) as a shortcut. If the user truly needs it, explain the risk in place.
- English, Markdown.
## Before handing over
- Walk through the guide in order as the reader: is any step relying on something not yet done or not mentioned?
- Re-check every command, path, variable and port against the repo.
- Tell me which steps you ran, which you couldn't, and what is marked `TODO: verify`.
