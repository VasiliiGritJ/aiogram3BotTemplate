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
        [types.InlineKeyboardButton(text="Профиль", callback_data="profile")]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=btns)

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


def workout_current_mkp(*, ready_to_complete: bool = False):
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
            [
                types.InlineKeyboardButton(
                    text="✅ Выполнить подход",
                    callback_data="workout:record_set",
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
                    text="Набор мышечной массы / веса",
                    callback_data="onboarding:goal:muscle_gain",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Снижение веса / жира",
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
                    text="Есть небольшой опыт",
                    callback_data="onboarding:experience:some_experience",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Опытный",
                    callback_data="onboarding:experience:experienced",
                )
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
