---
name: simplify-existing-code
description: Use when asked to simplify, clean up or de-bloat existing code that is overcomplicated — unnecessary abstractions, layers, wrappers, unused parameters, dead code — without changing its behavior. Not for writing new code or for small targeted fixes (minimal-diff).
---

# Simplify existing code

Goal: less code, same behavior. Understand what the code does before changing anything.

## Scope
- One module or area per run. If the request is broader, propose an order and start with the first.
- Splitting a file that is too long is a separate step. Don't mix it with simplification in the same diff.

## Process

1. **Pin down current behavior.** Read the code and summarize what it does, including edge cases and error paths. Run the existing tests and lint for that area and record the result.
   - If the code has no tests, say so and offer to first write tests that capture current behavior (placement per `TESTS.md`). Don't simplify untested code without my OK.
2. **Find candidates.** Typical bloat:
   - Backend: abstract classes or interfaces with one implementation; wrappers that only forward a call; parameters that always receive the same value; config nobody sets; branches that can't run; duplicated logic; exceptions that are swallowed (`except: pass`) or caught only to be re-raised; unused functions, imports and models.
   - Frontend: `useEffect` or state for values that can be derived; components that only wrap a single element; unused props, hooks and helpers.
3. **Propose before editing.** List each candidate with: what it is, why it's safe to remove or simplify, and risk (low/medium/high). Wait for my approval before changing code.
4. **Change in small steps.** One kind of simplification per step; run tests and lint after each step.
5. **Report** (see below).

## Before removing anything "unused"
A search with no hits is not proof. Also check:
- dynamic references: string routes in `src/frontend/src/api.js`, `getattr`, FastAPI dependencies, Alembic migrations, agent prompt `.md` files;
- callers outside this repo's runtime: collectors on other machines may run older versions.
Deleting whole files requires asking first (see CLAUDE.md).

## Don't change
- **Interfaces other parts depend on**: HTTP endpoints and payloads used by the frontend or collectors, the database schema (needs a migration, so ask first), the collector ↔ backend protocol.
- **Behavior at the edges**: error messages, status codes, processing order, return types.
- **Security code, even if it looks redundant**: authorization checks, input validation, rate limiting, `masking.py`, `secrets_box.py`. If you think one is truly redundant, flag it and explain; don't remove it.
- **The collector's stdlib-only rule**: never "simplify" by adding a third-party package.
- Don't rewrite code into a "nicer" pattern unless it actually reduces complexity.

## Report
At the end, briefly state:
- what was removed or simplified, and lines before → after;
- how unchanged behavior was verified (which tests and lint ran);
- what you noticed but didn't touch, and why.