from aiogram import types

# def example_mkp():
#     btns = [
#         [
#             types.InlineKeyboardButton(text="Ряд 1, Кнопка 1", callback_data="callback_1_1"),
#             types.InlineKeyboardButton(text='Ряд 1, Кнопка 2', callback_data="callback_1_2")
#         ],
#         [
#             types.InlineKeyboardButton(text="Ряд 2, Кнопка 1", callback_data="callback_2_1"),
#             types.InlineKeyboardButton(text='Ряд 2, Кнопка 2', callback_data="callback_2_2")
#         ],
#     ]
#     return types.InlineKeyboardMarkup(inline_keyboard=btns)

def start_mkp(
    workout_action: tuple[str, str] | None = None,
    *,
    show_history: bool = False,
):
    btns = [
        [
            types.InlineKeyboardButton(
                text="Мой план", callback_data="workout_plan"
            )
        ],
    ]
    if workout_action is not None:
        text, callback_data = workout_action
        btns.append(
            [types.InlineKeyboardButton(text=text, callback_data=callback_data)]
        )
    if show_history:
        btns.append(
            [
                types.InlineKeyboardButton(
                    text="📊 История тренировок",
                    callback_data="workout:history",
                )
            ]
        )
    btns.append(
        [
            types.InlineKeyboardButton(
                text="💳 Подписка", callback_data="subscription"
            )
        ]
    )
    btns.append(
        [types.InlineKeyboardButton(text="Профиль", callback_data="profile")]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=btns)


def subscription_mkp(
    *,
    payment_id: int | None = None,
    confirmation_url: str | None = None,
    show_pay: bool = True,
) -> types.InlineKeyboardMarkup:
    buttons = []
    if isinstance(confirmation_url, str) and confirmation_url.startswith("https://"):
        buttons.append(
            [types.InlineKeyboardButton(text="Оплатить в YooKassa", url=confirmation_url)]
        )
    if show_pay:
        buttons.append(
            [types.InlineKeyboardButton(text="Оплатить", callback_data="subscription:pay")]
        )
    if isinstance(payment_id, int) and payment_id > 0:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text="Проверить оплату",
                    callback_data=f"subscription:check:{payment_id}",
                )
            ]
        )
    buttons.append(
        [types.InlineKeyboardButton(text="🏠 В меню", callback_data="start")]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

def to_menu_mpk():
    btns = [
        [
            types.InlineKeyboardButton(text="Вернуться в меню", callback_data="start")
        ],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=btns)

def cancel_mpk():
    btns = [
        [types.InlineKeyboardButton(text="Отменить", callback_data="start")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=btns)

def profile_mpk():
    btns = [
        [
            types.InlineKeyboardButton(
                text="Изменить профиль", callback_data="profile:edit"
            )
        ],
        [
            types.InlineKeyboardButton(text="Связаться с разработчиками", callback_data="contact_with_devs")
        ],
        [
            types.InlineKeyboardButton(text="Вернуться в меню", callback_data="start")
        ],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=btns)


def workout_plan_mkp(
    workout_action: tuple[str, str] | None = None,
) -> types.InlineKeyboardMarkup:
    buttons = []
    if workout_action is not None:
        text, callback_data = workout_action
        buttons.append(
            [types.InlineKeyboardButton(text=text, callback_data=callback_data)]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="Вернуться в меню",
                callback_data="start",
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def workout_plan_source_mkp():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="🤖 Составить программу", callback_data="workout_plan:generate"
        )],
        [types.InlineKeyboardButton(
            text="📝 У меня есть своя программа", callback_data="user_program:start"
        )],
        [types.InlineKeyboardButton(text="Вернуться в меню", callback_data="start")],
    ])


