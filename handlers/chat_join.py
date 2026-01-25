from aiogram import types
from storage.config import dp, bot

@dp.chat_join_request()
async def join_request(update: types.ChatJoinRequest):
    user_id=update.from_user.id
    await update.approve() #.decline() если отклоняем
    await bot.send_message(user_id, "Спасибо за присоединение к каналу!")