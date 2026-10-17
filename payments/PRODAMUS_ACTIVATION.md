# Prodamus activation contract — local readiness, not production acceptance

Reference/runbook only. Active project rules remain in AGENTS.md/NEXT_TASK.md.
No production values, secrets or customer identity belong in this file.

## Existing composition and activation switch

The website adapter runs inside the existing payment webhook role. Keep the
PayAnyWay fallback `PAYMENT_PROVIDER=payanyway` and its existing receipt/runtime
configuration unchanged. Do not set `PAYMENT_PROVIDER=prodamus`: that variable
selects the historical bot/fallback composition, not the website provider.
The bot remains an entitlement consumer and has no RUB provider checkout action
when `WEBSITE_PURCHASE_URL` selects website-first UX. Stars are not implemented.

`WEBSITE_PURCHASE_ENABLED=false` (default): no website routes are registered.
After configuration and authorized deployment, `true` registers the routes.
All website settings are validated before the database runtime is opened or a
socket is bound. Invalid/missing settings stop bootstrap with a safe error.
Production landing purchase CTA stays hidden until real `/buy` smoke passes.
This stage does not change landing, bot, production config or processes.

## Exact website configuration

| Name | Secret | Required / purpose |
| --- | --- | --- |
| `WEBSITE_PURCHASE_ENABLED` | No | `true` only at authorized activation |
| `WEBSITE_PAYMENT_PROVIDER` | No | `prodamus` (default); other values rejected |
| `WEBSITE_PURCHASE_BASE_URL` | No | HTTPS origin: `https://payments.traineros.ru`; no path/query/fragment |
| `WEBSITE_PURCHASE_URL` | No | `https://payments.traineros.ru/buy`; shared commercial UX selector |
| `WEBSITE_CHECKOUT_MODE` | No | Missing/empty=`off`; exact modes `off`, `controlled`, `public`; public activation separate |
| `WEBSITE_CHECKOUT_TELEGRAM_USER_ID` | Private identity | Required only for controlled owner-only creation; never print |
| `WEBSITE_CHECKOUT_EXPIRES_AT` | No | UTC lease deadline required only for controlled creation |
| `TELEGRAM_BOT_URL` | No | `https://t.me/GritJBot` |
| `TELEGRAM_OIDC_CLIENT_ID` | No | Telegram-assigned OIDC client ID |
| `TELEGRAM_OIDC_CLIENT_SECRET` | Yes | Telegram OIDC token-exchange credential, server only |
| `PRODAMUS_PAYFORM_URL` | No | Merchant's assigned HTTPS payment form URL, no query/fragment |
| `PRODAMUS_SECRET_KEY` | Yes | Merchant's form/notification HMAC key, server only |
| `PRODAMUS_SYSTEM_CODE` | No | Optional integration system code, only if supplied by Prodamus |
| `PRODAMUS_NPD_ENABLED` | No | Must be `true` in production after merchant-side NPD activation is verified |

No independent Prodamus merchant ID or tax/INN request parameter is invented:
the assigned payform identifies the merchant in this adapter. If onboarding
specifies a required extra identifier, implement it only from that contract.
The existing `APP_ENV`, absolute `DATABASE_PATH`, production payment/receipt
mode, loopback webhook bind, commercial URLs and support configuration remain
mandatory. Keep real values in authorized production configuration, not dotenv
or tracked source. Do not read/copy development secrets to activate production.

## Public routes / Telegram allowed URLs

All routes use `https://payments.traineros.ru`:

- `/buy`: start Telegram OIDC or display email/order summary (499 ₽ / 30 days,
  no auto-renew); Telegram user identity comes only from verified OIDC.
- `/auth/telegram/callback`: register this exact redirect in Telegram OIDC;
  allow the purchase origin in Telegram settings. Validate state, nonce, PKCE,
  ID-token signature, issuer, audience and expiry; consume session once.
  With `openid profile`, bind the Bot API identity from the signed numeric `id`
  claim, not the opaque OIDC `sub`. Missing/invalid `id` fails closed.
- `/api/payment/create`: POST, receipt email and session-bound CSRF token;
  price, Telegram owner and product cannot be supplied by the browser.
- `/webhooks/prodamus`: POST form-urlencoded, `Sign` header; no browser cookie
  needed. Configure this exact notification URL at the provider.
