# Privacy operations — E3, local procedure, 2026-10-04

No production execution, regulator filing, residence entry or external submission.
Operator: Климашин Василий Дмитриевич; public contact: kurtbachotto@gmail.com.
This is the procedure to approve/use, not evidence that production ran it.

## Evidence and scope

- Protected local `db.db` opened with SQLite `mode=ro`, `query_only=ON`.
  Only metadata and aggregate COUNT evaluated: `fitness_profiles.limitations`
  exists; **0 nonempty rows**. No user/health values selected or printed.
- Therefore no health-column deletion is needed for this inspected local DB.
  Production DB, old snapshots, free-text historical programs and mail are not
  certified empty. Do not turn this count into a production-wide assertion.
- New runtime does not ask/store/use health fields; ordinary profile SELECT
  defers the old column. No external LLM call exists. Unsolicited Telegram
  messages still reach Telegram; this is not a guarantee that no user ever
  types health information. Do not copy unsolicited health text into records.
- E2 source checkpoint: `6b1b8ad`, 523 tests previously passed, no repeat before
  E3 changes. E3 utilities are opt-in and have no production/default DB path.

## Actual flow map

| Data / source | First app write; storage | Recipient / jurisdiction | Retention and deletion; basis |
| --- | --- | --- | --- |
| Telegram ID/name/username, `/start` | `handlers/start.py` → `users` SQLite | Telegram cloud already receives account/messages; operator SQLite | Contract/pre-contract art.6(1)(5); purpose duration. Verified account erasure; no blanket perpetual retention |
| Age/sex/height/weight/goal/experience/frequency/environment | Telegram message → FSM memory → `services/onboarding.py`, `fitness_profiles` | Telegram receives input before app; SQLite, profile UI via Telegram | Contract; correction existing onboarding/profile. `erase_training_data`, no health in export |
| Exercises, sets, load, history | workout handlers/services → workout tables, plans | SQLite, personalized results back through Telegram | Training purpose; remove workout sessions/plans + FK children. No LLM |
| Website login | `start_login`: token/state hashes, nonce, PKCE in `website_purchase_sessions`; verified Telegram identity afterwards | `oauth.telegram.org` token exchange (code/verifier/client auth); SQLite | Authentication, 10-minute default lease. `purge_expired_purchase_sessions` removes expired rows; no scheduled production job claimed |
| Checkout email/order | `/api/payment/create` → `SubscriptionPayment.buyer_email` before provider link | Prodamus checkout: email, order ID, item, amount. PayAnyWay fallback similarly; NPD/FNS via provider | Contract/tax. Financial rows blocked from blind deletion; per-record tax/claim purpose and expiry must be documented |
| Receipt/status/entitlement | signed callback → payments/receipts/access in SQLite | Provider and FNS; bot access UI | Contract/legal duty; never erase payment idempotency records under an active obligation |
| Support email | user's email → Gmail mailbox; no app support DB | Google/Gmail; exact account contracting entity/storage country not established | Request handling; manual mailbox deletion incl. sent/trash/attachments after purpose; no medical documents requested |
| Admin views | SQLite → authorized Telegram admin UI | Telegram ID/fullname/username/registration date; aggregate counts | Service administration only; auth decorators present. E3 removes traceback forwarding. No bulk export found; mailing exists, not executed/authorized here |
| Logs | app events include user/payment IDs; no raw medical payload intended | Local runtime destination unknown; proxy/mail logs unverified | Security purpose, manual retention/rotation approval required. Webhook access logging disabled; do not retain signed URLs, tokens, residence or exception payload |
| Backups | no complete deployed backup policy in inspected source | Storage location, schedule and expiry not verified | Separate inventory and expiry; deletions must also apply on restore and to controlled copies |
| Analytics / AI | no third-party analytics SDK or external AI runtime identified | No AI recipient/payload to pseudonymize | Do not invent a provider, foreign country or health consent for absent flow |

