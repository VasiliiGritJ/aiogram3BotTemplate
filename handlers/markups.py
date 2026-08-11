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
