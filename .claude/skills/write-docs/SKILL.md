---
name: write-docs
description: Write or update explanatory documentation for infradeck — how a component, feature or design works and why it was built that way (architecture, data flow, design decisions). Not for step-by-step instructions like deployment or setup guides (use write-guide).
---
 
# Write documentation
 
Documentation explains how something works and why. The reader wants to understand the system well enough to change it, debug it or decide about it. Step-by-step instructions belong in a guide (write-guide), not here.
 
## Before writing
- **Read the code first.** Every claim must come from the code, config or an existing doc you checked, never from what a typical FastAPI/React project would do. If something can't be confirmed, ask me or mark it `TODO: verify`; don't guess.
- **Check what exists.** Look in `docs/` and `floorplan/`. Update the existing page instead of creating a parallel one. A new file goes in `docs/` with a short kebab-case name (`incident-agent.md`).
- **Know the reader.** Default: a developer who knows the stack but not this codebase. If the page is for self-hosting users, say so and leave out internals they don't need.
## Structure
Use only the sections the topic needs:
1. **What it is**: one or two sentences: what this part does and where it sits in the system.
2. **How it works**: the flow in the order it actually happens: what triggers it, which components take part (with file paths), what data moves between them, where state is kept (Postgres, Redis, memory).
3. **Why it's built this way**: the decisions that aren't obvious from the code, with the reason and the trade-off. ("Collectors are stdlib-only so they install on any host without pip and keep the signed image small.") If the reason isn't known, ask me rather than inventing one.
4. **Limits and gotchas**: what it doesn't do, known edge cases, what breaks if you change X.
5. **Related**: links to code, other docs, guides.
A diagram (Mermaid) is worth adding only when three or more components exchange data. Otherwise prose is clearer.
 
## Style
- Lead with the point. No introductions ("In this document we will…"), no summaries that repeat the page, no filler ("It's important to note that…", "simply", "easily").
- Every sentence must tell the reader something they wouldn't know otherwise. If deleting a sentence loses nothing, delete it.
- Short paragraphs; one idea each. Use lists for parallel items, prose for reasoning, since "why" needs connected sentences, not bullets.
- Concrete over abstract: name the file, function, table, Redis key or endpoint. Use real examples from the codebase.
- Same term for the same thing throughout (don't alternate "collector", "agent" and "probe"; note that "agent" already means the incident agent).
- Don't document what the code says plainly (every parameter, every field). Document what you can't see from one file: flow, contracts, reasons.
- Never put real secrets, tokens, hostnames or IPs in examples; use obvious placeholders (`<your-domain>`, `example.com`).
- English, Markdown.
## Before handing over
- Re-check every file path, name and command against the repo.
- Read it once as the target reader: can they explain how it works and why after one read? Cut anything that doesn't help with that.
- Tell me what you couldn't verify, and which other docs may now be outdated.