## Localization evidence limit

Runtime uses `DATABASE_PATH` → SQLite (`db/runtime.py`, `storage/runtime_config.py`),
not a foreign SaaS ORM. Owner's earlier infrastructure checkpoint names Selectel
Moscow ru-7a. This does **not** prove the currently deployed DB path/mount or all
first writes. No usable SSH alias/credential reference was found in the inspected
project/runtime references and standard `.ssh` listing; no secret recovery done.
Live service environment/path, disk region and backup inventory remain unverified.
RUSSIA_PRIMARY_STORAGE = UNCERTAIN. Foreign *own* SaaS DB: none found in source;
Telegram cloud input and Gmail correspondence are separate foreign service flows,
not silently classified as Russian storage. Do not alter bot UX to solve this by
assumption. Obtain a read-only host/path and processor-location attestation before
calling localization PASS. Do not dump service environment to obtain the path.

## Working manual rights procedure

1. Register request received at support: random case number, received/due dates,
   request type, operator and disposition. Verify account/order ownership through
   authenticated account/channel; do not request a passport by default.
2. Access: `export_non_special_account_data` returns data in memory only; use a
   verified secure delivery channel, never chat/log/stdout. It omits health,
   auth secrets and checkout URLs. Add workout detail from existing history and
   any retained financial/legacy data via controlled review; do not label a
   limited export a complete answer if other retained categories exist.
3. Correction: existing validated profile editor; separately verify identity
   changes. Immutable order/receipt facts corrected through provider's lawful
   correction flow, not rewritten as history. Withdrawal affects consent-based
   purposes, not independent tax obligations. Record basis for any retained data.
4. Ended purpose/deletion: freeze relevant writes in an authorized maintenance
   window, determine obligations, make a consistent restricted backup where
   justified; run in explicit SQLAlchemy transaction with FK enforcement.
   `erase_training_data` removes profile (including legacy column), plans and
   history. `erase_account` rejects unverified/unreviewed requests, active access
   and any financial rows. It removes user/access/auth sessions and referral link
   remnants only after ended-purpose review. Return only table counts.
5. Accounts with financial records: training erasure can proceed independently;
   operator must document legal retention basis, exact categories and expiry for
   remaining rows. This utility deliberately does not assert or execute blanket
   tax-record destruction. Escalate such cases for a scoped operation.
6. Expired auth rows: run `purge_expired_purchase_sessions` during authorized
   maintenance, verify live rows/payment rows unchanged. Schedule is not deployed.
7. Verify counts, FK integrity and absence of targeted live records. A rollback
   before commit restores transaction consistency. After commit, restore only if
   lawful, isolated and with all approved erasures reapplied before writers start.
8. SQLite logical deletion alone does not prove physical destruction: handle
   free pages/WAL, exports, logs, mail and every backup. Restricted backup may not
   become indefinite storage of erased data. Decide expiry/isolation and lawful
   destruction method, prevent reintroduction on restore; verify completion.
9. Record act/system evidence per applicable RKN destruction requirements,
   without copying erased sensitive text. Record scope/counts/time/method,
   executor and verification, retain proof under its own lawful schedule; notify
   requester. Statutory deadlines depend on request type (152-FZ arts.20/21),
   not an invented universal retention period. If completion needs blocking first,
   apply it within the legal deadline. No automated rights SLA claimed.

## Historical health deletion plan (not executed)

For a future positive production count only: authorized maintenance + consistent
verified backup with restrictive access/short lawful disposal; approved exact
column/rows; transaction setting only obsolete health values to NULL, no other
profile/access/payment changes; verify zero target count and unchanged other
counts/FK checks. Roll back transaction on failure. A backup containing unlawfully
retained data is not an unlimited rollback licence. Complete controlled-copy/WAL/
free-page destruction and an act of destruction before reporting completion.
No destructive migration required. Local count zero does not authorize deletion
of any production record or backup.
