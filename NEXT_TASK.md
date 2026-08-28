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

### Порядок решений и работ

1. Утвердить коммерческую цену, период и продуктовую формулировку отдельно от
   sandbox value; не использовать test price как production fallback.
2. Подтвердить merchant/self-employed, налоговую и 54-ФЗ/receipt модель с
   компетентным специалистом. Это внешняя юридическая граница, не задача кода.
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