def user_program_mode_mkp():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="Следовать строго", callback_data="user_program:mode:strict"
        )],
        [types.InlineKeyboardButton(
            text="Разрешать замены", callback_data="user_program:mode:replacements"
        )],
        [types.InlineKeyboardButton(
            text="Адаптировать по прогрессу", callback_data="user_program:mode:adaptive"
        )],
        [types.InlineKeyboardButton(text="Отмена", callback_data="workout_plan")],
    ])


def user_program_preview_mkp():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="✅ Всё верно", callback_data="user_program:confirm"
        )],
        [types.InlineKeyboardButton(
            text="✏️ Ввести заново", callback_data="user_program:retry"
        )],
        [types.InlineKeyboardButton(text="Отмена", callback_data="workout_plan")],
    ])


def workout_current_mkp(
    *,
    ready_to_complete: bool = False,
    show_replacement: bool = False,
):
    buttons = []
    if ready_to_complete:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text="🏁 Завершить тренировку",
                    callback_data="workout:complete",
                )
            ]
        )
    else:
        buttons.append(
            [types.InlineKeyboardButton(
                text="ℹ️ Техника",
                callback_data="workout:technique",
            )]
        )
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text="✅ Выполнить подход",
                    callback_data="workout:record_set",
                )
            ]
        )
        if show_replacement:
            buttons.append(
                [
                    types.InlineKeyboardButton(
                        text="🔄 Заменить упражнение",
                        callback_data="workout:replace:current",
                    )
                ]
            )
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="❌ Отменить тренировку",
                callback_data="workout:cancel",
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def workout_technique_mkp():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="⬅️ Вернуться к упражнению",
            callback_data="workout:technique:back",
        )],
        [types.InlineKeyboardButton(
            text="❌ Отменить тренировку",
            callback_data="workout:cancel",
        )],
    ])


def workout_format_mkp(state, *, show_replacements: bool = False):
    """Render only durable format actions; callback data contains no user data."""
    buttons = []
    if state.started_at is None:
        buttons.append([types.InlineKeyboardButton(
            text="▶️ Начать блок", callback_data=f"workout:format:start:{state.block_id}"
        )])
        if show_replacements:
            for exercise in state.exercises:
                if (
                    exercise.planned_exercise_id is not None
                    and exercise.planned_exercise_id == exercise.selected_exercise_id
                ):
                    buttons.append([types.InlineKeyboardButton(
                        text=f"🔄 Заменить: {exercise.name}",
                        callback_data=f"workout:replace:{exercise.exercise_id}",
                    )])
    elif state.finished_at is None:
        if state.workout_format.value == "emom":
            minute = state.current_minute or 1
            buttons.append([
                types.InlineKeyboardButton(
                    text="✅ Минута выполнена",
                    callback_data=f"workout:format:emom:{state.block_id}:{minute}:1",
                ),
                types.InlineKeyboardButton(
                    text="Пропуск",
                    callback_data=f"workout:format:emom:{state.block_id}:{minute}:0",
                ),
            ])
        else:
            buttons.append([types.InlineKeyboardButton(
                text="✅ Круг завершён",
                callback_data=(
                    f"workout:format:round:{state.block_id}:{state.completed_rounds}"
                ),
            )])
        buttons.append([types.InlineKeyboardButton(
            text="🏁 Закончить блок", callback_data=f"workout:format:finish:{state.block_id}"
        )])
    buttons.append([types.InlineKeyboardButton(
        text="❌ Отменить тренировку", callback_data="workout:cancel"
    )])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def workout_replacement_mkp(options):
    """Render only server-selected, local snapshot/exercise identifiers."""
    buttons = [
        [
            types.InlineKeyboardButton(
                text=f"🔄 {candidate.name}",
                callback_data=(
                    "workout:replace:choose:"
                    f"{options.session_exercise_id}:{candidate.exercise_id}"
                ),
            )
        ]
        for candidate in options.candidates
    ]
    buttons.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Оставить текущее",
                callback_data="workout:replace:cancel",
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def workout_input_mkp():
    """Keep only the safe cancellation action while text input is pending."""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="❌ Отменить тренировку",
                    callback_data="workout:cancel",
                )
            ]
        ]
    )


