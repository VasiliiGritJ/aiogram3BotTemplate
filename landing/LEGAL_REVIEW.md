# Own legal documents — publication gate (8G1D-E2)

Local draft and static integration only. No Prodamus template acceptance, form
submission, production deployment, checkout activation or user consent collection
is authorized by this stage. These documents are not a legal compliance certificate.

## Intended URLs / configuration

- `https://www.traineros.ru/offer.html`
- `https://www.traineros.ru/privacy.html`
- `https://www.traineros.ru/consent.html`
- Refund text: `https://www.traineros.ru/offer.html#refund`; old `/#refund` remains.
- Use `TERMS_URL=https://www.traineros.ru/offer.html` and
  `PRIVACY_URL=https://www.traineros.ru/privacy.html`. Legacy anchor config is
  accepted and normalized to these pages; old anchors remain summaries with links.
- Existing public seller name/INN/email and the newly required
  `SELLER_DISCLOSURE_ADDRESS` are injected by `landing.build` from authorized
  process configuration, not committed. Never request a home address in chat.
  Do not publish fake fixture data from QA. Missing address prevents a build.
- Upload the complete generated artifact, including the three HTML documents
  and `legal.css`, only in a separately authorized publication stage.

## Exact remaining owner/legal review

1. **Seller residence disclosure.** Article 17(2.9) of the Unified Consumer
   Protection Rules specifies residence information for a physical-person seller
   without IP registration. Rospotrebnadzor confirms the rules apply to the
   self-employed. Therefore an email-only offer cannot be certified complete.
   Conservative MVP position: disclose the current residence/registration address
   already provided in the official questionnaire, only after separate owner
   approval for PUBLIC disclosure. The local template has a final-value slot,
   but no verified current address was found in the inspected repo or local
   questionnaire filenames. Do not ask for the address again in chat, read
   secrets, or invent a verified value. Retrieve it from the existing official
   questionnaire through an authorized secure channel at publication time.
   Do not assume a postal forwarding address or city alone fulfils this rule.
   No basis was found to require public bank-account or passport details for this
   service; they are omitted. No private seller data was read or copied.
2. **Health strategy implemented locally.** The questionnaire no longer asks
   for injuries/restrictions. The domain rejects non-null legacy payloads;
   new profiles leave the old nullable column unset, edits never overwrite it.
   The column is deferred from ordinary ORM queries, profile UI and planning
   do not access it. Old FSM health input is discarded without reading/echoing
   message text. No health checkbox is added. Free program annotations and
   unknown exercise text are not persisted/echoed; drafts retain controlled
   exercise names and numeric prescriptions only. Arbitrary support messages
   are no longer copied to Telegram administrators. Safety information is not
   a question about the user's health.
   No special-category consent is required for this NEW non-health MVP path.
   This does not legalize historical health storage: storage itself is processing
   under 152-FZ art. 3, and arts. 10/21 still apply IF such records/backups exist.
   No real DB was inspected or changed; existence and legal retention basis of
   historical records are UNKNOWN. Authorized handling before launch is a gate,
   not permission to delete the protected DB or run a destructive migration.
3. **Privacy operations — proof required, not inferred violations.** The code
   automatically stores identified profiles/history/orders in SQLite and sends
   personalized bot messages via Telegram. Support uses Gmail; checkout sends
   email/order details to the assigned provider. No proof of operator notification
   under art. 22, applicable transborder notification/recipient assessment under
   art. 12, or a complete localization assessment under art. 18(5) was found in
   the reviewed project records. Russian Selectel hosting alone does not prove
   the Telegram/email collection path compliant. Recipient jurisdictions and
   actual processor arrangements need confirmation, not guessed country lists.
   Confirm these facts before commercial launch. Do not claim notifications
   were never submitted just because their proof is absent from the repository.
4. **Retention and rights — operating gate.** No complete user-erasure operation
   or retention job exists. The OAuth session expires for authentication but
   remains a DB row; no lifetime-trial exception to lawful retention is assumed.
   A manual procedure is permissible, but must actually be established/tested
   against a temporary DB before commercial launch, including dependent workout,
   payment/receipt records, historical nullable health fields, logs and backups.
   Rights/request deadlines and unlawful/done-purpose processing are governed
   by arts. 20/21; the reviewed source has no evidence of an operating procedure.
   The policy now states manual handling and does not pretend a deletion button
   or automated deletion exists. Do not delete tax/payment records arbitrarily.
   A static separate ordinary consent remains a draft, not recorded consent.
   Necessary contractual data rely on art. 6(1)(5); if an OPTIONAL processing
   purpose later relies on consent, separate action/version/time evidence is
   required by art. 9. It is not a health-data fallback.

