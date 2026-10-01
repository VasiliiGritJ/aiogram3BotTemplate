# AGENTS.md — project rulebook

## Reading contract

**DEFAULT READ (every task):**

- `AGENTS.md`
- `NEXT_TASK.md`

**CONDITIONAL READ:**

- `PROJECT_SPEC.md` — only when a task affects product logic, user UX, training content, the commercial model, or needs a product decision.

`ROADMAP.md`, `DEV_WORKFLOW.md`, `STAGE_0.md`, and `STAGE_0_REPORT.md` are reference/history only. They are not required reading for ordinary work and cannot override the two active documents or the owner's latest instruction.

## Purpose and precedence

The owner is not a programmer. Leave the project reproducible, safe, and understandable.

Priority: latest explicit owner instruction → `NEXT_TASK.md` → applicable `PROJECT_SPEC.md` → this rulebook → current code. Historical documents are context only. Do not invent product decisions about money, health, security, or material UX; ask only when the decision is truly missing.

Communicate with the owner in Russian. User-facing bot text is Russian unless the task says otherwise. Python names and technical comments may follow normal English conventions.

## Operating model

- Work autonomously within the approved scope: inspect, diagnose, implement, test, fix ordinary errors, and leave a clear checkpoint.
- Treat one owner prompt as authorization for one complete safe block. Do not split it into avoidable micro-confirmations.
- Ask for at most one owner action per stage. Bundle real risk boundaries together; if a boundary is explicitly authorized in the current scope, do not ask again.
- Prefer small, understandable changes. Avoid speculative rewrites, microservices, and new dependencies when standard library or existing packages suffice.
- Run independent safe checks in parallel when practical. Parallelize external dependencies where that reduces waiting without widening scope.
- A blocker must be reduced with evidence, not merely moved to the owner. Never claim a GUI, credential, deploy path, process, or external state exists until it was actually checked.
- After two unsuccessful attempts at the same path, stop retrying that path; diagnose the root cause and choose a different safe route or report the concrete blocker.
- During **LAUNCH LOCK**, change only the explicitly approved launch-blocking scope. No design, refactor, or product expansion by implication.

## Model choice

- Default: **Terra + High**.
- Use **Sol + High** only for difficult architecture, database, payment, security, concurrency/race safety, or critical review work.

## Safety, secrets, and external systems

- Never expose, log, commit, or place real secrets in examples. Protect `.env`, `storage/.env`, tokens, provider keys, passwords, and personal credentials.
- Before asking for or rotating a secret, prove a secure channel exists. If no secure channel is actually available, say so; do not pretend a prompt/window/credential store exists.
- Keep runtime secrets out of the repository, shell history, logs, chat, and unapproved persistent stores. Use documented environment or production secret configuration only when authorized.
- Production deployment must be reproducible: identify the artifact, exact deploy path, destination, and verification method. Do not rely on an unverified one-off manual path.
- Real payments, production DB work, secret entry/change, process start/stop/restart, deployment, external API calls, system settings, destructive operations, and infrastructure changes are risk boundaries. Stop only if they are not explicitly authorized by the current scope.
- Do not make external payments, send mass Telegram messages, or change production infrastructure without explicit scope authorization.

## Data, database, and payments

- `db.db` is protected local data: never stage, reset, restore, revert, delete, or use it for experiments. Test migrations on a temporary DB or safe copy first.
- SQLite uses synchronous SQLAlchemy; close sessions correctly and keep commits/rollbacks predictable. Store durable product state in DB, not only in UI/FSM text. Store dates consistently in UTC.
- Schema changes must be reproducible. Before authorized real migration: stop writers, make and verify a SQLite-consistent backup, apply only approved migrations, then verify schema, `quick_check`, and FK integrity.
- Payment amounts must not rely on `float`. Provider confirmation is authoritative only after signature/identity/amount validation. Repeated notifications must be idempotent and cannot grant access twice.
- AI must not directly rewrite historical results or subscription state. Training and health behavior must remain conservative; do not provide diagnoses.

## Implementation and UX

- Use aiogram 3 idiomatically; avoid blocking synchronous network calls in async handlers.
- FSM is for short-lived dialogue state; durable user, workout, access, and payment state belongs in DB.
- Verify authorization on every admin/callback action.
- Keep user messages short, clear, and free of tracebacks or technical IDs. Buttons must describe the next real action. During a workout, the current exercise and set must be visible.
- If an existing defect blocks the approved task, fix it minimally with a regression test. Otherwise record it in the final report or `NEXT_TASK.md`; do not expand scope.

## Testing and checkpoints

- Add targeted tests for new business logic, especially access/trial, dates, payments, idempotency, admin authorization, workout transitions, and progression.
- Run targeted tests while developing. Fix ordinary implementation/test failures autonomously.
- Run one relevant full suite at a meaningful green checkpoint when code changed. Do not rerun tests without a code change or new evidence requiring them.
- Use proportionate checks: compile/import and `git diff --check` after code changes; document-only work needs Markdown/diff checks, not application tests.

## Git and files

- Work on the approved branch; never change `main`, force-push, rewrite history, delete branches/tags/releases, or remove material project files without explicit authorization.
- Do not use `git add .` or `git add -A`. Stage an exact whitelist and inspect the staged list.
- Local commit is allowed only when the current scope permits it and the required checkpoint is green. Commit only expected files; never stage `db.db` or secrets.
- `git push` requires explicit owner authorization, even after a green local commit.

## Completion report

Keep the final report short and evidence-based: stage, proven result, production changes, remaining/used boundaries, changed files and checks, local commit, push status, and the next safe step. Do not hide warnings or present an unverified result as complete.
