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