- `/payment/success`, `/payment/fail`: read the session-bound persisted order;
  never confirm or cancel a payment. Expose bot link and return-to-order action.

No extra VM/DNS/service required. Reverse proxy must forward the original form
body and `Sign`; enforce the existing 64 KiB body bound. Disable query/body,
cookies and authentication-header logging for auth/payment endpoints, including
reverse proxy access logs. Aiohttp raw access logging is disabled; bounded
application events contain only fixed categories and local payment IDs.
Responses have `no-store`, `no-referrer`, `nosniff`; purchase cookie is Secure,
HttpOnly, SameSite=Lax. OIDC network work and DB calls run off the HTTP event loop.

## Payment and receipt boundary

Server-owned order: 49900 minor units, RUB, 30 days, Telegram user, normalized
receipt email, immutable internal order ID and item description. Hosted form
has one product (499.00, quantity 1), never a recurring/subscription request.
HMAC canonicalization recursively sorts object keys, converts form scalar
values to strings, serializes compact UTF-8 JSON and escapes slashes. Tests use
an independent canonical-byte vector and round-trip form representation.

Notification contract verified against official Prodamus.Pay documentation
on 2026-10-07: POST form-urlencoded, signature in `Sign`, successful durable
processing acknowledged with HTTP 200 (no mandatory response body).
`order_id` is the provider operation; `order_num` is our transaction;
`sum`, `currency`, `domain`, `customer_email` and `payment_status=success`
are signed fields. Exact order/email/amount, RUB currency, configured payform
hostname and provider operation must match. Signed success
alone invokes the existing atomic payment/entitlement service. Duplicate and
concurrent delivery grant once and create one receipt intent. Active paid or
trial access extends from the existing allowed end point, otherwise from now.

| Situation | Local behavior |
| --- | --- |
| Pending / redirect before webhook | Read pending, no entitlement |
| Webhook before redirect | Read succeeded only after access is persisted |
| Invalid signature / unknown / mismatch | Reject, no access |
| Duplicate or concurrent paid webhook | Ack same effect, no second extension |
| Expired/replayed login session | Reject auth/purchase; new login required |
| Browser fail/cancel | No authoritative state mutation; existing order can be retried |
| Signed non-success status | Reject safely; **do not guess terminal mapping** |
| Persisted failed/canceled/expired order | A new order is allowed, no grant from failure |

Official contract references:
- https://help.prodamuspay.ru/kanaly-prodazh/tekhnicheskaya-dokumentaciya/sozdanie-platezhnoy-ssylki
- https://help.prodamuspay.ru/kanaly-prodazh/tekhnicheskaya-dokumentaciya/webhook-ob-oplate

Documented negative statuses are `order_canceled` and `order_denied`. They do
not grant access or close a local order: this preserves safe retry and avoids
guessing whether another attempt can still settle. Confirm actual merchant
round-trip signing and notification delivery before a real payment. Never infer
failure or cancel a potentially payable order from a redirect/timeout.

Receipt snapshot stores only necessary customer email, amount, currency,
service description/period, provider reference and income time. No passport,
card or bank details are collected by the purchase application.
`PRODAMUS_NPD_ENABLED` is a **local readiness assertion**, not a wire parameter.
Email/product data are ready; a pending `PaymentReceipt` is NOT proof of a
registered/delivered NPD receipt. Merchant-side “Мой налог” connection and exact
receipt proof/status format still require Prodamus confirmation. Do not run a
second generic receipt worker on Prodamus intents or mark them delivered based
only on payment success; PayAnyWay's NPD response does not handle Prodamus.
Provider-specific receipt acknowledgement remains an explicit adapter boundary
until its format is known. Receipt retry must never reapply entitlement.

## Controlled full refund (prepared locally, 2026-10-17; NOT executed)

- Owner strategy17Oct: defer the test refund until sufficient available sales
  balance and separate authorization. No request, transfer, automatic balance
  trigger, cancellation or revocation now. Keep the existing paid subscription.
  Deploying the prepared refund baseline must NOT execute reconciliation.
- Target: one paid Prodamus E2E order 89-01019-36601, 499 RUB. Production
  read-only check: one matching paid order, original grant intact, access end
  matches that grant, no later paid grants. Production schema remains17.
- Official refund path: payment list -> exact order -> full refund request.
  Do not submit without NEW owner authorization. Funding is from future sales
  or a Prodamus-generated payment order. Owner has chosen to wait for sufficient
  available funds from later sales; no extra funding transfer is authorized.
  Recheck available/refund-eligible funds when separately authorized; do not
  submit an unfunded request now. Buyer must receive the full499 RUB.
