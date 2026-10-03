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

Existing notification contract: `order_id` (provider operation), `order_num`
(our transaction), `sum`, `customer_email`, `payment_status=success`, `Sign`.
Exact order/email/amount and provider operation must match. Signed success
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

Provider-specific activation gate: obtain/confirm the actual signed notification
sample (including exact status names, array types, signature vector and success
acknowledgement) before public activation. Negative statuses currently do not
close an order because their production semantics are unverified. Adapt only if
the delivered contract differs, with targeted regression tests. Do not infer
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

## Minimal authorized activation sequence

1. Obtain Prodamus approval, assigned payform/key, notification contract and NPD
   confirmation; obtain/configure Telegram OIDC client and allowed redirect.
2. Bundle owner authorization for secure runtime values, verified DB backup /
   migration 17 if production is still schema 16, exact code deployment and
   necessary role reload. No production operation is authorized by this doc.
3. Keep switch OFF and CTA hidden; deploy reviewed source and configure HTTPS
   reverse proxy routes/secret-safe logs on the existing host. Preserve fallback.
4. Validate complete runtime/NPD settings, enable website switch, reload webhook
   role; validate real `/buy`, auth, email, 499 ₽ checkout and rejected signature.
   Confirm public endpoint health before publishing the purchase CTA.
5. Execute ONE separately authorized 499 ₽ E2E: signed webhook → one 30-day grant
   → bot entitlement → provider NPD receipt proof; replay callback without a
   second charge and assert unchanged access/receipt count. Redirect alone
   must grant nothing. Never call the local tests a real merchant acceptance.
