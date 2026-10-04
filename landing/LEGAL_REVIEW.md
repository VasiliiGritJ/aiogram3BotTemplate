# Own offer publication gate — 8G1D-E3

Local implementation only. No production deployment, /buy activation, real
residence entry, RKN submission or Prodamus form/support submission.

## Owner decision and implementation

Home/registration address is **not public**: no address input in static landing
configuration, HTML, footer, bundle or sitemap. Do not ask for public disclosure.
Old E2 public SELLER_DISCLOSURE_ADDRESS contract is removed, not reused.

- Public /offer.html is explicitly a service-terms summary, not the full offer.
  Existing price/refund/safety/receipt text is preserved; privacy and ordinary
  consent remain separate. No broad health consent.
- Full /purchase/offer lives on the purchase host. Server verifies the same live,
  authenticated Telegram purchase session used for payment; anonymous/expired
  sessions receive 401. No URL token or client-supplied Telegram ID.
- Runtime-only SELLER_RESIDENCE_PRIVATE, repr-redacted, mandatory when checkout
  runtime is built, HTML-escaped. Never pass to landing.build or log environment.
- Response: Cache-Control private, no-store; X-Robots-Tag and meta robots
  noindex/nofollow/noarchive/nosnippet; no-referrer, frame-ancestors none.
  These discourage caching/indexing, not screenshots or a buyer sharing text.
- /buy shows 499 RUB / 30 days, one-off/no renewal/no automatic debit, full offer,
  privacy and refund links before provider handoff. Required offer-version
  acknowledgement is checked server-side. It is not personal-data consent.
- Checkout remains OFF. No change to signed confirmation/access semantics.
  Full offer may be saved by a buyer; public summary does not pretend to include
  all seller disclosure. Legal sufficiency of this delivery method is not
  certified merely by robots headers; obtain Prodamus confirmation below.

## Audit result / actual gates

Technical E2 preserved in 6b1b8ad (523 tests already passed); E3 adds private offer,
offline deletion/session cleanup tools and removes admin traceback forwarding.
See ../privacy/OPERATIONS.md for actual code flows and procedure;
../privacy/RKN_DRAFT.md for unsent notification drafts and source links.

Local db.db read-only metadata/count: legacy limitations column exists,
0 nonempty rows. No local health deletion required. No values inspected.
Production historical records/backups not counted; do not extrapolate zero.
New MVP never intentionally collects/uses health; no AI provider exists.

RKN register could not be reached (two timeouts): UNVERIFIED. New filing
requirement uncertain until existing record checked. Automated processing is
not within an established exemption. No filing performed.

Own first app storage is SQLite; live DATABASE_PATH, mount region, backups and
Telegram/Gmail first-write jurisdictions not established in this environment.
RUSSIA_PRIMARY_STORAGE=UNCERTAIN. No foreign owned SaaS DB found in source,
but foreign communication flows remain. Do not call an absence of evidence a
proven violation or proof of compliance.

Offline rights/deletion procedure and temp-DB transaction/rollback tests exist.
Production operational adoption, backup disposal and financial retention decisions
are still required; no scheduled cleanup or physical-erasure completion claimed.
Ordinary consent page is a draft, not recorded consent; contract-necessary
processing uses its own basis.

## Prodamus question — draft only, NOT SENT

Допускает ли Prodamus использование собственной публичной оферты,
полный текст которой предоставляется покупателю на нашем сайте
непосредственно перед переходом к оплате после авторизации,
при этом страница не индексируется поисковыми системами?
Все обязательные сведения об Исполнителе, включая место жительства,
покупатель видит до оплаты.

## Runtime/publication sequence

1. Confirm existing RKN record, recipient/location evidence and adopt the
   documented rights/backup procedure; file/amend only with owner authorization.
2. Obtain Prodamus confirmation for this offer-delivery method (send requires
   owner authorization); this is not acceptance of their template.
3. Enter residence only in protected server config through an approved secure
   channel. No real value entered in this stage.
4. Separately authorize legal/application deploy. Static public documents can
   never contain residence; private route must work before any /buy activation.
   RKN/procedure gates are not silently bypassed by a successful static build.

## Legal references (checked 2026-10-04)

- [Unified rules art.17(2.9)](https://www.consultant.ru/document/cons_doc_LAW_496218/002faf264420d83ecdf54b519fbe57ccf1ec1004/) — residence disclosure to consumer; gated delivery needs review, not a claim of exemption.
- [152-FZ art.18(5)](https://www.consultant.ru/document/cons_doc_LAW_61801/cbf4e15b7c330f9372e876cdf2bc928bad7950ef/) — localization.
- [152-FZ art.21](https://www.consultant.ru/document/cons_doc_LAW_61801/d3fe43a7c415353b17faab255bc0de92bea127da/) — cessation/deletion.
- [152-FZ art.10](https://www.consultant.ru/document/cons_doc_LAW_61801/26edb2934b899bf9c74c3a8f7e574651c6565e6d/) — special categories; ordinary consent is not a substitute.
- [GK art.782](https://www.consultant.ru/document/cons_doc_LAW_9027/e21d6a868cf614117afa2f93877215e487cd4aee/) and [NPD art.14](https://www.consultant.ru/document/cons_doc_LAW_311977/ad291257df0088ddbc91dc935ca07c7d40bde627/) — preserved refund/receipt conditions.