- Official help: no additional refund agency fee. Accepted offer13.1 section9.8:
  original remuneration/costs are not returned. Authorized merchant read-only
  reconciliation17Oct proved actual fee18.96RUB/3.8%, paid499RUB, net/balance480.04RUB,
  October report forming and refunds0. This does not prove funding sufficiency
  or FNS acceptance. Typical bank completion1-5 working days, maximum30.
- «Возвращается» is only a request. Confirm «Возвращён», matching original
  order/provider operation/internal transaction, full amount and refund receipt
  in the official account. Preserve a protected evidence document outside git;
  persist only its SHA256 and a non-secret refund document reference.
- No verified official refund webhook/API contract is available. Do not treat
  order_canceled/order_denied or guessed refund/refunded events as refunds.
  FullRefundService is an internal TRUSTED OPERATOR reconciliation boundary,
  not a self-authenticating provider integration and not a public endpoint.
- Before authorized production use: stop affected writers, SQLite-consistent
  backup + verification, additive migration18 + quick/FK checks, deploy exact
  the combined eight-file whitelist below. Restart only
  affected roles so bot and webhook use the reviewed model. Checkout stays OFF.
  Verify evidence BEFORE reconciliation; do not revoke for a pending request.
- Reconciliation atomically records one refund and truncates only this grant's
  remaining access. Original succeeded payment, grant marker and receipt history
  remain; late signed success cannot regrant access. Changed/later access fails
  closed for explicit review. Browser return reads refunded, never active.
  Roll back source on regression; NEVER reset/restore the live paid DB blindly.
- Tax: agent KKT receipt is proven; separate duplicate buyer NPD receipt must
  not be issued. FNS income acceptance/correction is UNKNOWN, not inferred from
  PRODAMUS_NPD_ENABLED or pending PaymentReceipt. After actual refund, verify
  Prodamus/FNS correction of the original October2026 income under article8(3-4)
  of422-FZ. Do not invent a second sale/check or cancel unrelated tax records.
  No tax API or automatic correction is implemented without an official contract.

Sources: https://prodamus.ru/oferta/290926 ;
https://help.prodamus.ru/payform/vozvraty-platezhei/kak-v-prodamus-ustroeny-vozvraty-platezhei-klientam ;
https://help.prodamus.ru/payform/vozvraty-platezhei/sformirovat-zayavku-na-vozvrat ;
https://help.prodamus.ru/payform/vozvraty-platezhei/kak-ponyat-chto-vozvrat-proizveden ;
https://www.nalog.gov.ru/rn91/news/tax_doc_news/8485686/

## Public checkout release checkpoint (17 October 2026)

Prepared locally and deployed under explicit owner Phase A approval17Oct.
Checkout remains OFF; public/controlled activation, payment and refund were not
performed. Deployment evidence and restoration point are recorded below.
Targeted94/94 PASS; one final full610/610 PASS. No unchanged tests rerun.
Astra final public security/payment review PASS, including the session-expiry
401 catch. Compile/diff checks PASS. Existing paid E2E is not repeated.

### Explicit checkout policies

- Missing/empty mode defaults to `off`; default WebsitePurchaseService also
  installs a closed gate. Unknown configuration fails bootstrap, never opens
  public access. SettingsReader's standard whitespace normalization still applies.
- `off`: no creation for any buyer, including owner. Existing signed payment
  notifications remain operational and idempotent.
- `controlled`: exactly the verified owner, UTC deadline, one durable Prodamus
  order. Terminal/paid orders do not reopen its quota. Auth-session expiration
  and owner deadline are both rechecked after obtaining the DB writer lock.
- `public`: explicitly selected only. Verified unexpired OIDC identity, email,
  session-bound CSRF and exact offer-version acknowledgment remain mandatory.
  No identity, price, currency or period comes from browser parameters.
- While paid access exists (including paid time queued after trial), /buy shows
  the bot link instead of another form. POST is blocked atomically as well.
  Purchase is possible again after paid expiry via a new authenticated intent.
  Trial remains lifetime-once72h and paid grant calculation is unchanged.
- Each session retains its original pending order. A paid/terminal bound order
  cannot become a new purchase through repeat POST or page refresh. Independent
  sessions/workers reuse one valid pending order under BEGIN IMMEDIATE.
