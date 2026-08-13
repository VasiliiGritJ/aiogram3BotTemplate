# NEXT_TASK.md

## Текущая задача: Stage 7G — замена упражнения во время тренировки

### Принятый baseline

- Stage 7A–7E: controlled taxonomy, расширенный профиль, deterministic generator,
  multi-type progression и durable workout formats.
- Stage 7F: пользователь может прислать программу обычным русским текстом,
  проверить структурированный preview и сохранить её только после подтверждения.
- Сохранённый план различает `generated` / `user_defined` и режимы `strict` /
  `replacements` / `adaptive`; эти признаки snapshot-ятся в начатую тренировку.
- Deterministic parser сохраняет только controlled exercise IDs, отклоняет
  неоднозначные/нераспознанные строки и не хранит исходный текст постоянно.
- Migration 11 проверена на временной БД и новой копии реальной БД через
  7→8→9→10→11; реальная `db.db` остаётся на migration 6 и защищена.
- Regression checkpoint Stage 7F: targeted 112/112 PASS; полный suite и точный
  commit зафиксированы в отчёте текущего checkpoint.

## Цель 7G

Добавить контролируемую замену текущего упражнения во время активной тренировки:

- предлагать только совместимые alternatives из controlled taxonomy с учётом
  среды, паттерна движения, целевой мышцы и доступного оборудования;
- разрешать замену только для планов, чей `adaptation_mode` допускает её;
- проверять ownership, активную session и актуальный cursor на каждом действии;
- никогда не переписывать planned snapshot: история должна отдельно показывать,
  что было запланировано и что фактически выбрано;
- progression альтернативы должна использовать её собственный
  `selected_exercise_id`, не историю исходного упражнения;
- stale/double callbacks, resume, cancel и restart должны быть безопасны;
- не создавать второй workout engine и не менять уже записанные результаты.

Migration 12 не ожидается: существующие planned/selected snapshots уже рассчитаны
на runtime swap. Если анализ выявит реальный недостаток схемы, остановиться и
сначала объяснить его владельцу.

## Safety

- `db.db` не stage/reset/restore и не мигрировать без отдельного подтверждения.
- `storage/.env` не читать и не менять.
- Никаких Telegram/YooKassa network calls, production/deploy или push без
  отдельного явного подтверждения владельца.
- Сначала targeted service/UI tests, затем один full suite, compileall и
  `git diff --check`; live Telegram acceptance — отдельный шаг.
