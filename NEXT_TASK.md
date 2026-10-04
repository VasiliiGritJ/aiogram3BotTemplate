# CURRENT STATE

## Продукт и коммерческий контракт

- Русскоязычный коммерческий Telegram AI fitness coach. Бот — место оказания цифровой услуги.
- «Мой AI Тренер»: 499 ₽ за 30 дней, без автопродления.
- Trial: один явный 72-часовой период на Telegram user lifetime, 0 ₽, без карты и автопродления.
- Основной RUB-поток — **website-first**: `traineros.ru` → Telegram authentication → email для чека → checkout → signed provider webhook → идемпотентный 30-day entitlement → `@GritJBot`.
- Browser success redirect не выдаёт доступ. Order, signature validation и entitlement всегда server-side.
- Prodamus adapter готов локально как основной кандидат. PayAnyWay сохранён как fallback. Telegram Stars намеренно не реализован.

## 8G1D-D — локальная готовность активации

- Исправлен round-trip signing Prodamus (scalar → form string), добавлены безопасные события без email/секретов, CSRF формы покупки, атомарное одноразовое потребление OIDC session и чтение результата заказа только по серверной привязке.
- Targeted: 121/121 PASS; единственный full suite: 508/508 PASS; compile/diff-check PASS. Не повторять без изменения кода или нового дефекта.
- Точный config/route/receipt contract и sequence: `payments/PRODAMUS_ACTIVATION.md` (reference). `WEBSITE_PURCHASE_ENABLED` остаётся OFF; landing CTA не включена; внешние действия этапом не выполнялись.
- Ожидаются assigned Prodamus payform/signing key, подтверждённый notification/ack/status contract и НПД receipt proof format. Receipt intent готов, но не считается доставленным чеком. Не угадывать terminal-status mapping и не запускать второй receipt worker.
- Затем: Telegram OIDC settings + единое owner authorization на production secrets/config, verified backup/migration 17 если нужна, deploy/reload и один controlled 499 ₽ E2E. PayAnyWay fallback не менять по умолчанию.

## 8G1D-G — собственная оферта / анкета Prodamus

- Финальный source оферты от 04.10.2026: цифровой Telegram-сервис, 499 ₽ / 30 дней, разовая 100% предоплата, без автопродления/автосписаний, НПД-чек, 18+, lawful refund/claims, короткий medical disclaimer. Удалены положения про товары, ПК, акты и банковские/паспортные данные клиента. Privacy/consent отдельные, application code не менялся; legal/landing 25/25 PASS, diff-check PASS.
- Prodamus показал, что приложенная оферта будет опубликована на платёжной странице. После отдельного подтверждения owner приватное вложение удалено и заменено собственной 3-страничной версией БЕЗ домашнего адреса. Адрес остаётся только в регистрационной анкете; полное раскрытие потребителю до оплаты — через existing protected purchase flow. Временная адресная PDF-копия удалена; tracked source адрес не содержит. PDF content/bounds/visual/links PASS.
- **Анкета успешно отправлена 04.10.2026** с собственной офертой, не шаблоном. Owner подтвердил оферту Prodamus ред. 13.1 от 29.09.2026, обязательное согласие на ПД и замену вложения. Free «Старт для самозанятых»: 0 ₽, опубликованная стандартная комиссия карт РФ/СБП 3,8%; реклама/рекуррентные/платные extras не подключались. UI: проверка 1–3 рабочих дня, затем доступ/инструкции на email; application ID не показан. Требуется подтвердить почту по письму Prodamus. Изменение buyer-support email в мастере не удалось подтвердить (сохранённая карточка показывала прежний email); после выдачи кабинета проверить контакт и привести к `kurtbachotto@gmail.com`, который уже правильный в оферте.
- Далее: подтверждение почты → production credentials/contract → отдельно авторизованная activation → /buy → controlled 499 ₽ E2E. Production deploy/payment/DB/push этим этапом не выполнялись. Не начинать новый РКН/трансграничный audit и не писать в поддержку Prodamus без нового scope.

## 8G1D-E3 — private offer / privacy publication gate

