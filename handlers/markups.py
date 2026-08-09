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

def start_mkp():
    btns = [
        [
            types.InlineKeyboardButton(text="Профиль", callback_data="profile")
        ],
    ]
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
            types.InlineKeyboardButton(text="Связаться с разработчиками", callback_data="contact_with_devs")
        ],
        [
            types.InlineKeyboardButton(text="Вернуться в меню", callback_data="start")
        ],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=btns)


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
