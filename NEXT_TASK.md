# NEXT_TASK.md

## Текущая задача: Stage 7E — functional + street workout formats

### Принятый baseline

- Stage 7A: controlled exercise taxonomy и библиотека упражнений.
- Stage 7B: расширенный профиль пользователя.
- Stage 7C: детерминированная генерация программы по цели, опыту и среде.
- Stage 7D: persisted multi-type progression для `hypertrophy_load_reps`,
  `strength_load_reps` и `bodyweight_reps`; legacy history остаётся на прежней
  hypertrophy-семантике.
- Migration 9 добавлена и проверена на временной БД и копии реальной БД;
  реальная `db.db` по-прежнему находится на migration 6 и защищена.
- Полный regression checkpoint Stage 7D: 266/266 PASS.

## Цель 7E

Добавить детерминированные функциональные и street-workout форматы поверх
существующей taxonomy, не смешивая их с rep-based progression Stage 7D.

Следующий дизайн должен отдельно определить prescriptions и progression для:

- timed work;
- AMRAP;
- EMOM;
- For Time;
- rounds и distance.

Нельзя переиспользовать килограммы/повторения там, где source of truth должен
быть временем, раундами или дистанцией. До утверждения product logic не
добавлять speculative DB fields и не менять существующую историю.

## Safety

- `db.db` не stage/reset/restore и не мигрировать без отдельного подтверждения.
- `storage/.env` не читать и не менять.
- Никаких Telegram/YooKassa network calls, production/deploy или push без
  отдельного явного подтверждения владельца.