def workout_cancel_confirmation_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Да, отменить",
                    callback_data="workout:cancel:confirm",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Продолжить тренировку",
                    callback_data="workout:cancel:resume",
                )
            ],
        ]
    )


def workout_history_mkp(
    workout_buttons: list[tuple[str, int]],
    *,
    offset: int,
    page_size: int,
    has_newer: bool,
    has_older: bool,
):
    buttons = [
        [
            types.InlineKeyboardButton(
                text=text,
                callback_data=f"workout:history:detail:{workout_id}:{offset}",
            )
        ]
        for text, workout_id in workout_buttons
    ]
    navigation = []
    if has_newer:
        navigation.append(
            types.InlineKeyboardButton(
                text="◀️ Новее",
                callback_data=f"workout:history:page:{max(0, offset - page_size)}",
            )
        )
    if has_older:
        navigation.append(
            types.InlineKeyboardButton(
                text="Старше ▶️",
                callback_data=f"workout:history:page:{offset + page_size}",
            )
        )
    if navigation:
        buttons.append(navigation)
    buttons.append(
        [types.InlineKeyboardButton(text="🏠 В меню", callback_data="start")]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def workout_history_detail_mkp(offset: int):
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="← К истории",
                    callback_data=f"workout:history:page:{offset}",
                )
            ],
            [types.InlineKeyboardButton(text="🏠 В меню", callback_data="start")],
        ]
    )


def onboarding_sex_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Мужской", callback_data="onboarding:sex:male"
                ),
                types.InlineKeyboardButton(
                    text="Женский", callback_data="onboarding:sex:female"
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="Не указывать",
                    callback_data="onboarding:sex:not_specified",
                )
            ],
        ]
    )


def onboarding_goal_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Набрать мышечную массу",
                    callback_data="onboarding:goal:muscle_gain",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Стать сильнее",
                    callback_data="onboarding:goal:strength",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Снизить процент жира",
                    callback_data="onboarding:goal:fat_loss",
                )
            ],
        ]
    )


def onboarding_experience_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Новичок",
                    callback_data="onboarding:experience:beginner",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Средний",
                    callback_data="onboarding:experience:intermediate",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Продвинутый",
                    callback_data="onboarding:experience:advanced",
                )
            ],
        ]
    )


def onboarding_environment_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text="Тренажёрный зал",
                callback_data="onboarding:environment:gym",
            )],
            [types.InlineKeyboardButton(
                text="Функциональный зал",
                callback_data="onboarding:environment:functional_gym",
            )],
            [types.InlineKeyboardButton(
                text="Улица",
                callback_data="onboarding:environment:street",
            )],
            [types.InlineKeyboardButton(
                text="Дом",
                callback_data="onboarding:environment:home",
            )],
        ]
    )


def onboarding_frequency_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=str(value),
                    callback_data=f"onboarding:frequency:{value}",
                )
                for value in (2, 3, 4)
            ],
            [
                types.InlineKeyboardButton(
                    text=str(value),
                    callback_data=f"onboarding:frequency:{value}",
                )
                for value in (5, 6)
            ],
        ]
    )


def onboarding_duration_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"{value} мин",
                    callback_data=f"onboarding:duration:{value}",
                )
                for value in (30, 45)
            ],
            [
                types.InlineKeyboardButton(
                    text=f"{value} мин",
                    callback_data=f"onboarding:duration:{value}",
                )
                for value in (60, 90)
            ],
        ]
    )


def onboarding_confirmation_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Подтвердить", callback_data="onboarding:confirm"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Заполнить заново", callback_data="onboarding:restart"
                )
            ],
        ]
    )


def onboarding_limitations_mkp():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Нет ограничений",
                    callback_data="onboarding:limitations:none",
                )
            ]
        ]
    )