- Reuse requires exact provider/product/499RUB/30days/email/signed-link binding.
  Public resumption is limited to24h. Old/future-dated/corrupt/foreign-provider
  pending history is not cancelled or replaced; reconcile it explicitly.
  This application window is NOT evidence of provider expiration/nonpayment.
- Successful redirects never grant access; only validated provider confirmation
  does. The paid order, access and receipt are still applied exactly once.
  Manual completed-refund reconciliation remains separate; no guessed refund
  webhook statuses and no second fiscal worker have been introduced.

### Frozen artifact / exact deploy whitelist

Archive outside repository:
`C:\Users\Acer\AppData\Local\Temp\traineros-public-checkout-20261017-b82f3a90\release.tar`

Archive SHA256:
`17079f2ffa8403658e9d6cb3729b75f29c4ebb18d924bf76d121c7d56a509e3a`

Archive inspection verified exactly these eight regular files, each matching
its local reviewed SHA256. No db.db, runtime config, secret, key, test or other
working-tree file is included. OIDC source is already deployed and is NOT in
this release (local hash matches approved052e8102... checkpoint).

| File | SHA256 |
| --- | --- |
| `db/models.py` | `e2cd30cf5d2a06f7c6d0f5c040e03e10c22101942211ebf82555ddacd339a8e6` |
| `db/migrations.py` | `614ef7d651128357695adbaf5922f06266376b6350ad8d351e39be024ed09fd1` |
| `services/payment_refund.py` | `0bff2ff9af974febf68caedae6f9d0c46e31fa2a6eb5c56111eea9376c4ae5c4` |
| `services/payment_service.py` | `1e68399e5b74d02e2071b45323a8db1e09155058a3c344f962681dbbb0ac107d` |
| `services/prodamus.py` | `602a05a208584dc7b22ab50261552396e0c237210e947b26b4e81407f5d41746` |
| `services/website_purchase.py` | `9d50b9d586cf55e7ce60a38aa765bf7e32b45651c3ed889fcd551d14b763a265` |
| `storage/website_purchase_http.py` | `8ccdc7a5b62c737d6bf750f71f00dbf68615ddd9cd18da33af446895710d6a16` |
| `storage/website_purchase_runtime.py` | `1031d34c27463e298e823341807709b4923ef3ce9629ad221ff83a7604ba7695` |

### Phase A: owner-authorized deploy with checkout OFF

1. Establish the existing strict SSH path; verify target/revision/current source
   and config presence without printing secrets. Stage the verified archive in
   /opt/traineros/staging; reject extra files, links or hash mismatch. Compile
   staged Python with /opt/traineros/venv/bin/python, do not start it yet.
2. Set WEBSITE_CHECKOUT_MODE=off through the protected config consumer. Preserve
   all other values, permissions/ownership and existing API/receipt/OIDC settings.
   Stop only affected writers: traineros-bot and traineros-webhook.
3. Create root-only dated backup under /var/backups/traineros: SQLite Backup API
   from /var/lib/traineros/traineros.db, code whitelist and protected config with
   original metadata/absent-file markers. Verify backup quick_check/FK, schema
   and existing payment/access/receipt data preservation. Never package secrets
   or DB into the source release or print their contents.
4. Require exact existing schema1-17 (or verified1-18 for a no-op retry), no gaps.
   Load the staged migration module and run run_migrations(engine,target_version=18).
   With1-17 established this applies ONLY18. Verify1-18, quick_check=ok,
   foreign_key_check empty, payment_refunds schema/uniqueness/FK, and unchanged
   pre-existing rows. Failure: keep writers stopped and investigate against the
   verified backup; no blind financial DB reset/restore.
5. Atomically install only the eight source files, verify each deployed hash and
   ownership/permissions. Start webhook first, then bot. No nginx edit/reload by
   default: the last authorized E2E rollout already forwarded /api/payment/create
   to127.0.0.1:8081 in /etc/nginx/sites-available/traineros-webhook. Read/validate
   current proxy/logging/body bounds and nginx config; if reality differs, stop
   before an unapproved network/system change.
6. Smoke OFF: both services healthy/single bot/no409, HTTPS health/readiness,
   auth redirect/no secret-bearing query logs, invalid callback/signature rejected,
   offer authentication and success/fail read-only behavior. Verify runtime gate
   closed with value-free booleans and preserve existing paid access/trial/history.
   Do not create another real order, payment, refund or receipt for smoke.
