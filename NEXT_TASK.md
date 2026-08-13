# NEXT_TASK.md

## Текущая задача: Stage 7F — user-provided text program

### Принятый baseline

- Stage 7A–7C: controlled taxonomy, training profile и deterministic generator.
- Stage 7D: persisted progression для hypertrophy, strength и bodyweight.
- Stage 7E: `standard_sets`, AMRAP, EMOM, For Time и street circuits реализованы
  как immutable plan/session blocks с отдельными structured results.
- Migration 10 проверена на временной БД и копии реальной БД через 7→8→9→10;
  реальная `db.db` остаётся на migration 6 и защищена.
- Regression checkpoint Stage 7E: targeted 85/85 PASS; full suite 274/274 PASS,
  дополнительный snapshot/restart regression 5/5 PASS.

## Цель 7F

Спроектировать безопасный импорт пользовательской программы из текста в
существующие plan/block snapshots. Ввод должен проходить controlled parsing,
validation и явное подтверждение до назначения плана.

Нужно сохранить:

- привязку упражнений к taxonomy без опасного name guessing;
- структурированные sets/reps/rest и форматы blocks;
- environment и beginner-safety validation;
- неизменяемость уже начатых workout sessions;
- понятный controlled reject для неоднозначного или неполного текста.

AI/LLM parsing, если он понадобится, не должен напрямую писать plan или history:
результат сначала преобразуется в валидируемый draft обычным кодом.

## Safety

- `db.db` не stage/reset/restore и не мигрировать без отдельного подтверждения.
- `storage/.env` не читать и не менять.
- Никаких Telegram/YooKassa network calls, production/deploy или push без
  отдельного явного подтверждения владельца.
