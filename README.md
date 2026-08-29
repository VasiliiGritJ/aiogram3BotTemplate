# AI Trainer Telegram Bot

Русскоязычный Telegram-бот персонального фитнес-тренера на Python 3.12,
aiogram 3, SQLAlchemy и SQLite. Проект включает детерминированные тренировочные
программы, журнал и прогрессию, trial/подписку YooKassa и отдельный HTTP runtime
для платёжных уведомлений.

## Локальная разработка

1. Создайте виртуальное окружение и установите зависимости из
   `storage/requirements.txt`.
2. Скопируйте `storage/.env.example` в локальный `storage/.env` и замените
   заглушки. Этот файл игнорируется Git и никогда не должен коммититься.
3. Примените миграции через maintenance-команду после создания проверенного
   backup (см. ниже).
4. Запустите bot role явно через локальный loader:

   ```powershell
   .venv\Scripts\python.exe bot.py
   ```

Webhook в локальном test mode запускается отдельно и только по явному флагу:

```powershell
.venv\Scripts\python.exe payment_webhook_server.py --local-env
```

Обычный `import bot` или импорт `storage.config` не читает `.env`, не создаёт
Telegram client и не запускает сеть.

## Runtime roles и конфигурация

Production получает настройки только из переменных окружения процесса.
Наличие credentials само по себе не включает production: одновременно требуются
`APP_ENV=production` и `PAYMENTS_MODE=production`. Несовпадение test/production
shop проверяется через существующий fail-closed provider guard до создания
платежа.

Bot role (`python bot.py --production`) требует:

- `APP_ENV`, абсолютный `DATABASE_PATH`;
- `BOT_TOKEN`; опционально `TELEGRAM_PROXY_URL`;
- опциональные `ADMIN_IDS`, `SYSTEMD_SERVICE_NAME`, `DEBUG_MODE`;
- `PAYMENTS_MODE`, `SUBSCRIPTION_AMOUNT_MINOR`, `SUBSCRIPTION_CURRENCY`,
  `YOOKASSA_RETURN_URL`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_TOKEN`.

Webhook role (`python payment_webhook_server.py`) требует:

- `APP_ENV=production`, абсолютный `DATABASE_PATH`;
- все payment-переменные выше;
- `PAYMENT_WEBHOOK_BIND_HOST`, `PAYMENT_WEBHOOK_BIND_PORT`,
  `PAYMENT_WEBHOOK_PATH`.

Maintenance role не читает Telegram/YooKassa credentials: ему передаётся только
явный путь к БД. Ошибки bootstrap не содержат значения секретов.

## NPD income receipts

Для выбранного launch route самозанятого платёж и чек разделены. YooKassa
подтверждает оплату, а отдельный provider-neutral outbox хранит неизменяемое
намерение зарегистрировать доходный чек НПД через «Мой налог» или будущего
уполномоченного оператора. Это не моделируется как онлайн-касса/ККТ по 54-ФЗ и не
является юридической гарантией: итоговую merchant/налоговую схему нужно подтвердить
перед запуском.

`RECEIPTS_MODE=disabled|test|production` задаётся явно. Локальная разработка без
receipt provider сохраняет `disabled`. Production runtime fail-closed, пока не
выбраны `RECEIPT_PROVIDER`, авторизованный adapter и его отдельные secrets. В Git
нет и не должно быть реальных receipt credentials.

Успешная авторитетно подтверждённая оплата атомарно выдаёт доступ и создаёт ровно
один receipt intent. Повторный webhook/reconcile не создаёт новый чек и не
продлевает доступ повторно. Ошибка доставки чека сохраняется для bounded retry, но
не меняет подтверждённую оплату обратно на failed. Исторические платежи migration
автоматически не фискализирует.

Текущий server-side продукт: 499 RUB за 30 дней без автопродления. Неизменяемое
описание receipt item: `Доступ к сервису «Мой AI Тренер» на 30 дней`; внутренний
код `monthly_30d_v1` не используется как пользовательское описание.

## SQLite: два процесса

Поддерживаемая MVP-схема — один bot process и один webhook process с одной
persistent SQLite DB. Все SQLAlchemy connections централизованно получают:

- `foreign_keys=ON`;
- `busy_timeout=5000` мс и connection timeout 5 секунд;
- WAL journal и `synchronous=NORMAL`.

WAL позволяет коротким bot/webhook-транзакциям читать и писать без обычной
блокировки reader-versus-writer. Каждый service operation создаёт собственную
SQLAlchemy Session; Session не разделяется между потоками или процессами.
Платёжная идемпотентность и атомарная выдача доступа остаются на уровне БД/service.

SQLite не является целью для нескольких application replicas или высокой
постоянной write-нагрузки. При появлении нескольких bot/webhook replicas,
регулярном исчерпании busy timeout или необходимости HA следующий шаг —
PostgreSQL, а не увеличение таймаутов.

## Миграции, backup и restore

Перед production migration:

1. Остановить bot и webhook writers.
2. Проверить абсолютный путь активной БД.
3. Создать новый backup вне репозитория и вне каталога активной БД.
4. Применить только pending migrations.
5. Проверить `quick_check`, foreign keys и полный набор schema versions.
6. Запускать webhook/bot только после PASS.

Maintenance helper использует SQLite Backup API, поэтому snapshot включает
закоммиченные данные WAL. Нельзя заменять его копированием одного `db.db` при
активном WAL.

```powershell
.venv\Scripts\python.exe -m db.maintenance validate --database D:\data\trainer.db
.venv\Scripts\python.exe -m db.maintenance backup --database D:\data\trainer.db --destination D:\backups\trainer-YYYYMMDD.db
.venv\Scripts\python.exe -m db.maintenance migrate --database D:\data\trainer.db --backup D:\backups\trainer-pre-migration-YYYYMMDD.db --writers-stopped
.venv\Scripts\python.exe -m db.maintenance restore-copy --backup D:\backups\trainer-YYYYMMDD.db --destination D:\restore-drill\trainer.db
```

Backup/restore destination должен быть новым: существующий файл не
перезаписывается. При ошибке миграции процессы остаются остановленными; helper не
делает автоматический restore. Восстановление выполняется оператором в новый файл
после проверки причины и integrity.

## Production startup и health

Рекомендуемый порядок: migration gate → webhook process → bot process → внешний
reverse proxy. TLS и публичный HTTPS endpoint принадлежат deploy/reverse-proxy
слою, а webhook bind остаётся приватным.

- `/health/live` подтверждает, что HTTP process жив;
- `/health/ready` доступен только у runtime, собранного после успешной проверки
  конфигурации и схемы.

Health endpoints не вызывают YooKassa, не меняют БД и не раскрывают пути или
секреты. При shutdown webhook освобождает DB engine; bot корректно останавливает
polling, закрывает Telegram session, DB resources и single-instance guard.

Runtime `db.db`, `db.db-wal`, `db.db-shm`, backups и `storage/.env` никогда не
должны попадать в Git staging или deployment через Git.