## Actual flow audit (source only; no production/secrets/DB access)

| Data | Store / use | Recipient / boundary |
| --- | --- | --- |
| Telegram ID/name/username | `User`, bot identity, website OIDC account binding | Telegram account/messages; website exchanges OIDC code |
| Ordinary fitness parameters | `FitnessProfile`, deterministic planner | SQLite; summary/profile messages via Telegram; no external AI |
| Exercise/set/load/history | workout tables and controlled program snapshots | SQLite and Telegram UI; no raw program import stored |
| Email/orders/statuses/receipts | payment/profile/receipt tables | Assigned checkout/receipt provider, NPD/FNS |
| Browser authentication | `WebsitePurchaseSession`, expiry and server binding | Secure HttpOnly cookie; expired rows not automatically erased |
| Technical events | source logging IDs/statuses, not questionnaire/message bodies | Runtime log destination/rotation need production verification |
| Support request | Public email; no arbitrary bot forwarding | Gmail; no medical documents should be sent |
| Legacy health column | Kept, deferred, not used by new runtime path | Historical existence/basis UNKNOWN; real DB untouched |

No AI-provider calls, health prompts, medical analytics or user-health exports
were found in the inspected runtime. `generated_program_review` exports synthetic
controlled programs without opening a user DB. This is a source audit, not proof
of live server log/backup permissions or retention. No paid provider was activated.
The webhook entrypoint disables aiohttp access logging (`access_log=None`), so
signed queries and OAuth codes are not emitted by that access logger. Reverse
proxy/mail-provider logging remains an operational verification item.

## Minimal manual procedure to establish before launch

Owner/operator registers requests received at the published email, verifies the
requester's Telegram/account/order ownership without passport collection, identifies
the applicable purpose/basis and statutory response deadline, blocks disputed data
when required, separates lawful tax/claims retention from removable profile data,
and executes the approved change with least privilege. Verify dependent records
and backup expiry/recovery handling on a temporary fixture DB, record completion
without copying medical text, and notify the requester. Unsolicited medical content
must not be forwarded or added to a profile; review lawful handling/removal promptly.
No real deletion/DB change or external notification is authorized by this stage.

## Sources checked 2026-10-03/04 (current text, not future amendments)

- [Unified Rules, article 17](https://www.consultant.ru/document/cons_doc_LAW_496218/002faf264420d83ecdf54b519fbe57ccf1ec1004/)
- [Rospotrebnadzor: self-employed obligations](https://zpp.rospotrebnadzor.ru/news/regional/546081)
- [152-FZ, article 9: separate consent and proof](https://www.consultant.ru/document/cons_doc_LAW_61801/6c94959bc017ac80140621762d2ac59f6006b08c/)
- [152-FZ, article 10: health data](https://www.consultant.ru/document/cons_doc_LAW_61801/26edb2934b899bf9c74c3a8f7e574651c6565e6d/)
- [Civil Code, article 782: withdrawal](https://www.consultant.ru/document/cons_doc_LAW_9027/e21d6a868cf614117afa2f93877215e487cd4aee/)
- [422-FZ, article 14: NPD receipt](https://www.consultant.ru/document/cons_doc_LAW_311977/ad291257df0088ddbc91dc935ca07c7d40bde627/)
- [152-FZ, art. 6: contractual basis](https://www.consultant.ru/document/cons_doc_LAW_61801/315f051396c88f1e4f827ba3f2ae313d999a1873/)
- [152-FZ, art. 22: notification](https://www.consultant.ru/document/cons_doc_LAW_61801/d996966e22e1320c9de1ab82d9f6be12c3d9d765/)
- [152-FZ, art. 12: transborder notification](https://www.consultant.ru/document/cons_doc_LAW_61801/e4ebbe1780de623c7cf32a59ca82a7bb523a25dd/)
- [152-FZ, art. 18(5): localization](https://www.consultant.ru/document/cons_doc_LAW_61801/cbf4e15b7c330f9372e876cdf2bc928bad7950ef/)
- [152-FZ, art. 21: cessation and destruction](https://www.consultant.ru/document/cons_doc_LAW_61801/d3fe43a7c415353b17faab255bc0de92bea127da/)

## Product/source consistency

Public landing was read in the browser: 499 RUB / 30 days, no auto-renew,
72-hour trial, configured seller/INN/email and existing legal summaries match.
Subscription timing follows `services.payment_domain.calculate_subscription_grant`:
active paid period first, otherwise active trial, otherwise confirmation time;
plus exactly 30 days. No arbitrary activation deadline or guaranteed outcome added.
Provider names are intentionally absent from the offer: Prodamus is not yet
activated and PayAnyWay remains fallback. Browser redirect grants no access.
