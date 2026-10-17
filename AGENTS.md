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

- **GPT-6 Luna + Low/Medium:** low-risk mechanical work, narrow edits, searches, formatting, and simple targeted checks.
- **GPT-6.1 Sol + Medium:** default for primary development, including diagnosis → fix → targeted tests, normal multi-file engineering, integrations, and documentation.
- **GPT-6.1 Sol + High:** payments, authentication/OIDC, database migrations, security, idempotency/races, production wiring, and other critical engineering tasks.
- **GPT-6 Astra + Medium/High:** escalation for especially complex work and final critical review; not a default.
- **xhigh/max:** exceptional only when High is insufficient or a concrete benefit is established.
- **GPT-6 Sol:** legacy predecessor to GPT-6.1 Sol; do not select for new work without a reason.
- **GPT-5.6 Sol/Terra/Luna:** fallback or legacy only. Terra is not the project default.

## External systems and production boundaries

- Production deployment must be reproducible: identify the artifact, exact deploy path, destination, and verification method. Do not rely on an unverified one-off manual path.
- Real payments, production DB work, secret entry/change, process start/stop/restart, deployment, external API calls, system settings, destructive operations, and infrastructure changes are risk boundaries. Stop only if they are not explicitly authorized by the current scope.
- Do not make external payments, send mass Telegram messages, or change production infrastructure without explicit scope authorization.

## SECRET-SAFE ZONE

This mandatory section governs all production credentials: Telegram bot tokens,
API/signing keys, webhook/payment-provider secrets, SSH private keys, S3 access
and secret keys, passwords, recovery codes, `.env` secret values, OAuth client
secrets, database passwords, and equivalent credentials. It applies to browser,
Computer Use, terminal, screenshots, tool/model responses, logs, and artifacts.
Broad autonomy or permission to deploy/configure does not permit secret exposure
or replace owner-only secret entry.

### 1. OWNER-ONLY SECRET ZONE

- Treat every page, chat, panel, or screen where a secret may be displayed,
  generated, recovered, copied, or revealed by a button as **OWNER-ONLY SECRET
  ZONE**. Stop **before** the next action could display it; the owner performs
  only that minimal action manually.
- Never open such a screen with a tool that reads DOM/text/accessibility,
  screenshot it, analyze its contents, or use browser extraction on it. This
  includes navigation tools that automatically return an initial snapshot.
  Capturing first and redacting later, or suppressing output with `emit:false`,
  does not make secret capture safe.
- Never read a BotFather token message, reveal a hidden/masked value, press
  show/reveal secret, read a secret from clipboard or `.env` (including
  `storage/.env`), or display one through a terminal command.

### 2. No secrets through chat

- Never ask the owner to send a secret in ChatGPT/Codex, paste it as a message,
  share a screenshot containing it, or send private-key / `.env` contents.
- Request only an existing key/file path, confirmation that an action was
  completed, or a safely computed masked fingerprint when needed. A file path
  is not permission to read or output its secret contents.

### 3. Prove secure consumption before creation or rotation

- First prepare the complete secure consumption path and verify it with a
  **FAKE secret**. Require evidence that input is masked and the value does not
  enter command arguments, shell history, logs, git, tool output, or the repo.
  Do not claim a GUI, prompt, or channel works until it was actually checked.
- Only after PASS may the owner create/rotate the real credential through the
  applicable authorization boundary. Never **ROTATE FIRST → FIGURE OUT INPUT
  LATER**. If proof fails, report the concrete limitation without relaxing
  security or asking for the value through another unsafe channel.

### 4. Owner-only real secret ingestion

- Only the owner enters a real secret directly into the previously verified
  masked secure channel. Codex/Work must not see or extract the value. Prefer
  masked interactive input, stdin/process-memory transfer, and an authorized
  secret store or protected production-config consumer outside the repository.
- Never use command arguments, URL/query parameters, source files, git, chat,
  screenshots, logs, or ordinary browser automation output as secret handoff
  channels. Do not save values in unapproved stores or examples.
- Once real input begins, do not inspect/snapshot the input page or its DOM,
  accessibility, clipboard, requests, or debug output. Obtain only value-free
  completion/status signals from the consumer.
- Approved SSH/SDK consumers may use an existing credential internally without
  Codex reading its value. Config writes must preserve required permissions;
  persistent storage is allowed only in the explicitly authorized mechanism.

### 5. Verify without revealing the value

- Use only safe signals: PRESENT/MISSING, a safely computed fingerprint/hash
  prefix, expected length/type, or a successful authenticated request.
- Never output the secret, credential object, signed request, authentication
  header, or secret-bearing URL. Do not print its first/last characters without
  a specific technical necessity. Errors and smoke results must be sanitized
  at the consumer, before any tool/model output.

### 6. Accidental exposure means compromise

If a secret appears even once in a model/tool response, screenshot, log,
terminal output, chat, browser extraction, git/diff, or artifact:

1. Stop further use; do not repeat, quote, or recover the value from history.
2. Record **SECRET_COMPROMISED = YES** and report the incident truthfully.
3. Prepare and prove a safe replacement path before requesting rotation.
4. Revoke/rotate only after the applicable owner boundary; owner-only credential
   entry still applies.
5. Update only necessary consumers, restart only necessary services, and smoke
   test without exposing values.
6. Verify that the old credential is no longer used.

Do not hide an already exposed credential and continue using it, or report
SECRET_EXPOSED = NO for an incident that actually occurred.

### 7. Browser boundary

- Browser/Computer Use may navigate to **non-secret** settings, check statuses,
  and configure non-secret values. It must stop before a secret-bearing screen;
  an unfamiliar next screen is not evidence that it is safe.
- BotFather example: navigating to Telegram/BotFather and selecting the intended
  bot is permitted only while the next surface is verified non-secret. Do not
  open/read a token message or a bot-details page that may expose the token.
- Correct flow: Codex prepares the boundary → owner manually revoke/generate →
  owner enters the new value in proven masked input → Codex continues using
  value-free signals, without knowing the token.

### 8. Minimum owner interruption

- Prepare and fake-test the whole safe path in advance; bundle the necessary
  secret actions into one owner handoff where possible. After owner input,
  finish authorized config/restart/smoke autonomously.
- The owner must not ferry secrets between ChatGPT, Codex, terminals, and
  browsers. The only transfer is directly from the owner-only provider screen
  into the verified masked consumer; no intermediate chat or debug copy.

### 9. Prevention overrides convenience

- Do not use a browser/tool step that could accidentally expose a secret just
  because it is faster. After the first incident of that class, the unsafe
  method is forbidden for production secrets; do not repeat it.
- These boundaries remain mandatory even when the current scope authorizes
  deployment or rotation. Existing authorization avoids duplicate permission
  requests; it never authorizes secret observation or an unproved channel.

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
