from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandObject, CommandStart
from aiogram.utils.chat_action import ChatActionSender
from aiogram import F, types

from db import User
from handlers.markups import start_mkp
from handlers.onboarding import start_onboarding
from services.onboarding import has_completed_profile
from storage.config import bot, dp
from storage.states import tryFinish
import utils.custom_logger as cl
from utils.scripts import get_refer_id


@dp.message(CommandStart())
async def startcmd(
    message: types.Message,
    command: CommandObject,
    state: FSMContext,
):
    async with ChatActionSender.typing(bot=bot, chat_id=message.from_user.id):
        referal_inviter_tg_id = get_refer_id(command.args)
        cl.log("Start Handler", "info", f"Registation (ref - {referal_inviter_tg_id})", message.from_user.id)
        fullname = f"{message.from_user.first_name}{' ' + message.from_user.last_name if message.from_user.last_name else ''}"
        username = message.from_user.username or ""
        user = User.get_or_create(
            message.from_user.id,
            fullname=fullname,
            username=username,
            inviter_id=referal_inviter_tg_id if referal_inviter_tg_id else 0,
        )
    if has_completed_profile(user.id):
        await tryFinish(state)
        await message.answer("Меню", reply_markup=start_mkp())
    else:
        await start_onboarding(message, state)

# @dp.callback_query(F.data == "start", State.State)
@dp.callback_query(F.data == "start")
async def startCall(call: types.CallbackQuery, state: FSMContext):
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await tryFinish(state)
        await call.message.edit_text("Отправьте /start, чтобы начать.")
    elif has_completed_profile(user.id):
        await tryFinish(state)
        await call.message.edit_text("Меню", reply_markup=start_mkp())
    else:
        await start_onboarding(call.message, state, edit=True)
    await call.answer()
