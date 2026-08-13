# NEXT_TASK.md

## Следующая задача: Stage 7H — полный product regression и live acceptance

### Принятый baseline

- Stage 7A–7F: контролируемая библиотека из 111 упражнений, расширенный профиль,
  deterministic generator, multi-type progression, functional/street formats и
  безопасный импорт пользовательской программы из текста.
- Stage 7G: во время активной тренировки можно один раз заменить ещё не начатое
  упражнение на максимум три безопасных эквивалента из taxonomy. Выбор учитывает
  среду, опыт, primary muscle, movement pattern, progression capability,
  equivalence/role и оборудование.
- `planned_exercise_id` и planned snapshot остаются неизменными; меняется только
  selected snapshot текущей WorkoutSession. Progression продолжает искать историю
  по фактически выполненному `selected_exercise_id`.
- STRICT user program не предлагает замену; REPLACEMENTS/ADAPTIVE и generated
  plans поддерживают её. После первого сохранённого подхода или старта timed block
  замена блокируется. Stale/duplicate callbacks и ownership проверяются сервисом.
- Migration 12 не потребовалась: текущая schema уже содержит immutable
  planned/selected snapshots. Реальная `db.db` остаётся на migration 6 и защищена.
- Последний local checkpoint: полный unittest suite 300/300 PASS, compileall PASS,
  `git diff --check` PASS. Live Telegram acceptance ещё не выполнялся.

## Цель Stage 7H

Провести контролируемую приёмку всего Stage 7 на временной БД/копии и затем в
Telegram только по отдельному явному разрешению владельца. Цель — подтвердить,
что новые profile/generator/format/user-program/replacement flows не нарушают
access, subscription, history, resume и polling lifecycle.

### Обязательные проверки

- Один полный regression suite, compileall и `git diff --check` только после
  окончательного интеграционного checkpoint, без повторов при отсутствии новых
  кодовых изменений.
- Copy acceptance migrations 7→8→9→10→11: idempotent second run, `quick_check`,
  `foreign_key_check`, сохранность Stage 1–6 данных. Реальную `db.db` не
  мигрировать без отдельного решения владельца.
- Targeted end-to-end scenarios на temporary DB: generated plans по profile,
  strict/replacements/adaptive user program, standard and timed formats,
  replacement before start/after saved work, selected-history progression,
  cancel/resume/history.
- Live acceptance — отдельные ограниченные шаги: сначала ровно один bot process,
  затем owner-driven Telegram checks. Не создавать payments и не выполнять
  YooKassa actions.

### Safety

- `db.db` не stage/reset/restore/мигрировать; `storage/.env` не читать и не менять.
- Не выполнять Telegram/YooKassa network actions, bot launch, deploy или push без
  отдельного явного подтверждения владельца.
- Новые schema changes/Migration 12 не создавать без доказанного пробела в
  immutable snapshot semantics.
- Не смешивать Stage 7H с production payments, webhook registration, legal/commercial
  decisions или расширением catalogue без отдельной задачи.

### Done-критерий

Stage 7H завершается только после зелёных технических проверок, copy migration
acceptance и явной owner live acceptance. После этого можно отдельно обновить
ROADMAP и запросить разрешение на точный checkpoint commit/push.
