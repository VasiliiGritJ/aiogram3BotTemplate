"""Versioned, controlled exercise taxonomy used by workout planning."""

from dataclasses import dataclass


MUSCLE_GROUP_LABELS = {
    "chest": "грудь",
    "back": "спина",
    "quads": "квадрицепс",
    "hamstrings": "задняя поверхность бедра",
    "glutes": "ягодицы",
    "shoulders": "плечи",
    "biceps": "бицепс",
    "triceps": "трицепс",
    "calves": "икры",
    "core": "кор",
}

EQUIPMENT_CATEGORIES = {
    "machine",
    "cable",
    "dumbbell",
    "barbell",
    "smith",
    "bodyweight",
    "pullup_dip_station",
    "functional_equipment",
}
TRAINING_ENVIRONMENTS = {"gym", "functional_gym", "street", "home"}
EXPERIENCE_LEVELS = {"beginner", "intermediate", "advanced"}
MOVEMENT_PATTERNS = {
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "lunge",
    "scapular_rear_delt",
    "squat",
    "hinge",
    "isolation",
    "carry",
    "core",
    "locomotion_conditioning",
}
PROGRESSION_TYPES = {
    "external_load_reps",
    "bodyweight_reps",
    "timed_conditioning",
    "distance_other",
}

ALL_LEVELS = ("beginner", "intermediate", "advanced")
INTERMEDIATE_PLUS = ("intermediate", "advanced")


@dataclass(frozen=True)
class ExerciseDefinition:
    code: str
    name: str
    primary_muscle_group: str
    equipment: str
    environments: tuple[str, ...]
    experience_levels: tuple[str, ...]
    movement_pattern: str
    progression_type: str
    equivalence_group: str
    hint: str
    secondary_muscle_groups: tuple[str, ...] = ()
    restriction_tags: tuple[str, ...] = ()
    variant: str | None = None
    alternative_name: str | None = None

    @property
    def muscle_group(self) -> str:
        """Backward-compatible canonical primary muscle code."""
        return self.primary_muscle_group

    @property
    def primary_muscle_label(self) -> str:
        return MUSCLE_GROUP_LABELS[self.primary_muscle_group]

    @property
    def technique(self) -> "ExerciseTechnique":
        """Return compact controlled guidance without persisting UI-only copy."""
        override = _TECHNIQUE_OVERRIDES.get(self.code)
        if override is not None:
            return override
        return ExerciseTechnique(
            start_position=_START_POSITION_BY_EQUIPMENT[self.equipment],
            action=_ACTION_BY_MOVEMENT[self.movement_pattern],
            control=self.hint,
        )


@dataclass(frozen=True)
class ExerciseTechnique:
    start_position: str
    action: str
    control: str


_START_POSITION_BY_EQUIPMENT = {
    "machine": "Настройте тренажёр под свой рост и займите устойчивое положение у опоры.",
    "cable": "Выберите небольшой стартовый вес, встаньте устойчиво и возьмитесь за рукоять.",
    "dumbbell": "Возьмите гантели комфортного веса и примите устойчивое исходное положение.",
    "barbell": "Подготовьте штангу с комфортным весом и примите устойчивое исходное положение.",
    "smith": "Настройте высоту грифа и ограничители, затем займите устойчивое положение.",
    "bodyweight": "Примите устойчивое исходное положение и соберите корпус.",
    "pullup_dip_station": "Надёжно возьмитесь за опору и стабилизируйте корпус.",
    "functional_equipment": "Проверьте устойчивость оборудования и займите свободное исходное положение.",
}

_ACTION_BY_MOVEMENT = {
    "horizontal_push": "Плавно выжмите сопротивление от корпуса и подконтрольно вернитесь.",
    "vertical_push": "Плавно выжмите сопротивление вверх и подконтрольно вернитесь.",
    "horizontal_pull": "Потяните сопротивление к корпусу и плавно верните руки.",
    "vertical_pull": "Потяните сопротивление сверху к корпусу и плавно вернитесь.",
    "lunge": "Сделайте контролируемый шаг и опускайтесь до комфортной глубины, затем вернитесь через устойчивую стопу.",
    "scapular_rear_delt": "Двигайте руками плавно, сохраняя плечи опущенными и лопатки под контролем.",
    "squat": "Согните ноги в комфортной амплитуде и поднимитесь через устойчивые стопы.",
    "hinge": "Отведите таз назад, затем выпрямитесь за счёт ног и таза.",
    "isolation": "Выполните движение в суставе плавно, сохраняя остальной корпус неподвижным.",
    "carry": "Двигайтесь ровными короткими шагами, сохраняя устойчивый корпус.",
    "core": "Выполняйте заданное движение медленно, удерживая корпус собранным.",
    "locomotion_conditioning": "Двигайтесь в ровном контролируемом темпе и сохраняйте пространство вокруг.",
}