7. Report Phase A PASS with deployed hashes/schema/service status and rollback
   location. This is NOT public activation authorization.

### Executed Phase A evidence (17 October 2026, checkout OFF)

- Approved archive and exact previous/new hashes verified. Target app has no Git
  binary; source identity was checked by the approved manifest, not an invented
  repository revision. Staged archive compiled; no application tests rerun.
- Verified root-only code/config and consistent SQLite backup:
  `/var/backups/traineros/public-checkout-off-20261017T130719Z/`.
  Executable source/config rollback: `rollback.py` inside that directory;
  backup database: `database-before.sqlite`; secrets remain in protected backup
  files, never in source/git/tool output. Config metadata0640 preserved.
- Only18 applied from exact schema1-17. Verified schema1-18, quick_check=ok,
  empty FK issues, unique/FK refund table, all previous rows unchanged.
- Eight deployed hashes match frozen artifact. Webhook then bot started;
  services healthy, one bot, no new critical/409/auth errors. Nginx unchanged.
- Actual webhook process environment/runtime proves `off` and a closed gate.
  HTTPS live/ready200; empty auth callback400; private offer401; invalid create400;
  unsigned webhook400; success/fail200. No purchase/login/payment/refund created.
- One paid499RUB Prodamus order, correct server-side buyer binding, exactly30day
  original grant and active paid owner access preserved. Payment/access/receipt
  fingerprints unchanged; refund table empty. No cancellation or revocation.
- Result PASS. Rollback READY_NOT_USED; financial DB was never restored.
  **STOP after Phase A; Phase B requires a separate explicit owner approval.**

### Phase B procedure (owner-authorized and executed 17 October 2026)

Executed at13:20UTC /17:20Dubai under explicit owner approval. Approved eight
source hashes, OIDC hash, protected config baseline, nginx and schema18 matched
Phase A. Changed only WEBSITE_CHECKOUT_MODE=public and restarted only webhook.
Actual process environment and composed PublicCheckoutGate verified. Runtime
contract and public HTTP smoke PASS: identity requirement, server-owned499RUB /
30days/no auto-renew, receipt email/offer/CSRF, routes, HTTPS health200,
unauthenticated private offer401, invalid create400, unsigned webhook400 and
read-only success/fail200. No fabricated login or new order was used as evidence.
Existing paid order/buyer binding/exact30day active access preserved. Bot PID,
nginx and source unchanged; one bot and no new critical errors. No financial DB
writes, payment, refund, source deployment or unchanged tests rerun.

Root-only config backup and config-only OFF rollback:
`/var/backups/traineros/public-activation-20261017T132022Z/rollback.py`.
Rollback READY_NOT_USED; never restore financial DB. Backend public checkout
is active at https://payments.traineros.ru/buy. Landing CTA was not modified.
STOP after successful activation; no further features/payments/refunds/push.

After Phase A PASS and an explicit owner action, change ONLY
WEBSITE_CHECKOUT_MODE=public and restart only traineros-webhook. Verify the
composed runtime uses PublicCheckoutGate, OIDC and payment guards remain in
place, services/HTTPS/callbacks are healthy, paid owner sees active-access bot UX.
Smoke cannot fake a live login or create another499RUB payment. Landing CTA
publication is a separate approved static deployment, not a hidden part of this
backend whitelist; do not change bot UX, DNS or provider settings by implication.
If public activation is not explicitly approved, leave off.

### Rollback

Close creation first: off and webhook restart (or leave webhook stopped if
unhealthy). Restore only backed-up application/config values and metadata,
keeping checkout off. Restart the affected roles and smoke. Restore nginx only
if this approved release actually changed it; no unconditional reload.
Keep additive migration18/table and all real payment/refund history. Do not
restore the DB snapshot over later financial/user activity or revoke paid access.
A previously absent new module may remain unused after source rollback; do not
delete broad directories. Log only fixed status categories, never credentials,
cookies, signed payloads, auth codes, state/nonce or customer identity.

Owner deferred the test refund: no request, extra transfer or subscription
revocation until sufficient available sales balance AND a new refund approval.
Tax-income acceptance remains an open evidence item, not an invented failed
filing; agency KKT/OFD proof is not silently relabelled as FNS acceptance.