- Owner решил: домашний/регистрационный адрес НЕ публиковать на открытом сайте. Не спрашивать повторно. E2 технически сохранён в `6b1b8ad`; прежние 523 теста не повторялись до новых изменений.
- E3: статическая сборка не читает адрес; `/offer.html` — явно summary. Полная `/purchase/offer` требует authenticated unexpired purchase session; private/no-store, noindex/nofollow/noarchive/nosnippet, no-referrer. `SELLER_RESIDENCE_PRIVATE` только server config, runtime fail-closed; реальное значение не вводилось. До оплаты ссылки на полную оферту/privacy/refund и offer-version acknowledgment. /buy остаётся OFF.
- Health collection/use E2 устранены; DB schema не менялась. Read-only local db.db: legacy column есть, **0 непустых строк**; production/backups не проверены. Real DB не менялась. Внешнего LLM нет. Telegram/Gmail — реальные внешние коммуникационные flows.
- РКН: официальный реестр дважды timeout, indexed lookup пуст; **UNVERIFIED**, не NOT_FOUND. Draft `privacy/RKN_DRAFT.md` не отправлен. Live DB path/регион/backup и фактические recipient countries не подтверждены: localization UNCERTAIN.
- `privacy/OPERATIONS.md`: source data map, ручная rights/retention/backup процедура; opt-in maintenance функции с temp-DB tests, guards financial/active obligations, no production default. Нельзя обещать полный erasure финансовых данных или завершённое физическое уничтожение. Админский traceback больше не отправляется в Telegram.
- `landing/LEGAL_REVIEW.md`: точные gates и готовый НЕ отправленный вопрос Prodamus о полной оферте после авторизации. Нет deploy/form/notice/checkout activation. Остались проверка РКН/локализации/recipient evidence, принятие operational procedure, Prodamus confirmation, secure residence injection, затем отдельный production этап.
- E3 technical GREEN: targeted 56/56, один full suite 531/531, compile/diff-check PASS. Не повторять без изменения кода. Local checkpoint не означает legal approval или разрешение публикации. Production и защищённая db.db не менялись; реальный адрес не вводился.

## Актуальные checkpoint'ы

- Stage 7 accepted: deterministic training architecture, 113 controlled exercises/techniques, environment override с полным preview и owner live acceptance.
- Landing live: `https://www.traineros.ru`; apex редиректит на canonical `www`. Визуальный дизайн frozen.
- Website-first / Prodamus checkpoint: `2600ad7ddc4a2437b901c7a3fb062c9bf8e3b584`.
- Legal/footer hotfix: `1127f3032931a7c8b7f1012b97c553689b784ca3`; добавляет canonical Terms, Privacy, Refund, Payment, Seller и Support links и скрывает website purchase CTA, пока `/buy` не запущен.
- Production artifact для hotfix собран локально, но его S3 deploy ещё не воспроизводимо завершён.
- На checkpoint hotfix: landing regression 17/17 PASS; relevant full suite, compile и diff-check green. Не повторять без изменения кода.

## Данные и runtime safety

- `db.db` — защищённые локальные данные: never stage/reset/restore/revert/delete.
- В репозитории есть миграции payment/website work. Authoritative production schema и runtime проверяются live перед activation; не выводятся из локальной `db.db`.
- Есть Selectel public landing и minimal production host/webhook route. Не менять services, DB, DNS, IAM, provider config или secrets без явного scope.
- Real payment не выполнялся. `git push` = NO до отдельного owner authorization.

## Блокеры и следующий безопасный шаг

1. **Reproducible S3 deploy:** secret существующего `traineros-landing-deploy` недоступен worker'у. AWS Tools/CLI/profile отсутствуют, а prior one-shot GUI не может быть показан в текущей Windows desktop session. Не создавать/ротировать IAM keys без отдельного разрешения. Перед deploy нужен реально проверенный secure GUI либо one-shot in-memory credential channel.
2. **Prodamus onboarding:** не должен ждать S3 hotfix, если текущий public site уже содержит обязательный compliance content. Отправить одобренную заявку на free «Старт для самозанятых» через официальный flow при доступной форме; не принимать paid options или новое binding agreement без отдельной boundary.
3. **Website-first activation:** `/buy` пока не public. CTA остаётся скрытой до явной авторизации и проверки dynamic endpoint, OIDC, Prodamus production credentials, DB migration, webhook deploy и controlled E2E.

## Git

- Branch: `develop/fitness-coach-foundation`.
- Ожидаемое pre-existing local изменение: только `db.db`; оно всегда остаётся unstaged.
- Local commits — checkpoints, а не разрешение на deploy или push.
