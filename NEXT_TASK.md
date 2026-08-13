# NEXT_TASK.md

## Текущая задача: Stage 6 — production-readiness коммерческих платежей

### Принятый baseline

- Stages 1–4 приняты; onboarding, trial, планы, guided workout, history и deterministic progression работают.
- Stage 5 принят в sandbox: migration 6, `SubscriptionPayment`, идемпотентный `PaymentService`, YooKassa adapter, Telegram UX и безопасный polling lifecycle реализованы.
- Sandbox YooKassa shop подтверждён; один sandbox payment успешно обработан. Повторный reconcile не изменил период доступа повторно.
- UX корректно различает текущий trial и уже оплаченный платный период, зарезервированный после trial; технический product code и лишние платёжные кнопки скрыты.
- Последний полный unittest suite: **204/204 PASS**. Реальная `db.db` содержит sandbox acceptance-данные и остаётся защищённым локально modified файлом.
- Single-instance guard предотвращает второй локальный polling до обращения к Telegram; причина локальных 409 устранена.

## Цель Stage 6

Подготовить отдельный, контролируемый путь к коммерческой эксплуатации платежей. На этом этапе нельзя считать sandbox-настройки, цену 100 RUB или тестовую оплату готовыми к production.

## Обязательные предварительные решения владельца

До любых production-вызовов или deploy письменно зафиксировать:

- коммерческую цену, валюту, период доступа, условия возврата и отображаемый пользователю текст;
- юридический статус продавца/получателя платежей и merchant-настройки YooKassa;
- применимые требования 54-ФЗ, чеков и передачи фискальных данных;
- production return URL, публичный HTTPS endpoint и способ безопасного хранения production credentials;
- процедуру поддержки: отмены, возвраты, спорные платежи и обработку недоступности провайдера.

## План работы

### Phase 1. Commercial and legal readiness

- Зафиксировать утверждённые коммерческие условия и юридическую схему до изменения product config.
- Определить применимые требования к 54-ФЗ/чекам и ответственную сторону за их исполнение.
- Не использовать sandbox цену как fallback или production price.

### Phase 2. Production configuration and deployment design

- Спроектировать отдельную production-конфигурацию без коммита секретов.
- Подготовить безопасный deployment, backup/restore и наблюдаемость без утечки персональных данных или секретов.
- Подтвердить single-instance lifecycle для выбранной production-среды.
- Runtime contract: Internet → HTTPS/TLS termination на deployment edge/reverse proxy → private/internal aiohttp webhook server → `PaymentWebhookProcessor` → authoritative YooKassa verification → идемпотентное применение доступа через `PaymentService`. Публичный webhook endpoint обязан быть HTTPS; aiohttp не управляет сертификатами, а forwarded headers не являются security proof. Регистрация production webhook — отдельное явное внешнее действие.

### Phase 3. Verified payment confirmation

- Спроектировать и протестировать production webhook с проверкой подлинности и идемпотентной обработкой.
- Сохранить polling/reconcile только как контролируемый резервный путь, если он необходим.
- Не доверять redirect Telegram или странице успеха как доказательству оплаты.

### Phase 4. Controlled production acceptance

- Перед первым реальным платежом создать backup и пройти отдельный safety gate владельца.
- Провести ограниченную production-приёмку: успешная оплата, повторное событие, отмена/ошибка и отсутствие двойного продления.
- Отдельно подтвердить deploy и операционный план отката.

## Вне scope без отдельного подтверждения

- реальные production payment/create/reconcile вызовы;
- production credentials, webhook registration, deploy или публикация;
- изменение коммерческой цены, возвратов или юридических условий по предположению;
- очистка/восстановление `db.db`, удаление sandbox payment или переписывание Git history.

## Постоянные ограничения

- `storage/.env` не читать, не выводить, не менять и не коммитить.
- `db.db` защищена: не stage, не commit, не reset/restore и не использовать для экспериментов.
- Push, реальная внешняя операция и production action требуют отдельного явного подтверждения владельца.

## Критерий готовности Stage 6

Коммерческие, юридические и технические prerequisites документированы и одобрены; production confirmation path, deployment и acceptance plan воспроизводимо проверены до первого реального платежа.
