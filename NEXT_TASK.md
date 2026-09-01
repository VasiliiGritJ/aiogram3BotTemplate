# NEXT_TASK.md

## Следующая задача: Stage 8 — pre-launch commercial polish и production readiness

### Принятый baseline

- Stage 7 ACCEPTED / COMPLETE: контролируемая библиотека из 113 упражнений,
  техника 113/113, deterministic evidence-oriented generation, weekly balance,
  multi-type progression, functional/street formats, user text programs,
  runtime replacement, per-session environment override и адаптированный preview
  перед execution.
- Миграции 7–12 безопасно применены к реальной защищённой `db.db`; workout history,
  access и единственная sandbox payment сохранены.
- Final owner live acceptance пройдена; последний полный unittest suite — 357/357
  PASS. Локальный polling lifecycle защищён single-instance guard.
- Stage 5–6 дали sandbox payment flow, fail-closed test/production mode boundary и
  production webhook foundation. Реальные production payments, webhook registration
  и deploy не выполнялись.

## Цель Stage 8

Подготовить коммерческий и operational go/no-go перед production payments, не
создавая реальные списания до отдельных явных подтверждений владельца.

### Текущий технический checkpoint

- Stage 8A–8C подготовили gap audit, централизованную admin authorization,
  role-specific runtime bootstrap и безопасный SQLite/WAL contract для одного bot
  и одного webhook process.
- Import-time dotenv/network side effects удалены; production конфигурация и
  test/production payment mode проверяются fail-closed до запуска role.
- Migration/backup/restore gate использует SQLite Backup API и не требует
  Telegram/payment secrets.
- Следующий безопасный блок — **Stage 8D: fiscal/receipt boundary decision**. Это
  внешнее коммерческое/юридическое решение; receipt/54-ФЗ код не добавлять до его
  явного утверждения.

### Stage 8D1 technical checkpoint

- Launch route зафиксирован как self-employed/NPD: доходный чек НПД отделён от
  YooKassa payment lifecycle и не моделируется как 54-ФЗ KKT flow.
- Migration 13 добавляет durable provider-neutral receipt outbox без backfill
  исторического sandbox payment. Payment success, access grant и новый receipt
  intent сходятся в одной exactly-once транзакции.
- Receipt worker foundation использует стабильный idempotency key, SQLite-safe
  claim lease и bounded retry; внешнего provider adapter/daemon пока нет.
- Production receipt runtime остаётся fail-closed. Следующая внешняя граница —
  выбрать уполномоченного ФНС оператора НПД и подтвердить совместимость с текущей
  YooKassa/merchant схемой до credentials, сети и production acceptance.

### Stage 8D2 technical checkpoint

- Migration 14 добавляет nullable `receipt_email` в fitness profile без backfill.
  Email собирается только при первом входе в purchase flow, хранится в профиле и
  не является маркетинговым согласием.
- Новый receipt intent получает неизменяемый provider-neutral snapshot email;
  повторный reconcile/webhook не создаёт новую оплату или receipt intent.
- Конкретный payment/NPD provider всё ещё не выбран; PayAnyWay в код не
  интегрирован. Следующий безопасный блок — продолжать production readiness без
  внешних API, пока не принято это коммерческое решение.

### Stage 8D3 technical checkpoint

- Коммерческий контракт launch offer централизован в коде: `Мой AI Тренер`,
  499 RUB за 30 дней без автопродления; user-facing receipt description не
  использует технический product code.
- Purchase UX показывает цену, период, отсутствие автопродления и сохранённый
  email чека; документы, политика конфиденциальности, условия отказа/возврата и
  поддержка доступны из безопасных конфигурационных ссылок.
- В paid production mode обязательны `PUBLIC_SITE_URL`, `TERMS_URL`,
  `PRIVACY_URL`, `REFUND_URL` и `SUPPORT_EMAIL`: отсутствующее, небезопасное или
  не-HTTPS значение fail-closed до provider create. В development/test незаданные
  публичные действия не показываются.
- Конкретный payment/NPD provider, реальные public URLs, merchant/legal
  approvals и hosting остаются внешней границей; PayAnyWay не интегрирован.

### Stage 8E1 technical checkpoint

- Подготовлена статическая русскоязычная landing-страница «Мой AI Тренер» для
  будущего маркетинга и payment compliance: продукт 499 ₽ / 30 дней / без
  автопродления, реальные функции MVP, FAQ, условия, приватность, возврат,
  реквизиты, поддержку и краткое fitness safety disclosure.
- Production publication fail-closed требует публичные `PUBLIC_SITE_URL`,
  `TELEGRAM_BOT_URL`, `SELLER_FULL_NAME`, `SELLER_INN`, `SUPPORT_EMAIL`; в Git
  остаются только пустые placeholders. Без подтверждённых seller details и URL
  страница не считается готовой для коммерческой публикации.
- Landing не запущен и не опубликован; PayAnyWay не интегрирован. Следующая
  граница — owner visual review, затем отдельные решения о домене, hosting/TLS и
  финальном compliance review.

### Порядок решений и работ

1. Зафиксировано: 499 RUB, 30 дней, без автопродления; server-side receipt item —
   `Доступ к сервису «Мой AI Тренер» на 30 дней`.
2. Подтвердить финальную merchant/self-employed/NPD receipt модель и выбранного
   уполномоченного оператора с компетентным специалистом. Это внешняя юридическая
   граница, не задача кода.
3. Подготовить production credentials/configuration только после подтверждения
   типа магазина и fail-closed verification; secrets не читать и не коммитить.
4. Выбрать hosting/public HTTPS endpoint, подключить deployment contract и
   зарегистрировать webhook только отдельным разрешённым шагом.
5. Провести ограниченную production acceptance без повторных/случайных списаний:
   provider verification, controlled payment, webhook delivery, idempotent access,
   receipt/merchant checks и rollback/incident procedure.

### Safety

- Не переключать `PAYMENTS_MODE=production` только из-за наличия credentials.
- Не создавать реальный платёж, не регистрировать webhook и не выполнять deploy без
  отдельного явного подтверждения владельца после внешних решений выше.
- `db.db` остаётся защищённой: не stage/reset/restore/delete; `storage/.env` не
  читать, не показывать и не коммитить.
- Не смешивать Stage 8 с новыми тренировочными функциями, медицинскими заявлениями
  или неограниченной AI-генерацией.

### Done-критерий

Production launch возможен только после отдельного commercial/legal approval,
проверенного HTTPS/webhook deployment и успешной контролируемой production
acceptance с доказанной идемпотентностью доступа.
