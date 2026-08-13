# NEXT_TASK.md

## Текущая задача: Stage 7A — exercise taxonomy и controlled library

### Принятый baseline

- Stages 1–5 приняты; onboarding, access/trial, план, guided workout, history, deterministic progression и sandbox payment path работают.
- Stage 6 technical checkpoint завершён: payment runtime разделяет test/production mode fail-closed; webhook processor/HTTP boundary/health contract тестируемы. Реальные production credentials, платежи, webhook registration и deploy не выполнялись.
- Stage 7 product spec зафиксирован в `PROJECT_SPEC.md`; полная декомпозиция находится в `ROADMAP.md`.
- Реальная `db.db` содержит локальные acceptance-данные и остаётся защищённым modified-файлом: не использовать её как test fixture, не stage/commit/reset/restore.

## Цель 7A

Подготовить контролируемую, расширяемую taxonomy упражнений и библиотеку минимум из 70 качественных упражнений для будущих планов, функциональных форматов и runtime replacement. Это фундамент, но не генерация новых планов, не новый onboarding и не UI замены.

## Обязательные продуктовые правила

- Сохранить все существующие стабильные exercise ID и не удалять legacy exercises.
- Покрыть грудь, спину, квадрицепс, заднюю поверхность бедра, ягодицы, плечи, бицепс, трицепс, икры и core.
- Описывать упражнения структурированно: целевая мышца, тип движения, тип оборудования, подходящая среда и уровень/приоритет; не плодить бренды и почти одинаковые варианты.
- Поддержать современные тренажёры, блоки, гантели, штангу, Smith/Hack/leg press и аналогичные распространённые машины, собственный вес, турник/брусья и функциональный инвентарь.
- В taxonomy должны быть данные, чтобы позднее безопасно выбрать замену по мышце, движению, уровню, месту и доступному оборудованию.

## Первый implementation block

1. Изучить существующие `Exercise`, Stage 2 plan data, alternatives, snapshots, migrations и tests.
2. Предложить минимальную schema boundary для taxonomy: какие новые поля действительно нужны и почему текущих `muscle_group`, `primary_muscle_group`, `equipment`, `variant`, `alternative_name`, `restriction_tags` недостаточно.
3. До кода зафиксировать конкретный controlled catalog и mapping legacy exercises; не менять stable IDs.
4. Если migration действительно нужна, реализовать её отдельным маленьким шагом: temp DB и copy acceptance до явного разрешения применять к реальной `db.db`.
5. Добавить/обновить seed/library data и детерминированные tests покрытия, uniqueness и обратной совместимости.

## Вне scope 7A

- onboarding/profile migration;
- генерация планов для новых целей/частот/длительностей;
- новые progression algorithms;
- functional execution UI;
- text-program parser, OCR или AI;
- runtime exercise swap UI;
- production payments, webhook registration, deploy и внешние API calls.

## Safety и checkpoint

- `storage/.env` не читать, не выводить, не менять и не коммитить.
- Реальную `db.db` не менять до отдельного copy-migration acceptance и явного разрешения; никогда не stage/commit/reset/restore.
- Новые миграции сначала проверять на temp DB и копии реальной базы; Stage 1–6 data, plans, sessions и snapshots должны сохраниться.
- Перед checkpoint: targeted tests, полный suite один раз, compileall и `git diff --check`; commit — только с exact whitelist и без push.

## Критерий готовности 7A

Минимальная taxonomy проверена миграционно и покрыта тестами; библиотека содержит минимум 70 качественных упражнений с требуемым покрытием; legacy IDs/планы/history читаемы; следующие блоки могут использовать taxonomy без догадок.