_TECHNIQUE_OVERRIDES = {
    "bird_dog": ExerciseTechnique(
        start_position="Встаньте на четвереньки: ладони под плечами, колени под тазом.",
        action="Одновременно вытяните вперёд одну руку и назад противоположную ногу, затем вернитесь.",
        control="Не разворачивайте таз и не прогибайте поясницу.",
    ),
}


def _e(
    code: str,
    name: str,
    muscle: str,
    equipment: str,
    environments: tuple[str, ...],
    movement: str,
    progression: str,
    equivalence: str,
    hint: str,
    *,
    levels: tuple[str, ...] = ALL_LEVELS,
    secondary: tuple[str, ...] = (),
    restrictions: tuple[str, ...] = (),
    variant: str | None = None,
    alternative_name: str | None = None,
) -> ExerciseDefinition:
    return ExerciseDefinition(
        code=code,
        name=name,
        primary_muscle_group=muscle,
        equipment=equipment,
        environments=environments,
        experience_levels=levels,
        movement_pattern=movement,
        progression_type=progression,
        equivalence_group=equivalence,
        hint=hint,
        secondary_muscle_groups=secondary,
        restriction_tags=restrictions,
        variant=variant,
        alternative_name=alternative_name,
    )


# The first 15 definitions intentionally preserve the legacy catalog order.
# Fresh databases therefore keep the historical IDs 1..15; existing databases
# are updated by stable code and never have their IDs reassigned.
EXERCISE_DEFINITIONS = (
    _e("leg_press", "Жим ногами под углом в тренажёре", "quads", "machine", ("gym",), "squat", "external_load_reps", "knee_dominant_press", "Прижимайте спину к опоре и двигайтесь без рывков.", secondary=("glutes",), restrictions=("knee",), variant="под углом", alternative_name="жим платформы ногами"),
    _e("seated_leg_curl", "Сгибание ног сидя в тренажёре", "hamstrings", "machine", ("gym",), "isolation", "external_load_reps", "leg_curl", "Сохраняйте ровный темп и не бросайте вес.", restrictions=("knee",)),
    _e("chest_press", "Горизонтальный жим сидя в рычажном тренажёре", "chest", "machine", ("gym",), "horizontal_push", "external_load_reps", "horizontal_chest_press", "Держите лопатки у спинки и не выпрямляйте локти резко.", secondary=("triceps", "shoulders"), restrictions=("shoulder",), variant="горизонтальный", alternative_name="жим от груди в тренажёре"),
    _e("lat_pulldown", "Тяга верхнего блока к груди", "back", "cable", ("gym", "functional_gym"), "vertical_pull", "external_load_reps", "vertical_pull", "Тяните рукоять к верхней части груди без раскачивания.", secondary=("biceps",), restrictions=("shoulder",), variant="к груди", alternative_name="верхняя тяга"),
    _e("seated_row", "Горизонтальная тяга нижнего блока сидя", "back", "cable", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Сохраняйте нейтральную спину и ведите локти назад.", secondary=("biceps",), restrictions=("back", "shoulder")),
    _e("shoulder_press", "Жим вверх в тренажёре", "shoulders", "machine", ("gym",), "vertical_push", "external_load_reps", "vertical_press", "Не прогибайтесь и работайте в комфортной амплитуде.", secondary=("triceps",), restrictions=("shoulder",)),
    _e("cable_curl", "Сгибание рук на нижнем блоке", "biceps", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Держите локти рядом с корпусом."),
    _e("triceps_pushdown", "Разгибание рук на верхнем блоке", "triceps", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_extension", "Не разводите локти и не раскачивайте корпус.", restrictions=("shoulder",)),
    _e("hip_abduction", "Разведение ног в тренажёре", "glutes", "machine", ("gym",), "isolation", "external_load_reps", "hip_abduction", "Двигайтесь плавно и сохраняйте устойчивое положение корпуса.", restrictions=("knee",)),
    _e("calf_raise", "Подъём на носки в тренажёре", "calves", "machine", ("gym",), "isolation", "external_load_reps", "calf_raise", "Поднимайтесь и опускайтесь подконтрольно.", restrictions=("knee",)),
    _e("back_extension", "Разгибание корпуса в тренажёре", "glutes", "machine", ("gym", "functional_gym"), "hinge", "bodyweight_reps", "hip_hinge_extension", "Не переразгибайте спину и двигайтесь медленно.", secondary=("hamstrings", "back"), restrictions=("back",)),
    _e("cable_crunch", "Скручивание на верхнем блоке", "core", "cable", ("gym", "functional_gym"), "core", "external_load_reps", "trunk_flexion", "Скручивайте корпус без рывка и не тяните руками.", restrictions=("back",)),
    _e("barbell_bench_press", "Жим штанги лёжа на горизонтальной скамье", "chest", "barbell", ("gym", "functional_gym"), "horizontal_push", "external_load_reps", "horizontal_chest_press", "Опускайте штангу контролируемо к середине груди и сохраняйте устойчивое положение лопаток.", levels=INTERMEDIATE_PLUS, secondary=("triceps", "shoulders"), variant="горизонтальная скамья"),
    _e("dumbbell_bench_press", "Жим гантелей лёжа на горизонтальной скамье", "chest", "dumbbell", ("gym", "functional_gym"), "horizontal_push", "external_load_reps", "horizontal_chest_press", "Двигайте гантели плавно и не теряйте контроль в нижней точке.", secondary=("triceps", "shoulders"), variant="горизонтальная скамья"),
    _e("barbell_back_squat", "Приседания со штангой на спине", "quads", "barbell", ("gym", "functional_gym"), "squat", "external_load_reps", "barbell_squat", "Сохраняйте нейтральную спину и контролируйте глубину в комфортной амплитуде.", levels=INTERMEDIATE_PLUS, secondary=("glutes", "hamstrings", "core")),

    # Chest
    _e("incline_chest_press_machine", "Наклонный жим от груди в тренажёре", "chest", "machine", ("gym",), "horizontal_push", "external_load_reps", "incline_chest_press", "Сохраняйте лопатки прижатыми к опоре.", secondary=("triceps", "shoulders")),
    _e("decline_chest_press_machine", "Жим от груди с отрицательным наклоном в тренажёре", "chest", "machine", ("gym",), "horizontal_push", "external_load_reps", "decline_chest_press", "Не отрывайте плечи от опоры.", secondary=("triceps",)),
    _e("pec_deck_fly", "Сведение рук в тренажёре pec deck", "chest", "machine", ("gym",), "isolation", "external_load_reps", "chest_fly", "Сводите руки плавно, не поднимая плечи."),
    _e("cable_chest_fly", "Сведение рук в кроссовере стоя", "chest", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "chest_fly", "Сохраняйте небольшой сгиб локтей и устойчивый корпус."),
    _e("incline_dumbbell_press", "Жим гантелей на наклонной скамье", "chest", "dumbbell", ("gym", "functional_gym"), "horizontal_push", "external_load_reps", "incline_chest_press", "Контролируйте гантели и сохраняйте опору стоп.", secondary=("triceps", "shoulders")),
    _e("dumbbell_fly", "Разведение гантелей лёжа", "chest", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "chest_fly", "Не опускайте локти глубже комфортной амплитуды.", levels=INTERMEDIATE_PLUS),
    _e("incline_barbell_bench_press", "Жим штанги на наклонной скамье", "chest", "barbell", ("gym", "functional_gym"), "horizontal_push", "external_load_reps", "incline_chest_press", "Опускайте штангу контролируемо к верхней части груди.", levels=INTERMEDIATE_PLUS, secondary=("triceps", "shoulders")),
    _e("smith_bench_press", "Жим лёжа в тренажёре Смита", "chest", "smith", ("gym",), "horizontal_push", "external_load_reps", "horizontal_chest_press", "Настройте скамью так, чтобы гриф двигался к середине груди.", secondary=("triceps", "shoulders")),
    _e("push_up", "Отжимания от пола", "chest", "bodyweight", ("gym", "functional_gym", "street", "home"), "horizontal_push", "bodyweight_reps", "horizontal_chest_press", "Сохраняйте корпус прямым и опускайтесь подконтрольно.", secondary=("triceps", "shoulders", "core")),
    _e("incline_push_up", "Отжимания от высокой опоры", "chest", "bodyweight", ("gym", "functional_gym", "street"), "horizontal_push", "bodyweight_reps", "horizontal_chest_press", "Выберите устойчивую опору и держите корпус прямым.", secondary=("triceps", "core")),

    # Back
    _e("neutral_grip_lat_pulldown", "Тяга верхнего блока нейтральным хватом", "back", "cable", ("gym", "functional_gym"), "vertical_pull", "external_load_reps", "vertical_pull", "Ведите локти вниз и не отклоняйтесь назад.", secondary=("biceps",)),
    _e("single_arm_lat_pulldown", "Тяга верхнего блока одной рукой", "back", "cable", ("gym", "functional_gym"), "vertical_pull", "external_load_reps", "vertical_pull", "Не разворачивайте корпус вслед за рукоятью.", secondary=("biceps",)),
    _e("wide_grip_seated_row", "Горизонтальная тяга блока широким хватом", "back", "cable", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Ведите локти в стороны без рывка.", secondary=("shoulders", "biceps")),
    _e("single_arm_cable_row", "Тяга нижнего блока одной рукой", "back", "cable", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Сохраняйте плечи на одном уровне.", secondary=("biceps",)),
    _e("chest_supported_row_machine", "Тяга с упором грудью в тренажёре", "back", "machine", ("gym",), "horizontal_pull", "external_load_reps", "horizontal_row", "Не отрывайте грудь от опоры.", secondary=("biceps",)),
    _e("high_row_machine", "Верхняя тяга в рычажном тренажёре", "back", "machine", ("gym",), "vertical_pull", "external_load_reps", "vertical_pull", "Опускайте плечи и ведите локти к корпусу.", secondary=("biceps",)),
    _e("assisted_pull_up", "Подтягивания в гравитроне", "back", "machine", ("gym",), "vertical_pull", "bodyweight_reps", "vertical_pull", "Начинайте движение сведением лопаток.", secondary=("biceps",)),
    _e("dumbbell_one_arm_row", "Тяга гантели одной рукой к поясу", "back", "dumbbell", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Держите спину нейтральной и не вращайте корпус.", secondary=("biceps",)),
    _e("chest_supported_dumbbell_row", "Тяга гантелей с упором грудью", "back", "dumbbell", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Сохраняйте грудь на скамье и тяните локти назад.", secondary=("biceps",)),
    _e("barbell_bent_over_row", "Тяга штанги в наклоне", "back", "barbell", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "horizontal_row", "Удерживайте нейтральную спину и стабильный наклон.", levels=INTERMEDIATE_PLUS, secondary=("biceps", "hamstrings", "core"), restrictions=("back",)),
    _e("inverted_row", "Горизонтальные подтягивания", "back", "pullup_dip_station", ("gym", "functional_gym", "street"), "horizontal_pull", "bodyweight_reps", "horizontal_row", "Держите тело прямым и тяните грудь к перекладине.", secondary=("biceps", "core")),
    _e("pull_up", "Подтягивания прямым хватом", "back", "pullup_dip_station", ("gym", "functional_gym", "street"), "vertical_pull", "bodyweight_reps", "vertical_pull", "Не раскачивайтесь и начинайте движение лопатками.", levels=INTERMEDIATE_PLUS, secondary=("biceps",)),
    _e("chin_up", "Подтягивания обратным хватом", "back", "pullup_dip_station", ("gym", "functional_gym", "street"), "vertical_pull", "bodyweight_reps", "vertical_pull", "Поднимайтесь без рывка и контролируйте опускание.", levels=INTERMEDIATE_PLUS, secondary=("biceps",)),

    # Quads
    _e("hack_squat", "Приседания в гакк-тренажёре", "quads", "machine", ("gym",), "squat", "external_load_reps", "knee_dominant_squat", "Прижимайте спину к опоре и контролируйте глубину.", secondary=("glutes",)),
    _e("pendulum_squat", "Приседания в маятниковом тренажёре", "quads", "machine", ("gym",), "squat", "external_load_reps", "knee_dominant_squat", "Сохраняйте стопы полностью на платформе.", secondary=("glutes",)),
    _e("leg_extension", "Разгибание ног в тренажёре", "quads", "machine", ("gym",), "isolation", "external_load_reps", "knee_extension", "Разгибайте ноги плавно и не бросайте вес.", restrictions=("knee",)),
    _e("smith_squat", "Приседания в тренажёре Смита", "quads", "smith", ("gym",), "squat", "external_load_reps", "knee_dominant_squat", "Поставьте стопы устойчиво и двигайтесь по направляющей.", secondary=("glutes",)),
    _e("smith_split_squat", "Сплит-присед в тренажёре Смита", "quads", "smith", ("gym",), "squat", "external_load_reps", "split_squat", "Сохраняйте устойчивую стойку и контролируйте колено.", secondary=("glutes",)),
    _e("dumbbell_goblet_squat", "Приседания с гантелью перед грудью", "quads", "dumbbell", ("gym", "functional_gym"), "squat", "external_load_reps", "goblet_squat", "Держите гантель у груди и сохраняйте опору всей стопы.", secondary=("glutes", "core")),
    _e("dumbbell_split_squat", "Сплит-присед с гантелями", "quads", "dumbbell", ("gym", "functional_gym"), "squat", "external_load_reps", "split_squat", "Опускайтесь вертикально в устойчивой стойке.", secondary=("glutes",)),
    _e("barbell_front_squat", "Фронтальные приседания со штангой", "quads", "barbell", ("gym", "functional_gym"), "squat", "external_load_reps", "barbell_squat", "Удерживайте локти высоко и корпус собранным.", levels=INTERMEDIATE_PLUS, secondary=("glutes", "core")),
    _e("bodyweight_squat", "Приседания с собственным весом", "quads", "bodyweight", ("gym", "functional_gym", "street", "home"), "squat", "bodyweight_reps", "bodyweight_squat", "Сохраняйте колени по направлению носков и опору всей стопы.", secondary=("glutes",)),
    _e("reverse_lunge", "Обратные выпады", "quads", "bodyweight", ("gym", "functional_gym", "street", "home"), "lunge", "bodyweight_reps", "lunge", "Шагайте назад и сохраняйте устойчивость передней стопы.", secondary=("glutes",)),
    _e("step_up", "Зашагивания на устойчивую тумбу", "quads", "functional_equipment", ("gym", "functional_gym", "street"), "squat", "external_load_reps", "step_up", "Используйте устойчивую опору и поднимайтесь за счёт рабочей ноги.", secondary=("glutes",)),

    # Hamstrings and glutes
    _e("lying_leg_curl", "Сгибание ног лёжа в тренажёре", "hamstrings", "machine", ("gym",), "isolation", "external_load_reps", "leg_curl", "Не отрывайте таз от опоры."),
    _e("standing_single_leg_curl", "Сгибание одной ноги стоя в тренажёре", "hamstrings", "machine", ("gym",), "isolation", "external_load_reps", "leg_curl", "Сохраняйте таз неподвижным."),
    _e("barbell_romanian_deadlift", "Румынская тяга со штангой", "hamstrings", "barbell", ("gym", "functional_gym"), "hinge", "external_load_reps", "romanian_deadlift", "Отводите таз назад и держите штангу близко к ногам.", levels=INTERMEDIATE_PLUS, secondary=("glutes", "back"), restrictions=("back",)),
    _e("dumbbell_romanian_deadlift", "Румынская тяга с гантелями", "hamstrings", "dumbbell", ("gym", "functional_gym"), "hinge", "external_load_reps", "romanian_deadlift", "Сохраняйте нейтральную спину и отводите таз назад.", secondary=("glutes", "back"), restrictions=("back",)),
    _e("barbell_deadlift", "Классическая становая тяга", "hamstrings", "barbell", ("gym", "functional_gym"), "hinge", "external_load_reps", "floor_deadlift", "Начинайте подъём устойчиво, удерживая гриф близко к ногам.", levels=("advanced",), secondary=("glutes", "back", "quads", "core"), restrictions=("back",)),
    _e("kettlebell_deadlift", "Становая тяга с гирей", "hamstrings", "functional_equipment", ("gym", "functional_gym"), "hinge", "external_load_reps", "floor_deadlift", "Поставьте гирю между стопами и поднимайтесь за счёт ног и таза.", secondary=("glutes", "back")),
    _e("slider_leg_curl", "Сгибание ног со скольжением лёжа", "hamstrings", "functional_equipment", ("functional_gym",), "isolation", "bodyweight_reps", "leg_curl", "Удерживайте таз поднятым и двигайте пятки плавно.", secondary=("glutes", "core")),
    _e("barbell_hip_thrust", "Ягодичный мост со штангой", "glutes", "barbell", ("gym", "functional_gym"), "hinge", "external_load_reps", "hip_thrust", "Зафиксируйте верх спины и завершайте движение сокращением ягодиц.", levels=INTERMEDIATE_PLUS, secondary=("hamstrings",)),
    _e("glute_drive_machine", "Ягодичный мост в тренажёре", "glutes", "machine", ("gym",), "hinge", "external_load_reps", "hip_thrust", "Прижимайте таз к опоре и не переразгибайте поясницу.", secondary=("hamstrings",)),
    _e("smith_hip_thrust", "Ягодичный мост в тренажёре Смита", "glutes", "smith", ("gym",), "hinge", "external_load_reps", "hip_thrust", "Настройте скамью и ограничители перед подходом.", secondary=("hamstrings",)),
    _e("cable_pull_through", "Тяга каната между ног", "glutes", "cable", ("gym", "functional_gym"), "hinge", "external_load_reps", "hip_hinge_extension", "Отводите таз назад и не тяните канат руками.", secondary=("hamstrings",)),
    _e("cable_glute_kickback", "Отведение ноги назад на нижнем блоке", "glutes", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "hip_extension", "Не разворачивайте таз и двигайте ногой подконтрольно."),
    _e("hip_adduction", "Сведение ног в тренажёре", "glutes", "machine", ("gym",), "isolation", "external_load_reps", "hip_adduction", "Сводите ноги плавно без удара плит."),
    _e("bodyweight_glute_bridge", "Ягодичный мост с собственным весом", "glutes", "bodyweight", ("gym", "functional_gym", "street", "home"), "hinge", "bodyweight_reps", "hip_thrust", "Поднимайте таз до нейтрального положения корпуса.", secondary=("hamstrings",)),
    _e("single_leg_glute_bridge", "Ягодичный мост на одной ноге", "glutes", "bodyweight", ("gym", "functional_gym", "street", "home"), "hinge", "bodyweight_reps", "hip_thrust", "Сохраняйте таз ровным на протяжении подхода.", levels=INTERMEDIATE_PLUS, secondary=("hamstrings", "core")),

    # Shoulders
    _e("lateral_raise", "Подъём рук в стороны с гантелями", "shoulders", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "lateral_raise", "Поднимайте руки без рывка до комфортной высоты."),
    _e("dumbbell_shoulder_press", "Жим гантелей сидя", "shoulders", "dumbbell", ("gym", "functional_gym"), "vertical_push", "external_load_reps", "vertical_press", "Сохраняйте корпус устойчивым и не сводите гантели ударом.", secondary=("triceps",)),
    _e("barbell_overhead_press", "Жим штанги стоя", "shoulders", "barbell", ("gym", "functional_gym"), "vertical_push", "external_load_reps", "vertical_press", "Напрягите корпус и проводите гриф близко к лицу.", levels=INTERMEDIATE_PLUS, secondary=("triceps", "core")),
    _e("smith_shoulder_press", "Жим вверх в тренажёре Смита", "shoulders", "smith", ("gym",), "vertical_push", "external_load_reps", "vertical_press", "Настройте скамью и не прогибайтесь в пояснице.", secondary=("triceps",)),
    _e("cable_lateral_raise", "Подъём руки в сторону на нижнем блоке", "shoulders", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "lateral_raise", "Двигайте рукой плавно, удерживая плечо опущенным."),
    _e("lateral_raise_machine", "Подъём рук в стороны в тренажёре", "shoulders", "machine", ("gym",), "isolation", "external_load_reps", "lateral_raise", "Прижимайте корпус к опоре и не поднимайте плечи."),
    _e("reverse_pec_deck", "Обратное сведение рук в pec deck", "shoulders", "machine", ("gym",), "horizontal_pull", "external_load_reps", "rear_delt_fly", "Ведите локти назад без прогиба корпуса.", secondary=("back",)),
    _e("cable_rear_delt_fly", "Разведение рук на блоках для задней дельты", "shoulders", "cable", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "rear_delt_fly", "Сохраняйте небольшой сгиб локтей и не раскачивайтесь.", secondary=("back",)),
    _e("dumbbell_rear_delt_fly", "Разведение гантелей в наклоне", "shoulders", "dumbbell", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "rear_delt_fly", "Удерживайте нейтральную спину и работайте без рывка.", secondary=("back",)),
    _e("cable_face_pull", "Тяга каната к лицу", "shoulders", "cable", ("gym", "functional_gym"), "horizontal_pull", "external_load_reps", "rear_delt_fly", "Тяните канат к уровню глаз и разводите кисти.", secondary=("back",)),

    # Arms
    _e("dumbbell_curl", "Сгибание рук с гантелями стоя", "biceps", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Не уводите локти вперёд и не раскачивайтесь."),
    _e("incline_dumbbell_curl", "Сгибание рук с гантелями на наклонной скамье", "biceps", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Сохраняйте плечи у спинки скамьи."),
    _e("hammer_curl", "Молотковые сгибания с гантелями", "biceps", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Удерживайте нейтральный хват и неподвижные локти."),
    _e("preacher_curl_machine", "Сгибание рук в тренажёре Скотта", "biceps", "machine", ("gym",), "isolation", "external_load_reps", "elbow_flexion", "Не отрывайте плечи от опоры."),
    _e("barbell_curl", "Сгибание рук со штангой стоя", "biceps", "barbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Сохраняйте корпус неподвижным.", levels=INTERMEDIATE_PLUS),
    _e("cable_hammer_curl", "Молотковые сгибания с канатом на блоке", "biceps", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_flexion", "Разведите концы каната в верхней точке."),
    _e("assisted_dip", "Отжимания на брусьях в гравитроне", "triceps", "machine", ("gym",), "vertical_push", "bodyweight_reps", "dip", "Сохраняйте плечи опущенными и контролируйте глубину.", secondary=("chest", "shoulders")),
    _e("cable_overhead_triceps_extension", "Разгибание рук с канатом из-за головы", "triceps", "cable", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_extension", "Держите локти направленными вперёд."),
    _e("dumbbell_overhead_triceps_extension", "Разгибание рук с гантелью из-за головы", "triceps", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_extension", "Не разводите локти и удерживайте корпус устойчивым."),
    _e("dumbbell_skull_crusher", "Разгибание рук с гантелями лёжа", "triceps", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "elbow_extension", "Опускайте гантели плавно по сторонам головы."),
    _e("close_grip_bench_press", "Жим штанги лёжа узким хватом", "triceps", "barbell", ("gym", "functional_gym"), "horizontal_push", "external_load_reps", "triceps_press", "Сохраняйте предплечья вертикальными и контролируйте гриф.", levels=INTERMEDIATE_PLUS, secondary=("chest", "shoulders")),
    _e("bench_dip", "Обратные отжимания от скамьи", "triceps", "bodyweight", ("gym", "functional_gym", "street"), "vertical_push", "bodyweight_reps", "dip", "Используйте устойчивую опору и не опускайтесь глубже комфортного.", secondary=("chest", "shoulders"), restrictions=("shoulder",)),
    _e("parallel_bar_dip", "Отжимания на параллельных брусьях", "triceps", "pullup_dip_station", ("gym", "functional_gym", "street"), "vertical_push", "bodyweight_reps", "dip", "Опускайтесь подконтрольно и не проваливайтесь в плечах.", levels=INTERMEDIATE_PLUS, secondary=("chest", "shoulders")),

    # Calves and core
    _e("seated_calf_raise", "Подъём на носки сидя в тренажёре", "calves", "machine", ("gym",), "isolation", "external_load_reps", "calf_raise", "Сделайте короткую паузу в верхней точке."),
    _e("leg_press_calf_raise", "Подъём на носки в тренажёре для жима ногами", "calves", "machine", ("gym",), "isolation", "external_load_reps", "calf_raise", "Двигайте только голеностопом и контролируйте платформу."),
    _e("single_leg_calf_raise", "Подъём на носок одной ногой", "calves", "bodyweight", ("gym", "functional_gym", "street", "home"), "isolation", "bodyweight_reps", "calf_raise", "Используйте опору для равновесия и полную комфортную амплитуду."),
    _e("dumbbell_calf_raise", "Подъём на носки с гантелями", "calves", "dumbbell", ("gym", "functional_gym"), "isolation", "external_load_reps", "calf_raise", "Не торопитесь в нижней точке."),
    _e("plank", "Планка на предплечьях", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "core", "timed_conditioning", "anti_extension_core", "Держите корпус прямым и не задерживайте дыхание."),
    _e("side_plank", "Боковая планка", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "core", "timed_conditioning", "lateral_core", "Удерживайте таз на одной линии с корпусом."),
    _e("dead_bug", "Поочерёдное опускание руки и ноги лёжа", "core", "bodyweight", ("gym", "functional_gym", "home"), "core", "bodyweight_reps", "anti_extension_core", "Прижимайте поясницу к полу и двигайтесь медленно."),
    _e("bird_dog", "Вытягивание противоположных руки и ноги на четвереньках", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "core", "bodyweight_reps", "spinal_stability", "Не разворачивайте таз и удерживайте равновесие."),
    _e("hanging_knee_raise", "Подъём коленей в висе", "core", "pullup_dip_station", ("gym", "functional_gym", "street"), "core", "bodyweight_reps", "trunk_flexion", "Не раскачивайтесь и подкручивайте таз в верхней точке.", levels=INTERMEDIATE_PLUS),
    _e("captains_chair_knee_raise", "Подъём коленей в упоре на локтях", "core", "pullup_dip_station", ("gym", "street"), "core", "bodyweight_reps", "trunk_flexion", "Прижимайте спину к опоре и не раскачивайтесь."),
    _e("pallof_press", "Жим Паллафа в кроссовере", "core", "cable", ("gym", "functional_gym"), "core", "external_load_reps", "anti_rotation_core", "Не позволяйте блоку разворачивать корпус."),
    _e("ab_wheel_rollout", "Выкатывание ролика с колен", "core", "functional_equipment", ("gym", "functional_gym"), "core", "bodyweight_reps", "anti_extension_core", "Двигайтесь только в амплитуде, где сохраняете нейтральную поясницу.", levels=INTERMEDIATE_PLUS, restrictions=("back",)),
    _e("russian_twist", "Повороты корпуса сидя", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "core", "bodyweight_reps", "rotation_core", "Поворачивайте грудную клетку плавно, не дёргая шею."),

    # Functional movements for later AMRAP/EMOM/For Time support.
    _e("kettlebell_swing", "Махи гирей", "glutes", "functional_equipment", ("gym", "functional_gym"), "hinge", "timed_conditioning", "ballistic_hinge", "Разгоняйте гирю движением таза, а не подъёмом рук.", levels=INTERMEDIATE_PLUS, secondary=("hamstrings", "core")),
    _e("kettlebell_goblet_squat", "Приседания с гирей перед грудью", "quads", "functional_equipment", ("gym", "functional_gym"), "squat", "external_load_reps", "goblet_squat", "Держите гирю близко к груди и сохраняйте устойчивые стопы.", secondary=("glutes", "core")),
    _e("kettlebell_clean", "Взятие гири на грудь", "glutes", "functional_equipment", ("gym", "functional_gym"), "hinge", "external_load_reps", "kettlebell_clean", "Ведите гирю близко к телу и мягко принимайте её на предплечье.", levels=("advanced",), secondary=("hamstrings", "shoulders", "core")),
    _e("kettlebell_push_press", "Швунг гири от плеча", "shoulders", "functional_equipment", ("gym", "functional_gym"), "vertical_push", "external_load_reps", "push_press", "Помогайте ногами и фиксируйте гирю над плечом.", levels=INTERMEDIATE_PLUS, secondary=("triceps", "quads", "core")),
    _e("medicine_ball_slam", "Броски медбола в пол", "core", "functional_equipment", ("gym", "functional_gym"), "locomotion_conditioning", "timed_conditioning", "ball_slam", "Поднимайте мяч устойчиво и бросайте перед собой, сохраняя пространство вокруг.", secondary=("shoulders", "back")),
    _e("farmers_carry", "Прогулка фермера", "core", "functional_equipment", ("gym", "functional_gym", "street"), "carry", "distance_other", "loaded_carry", "Идите короткими устойчивыми шагами с ровным корпусом.", secondary=("shoulders", "back", "calves")),
    _e("sled_push", "Толкание тренировочных саней", "quads", "functional_equipment", ("gym", "functional_gym", "street"), "locomotion_conditioning", "distance_other", "sled", "Держите корпус собранным и толкайте с ровным темпом.", secondary=("glutes", "calves", "core")),
    _e("battle_rope_waves", "Попеременные волны канатами", "shoulders", "functional_equipment", ("gym", "functional_gym", "street"), "locomotion_conditioning", "timed_conditioning", "battle_rope", "Сохраняйте устойчивую стойку и ровное дыхание.", secondary=("core",)),
    _e("box_step_over", "Перешагивания через тумбу", "quads", "functional_equipment", ("gym", "functional_gym", "street"), "locomotion_conditioning", "timed_conditioning", "box_step", "Используйте устойчивую тумбу и полностью ставьте стопу на поверхность.", secondary=("glutes", "calves")),
    _e("bear_crawl", "Медвежья ходьба", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "locomotion_conditioning", "distance_other", "ground_locomotion", "Двигайтесь небольшими шагами, удерживая таз на одном уровне.", secondary=("shoulders", "quads")),
    _e("mountain_climber", "Бег в упоре с подтягиванием коленей", "core", "bodyweight", ("gym", "functional_gym", "street", "home"), "locomotion_conditioning", "timed_conditioning", "ground_conditioning", "Сохраняйте плечи над ладонями и не поднимайте таз."),
    _e("prone_y_raise", "Y-подъёмы лёжа на животе", "shoulders", "bodyweight", ("gym", "functional_gym", "street", "home"), "scapular_rear_delt", "bodyweight_reps", "prone_upper_back", "Лягте на живот и плавно сводите лопатки, не поднимая плечи к ушам.", secondary=("back",)),
    _e("prone_reverse_snow_angel", "Разведения рук лёжа на животе («обратные снежные ангелы»)", "shoulders", "bodyweight", ("gym", "functional_gym", "street", "home"), "scapular_rear_delt", "bodyweight_reps", "prone_upper_back_vertical", "Лягте на живот и плавно проведите прямыми руками вдоль корпуса, сводя лопатки.", secondary=("back",)),
)


LEGACY_EXERCISE_CODES = tuple(
    definition.code for definition in EXERCISE_DEFINITIONS[:15]
)

# Stage 2 consumers keep their explicit, product-approved alternatives. Stage
# 7G can query equivalence_group across the full catalog without rewriting this
# backward-compatible mapping.
EXERCISE_ALTERNATIVES = {
    "barbell_bench_press": ("dumbbell_bench_press", "chest_press"),
    "barbell_back_squat": ("leg_press",),
}

# This is deliberately separate from replacement alternatives: advancing a
# bodyweight variation is only allowed when the controlled catalog names one
# unambiguous successor. Missing metadata means a safe hold at the upper range.
BODYWEIGHT_PROGRESSION_SUCCESSORS = {
    "incline_push_up": "push_up",
    "bodyweight_squat": "reverse_lunge",
}


def exercise_definition_by_code(code: str) -> ExerciseDefinition | None:
    """Return one controlled definition without introducing a DB dependency."""
    return next((item for item in EXERCISE_DEFINITIONS if item.code == code), None)


def validate_exercise_definition(definition: ExerciseDefinition) -> None:
    """Raise ValueError when controlled taxonomy is internally inconsistent."""
    if definition.primary_muscle_group not in MUSCLE_GROUP_LABELS:
        raise ValueError(f"Invalid muscle group: {definition.code}")
    if definition.equipment not in EQUIPMENT_CATEGORIES:
        raise ValueError(f"Invalid equipment: {definition.code}")
    if not definition.environments or not set(definition.environments) <= TRAINING_ENVIRONMENTS:
        raise ValueError(f"Invalid environments: {definition.code}")
    if not definition.experience_levels or not set(definition.experience_levels) <= EXPERIENCE_LEVELS:
        raise ValueError(f"Invalid experience levels: {definition.code}")
    if definition.movement_pattern not in MOVEMENT_PATTERNS:
        raise ValueError(f"Invalid movement pattern: {definition.code}")
    if definition.progression_type not in PROGRESSION_TYPES:
        raise ValueError(f"Invalid progression type: {definition.code}")
    if not definition.equivalence_group:
        raise ValueError(f"Missing equivalence group: {definition.code}")
    if not set(definition.secondary_muscle_groups) <= set(MUSCLE_GROUP_LABELS):
        raise ValueError(f"Invalid secondary muscle group: {definition.code}")
    if definition.primary_muscle_group in definition.secondary_muscle_groups:
        raise ValueError(f"Primary muscle duplicated as secondary: {definition.code}")
