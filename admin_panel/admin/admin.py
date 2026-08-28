import datetime
import os
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from storage.config import dp, systemdServiceName
from db import User

from admin_panel.authorization import require_admin_callback, require_admin_message

from .markups import *
from .states import tryFinish


@dp.message(Command('admin'))
@require_admin_message
async def adminCmd(message: types.Message, state: FSMContext):
    await tryFinish(state)
    allUsers = User.all()
    await message.answer(f"Админ-панель\n\nСтатистика:\nЮзеров - {len(allUsers)}", reply_markup=admin_mpk())

@dp.callback_query(F.data == "admin")
@require_admin_callback
async def adminCall(call: types.CallbackQuery, state: FSMContext):
    await tryFinish(state)
    allUsers = User.all()
    await call.message.edit_text(f"Админ-панель\n\nСтатистика:\nЮзеров - {len(allUsers)}", reply_markup=admin_mpk())

@dp.callback_query(F.data.contains("all_users_"))
@require_admin_callback
async def adminUsersMenuCall(call: types.CallbackQuery, state: FSMContext):
    pageNum = int(call.data.split("_")[2])
    allUsers = User.all()
    currentUsersList = allUsers[((pageNum - 1) * 5):(pageNum * 5)]
    await call.message.edit_text(f"Все юзеры (страница №{pageNum})\nЮзеров всего - {len(allUsers)}, текущая страница - юзеры {((pageNum - 1) * 5) + 1} - {(pageNum * 5)}", reply_markup=all_users_admin_mpk(pageNum, len(allUsers), currentUsersList))

@dp.callback_query(F.data.contains("checkuser_"))
@require_admin_callback
async def adminUserMenuCall(call: types.CallbackQuery, state: FSMContext):
    user_id, pageNum = int(call.data.split("_")[1]), int(call.data.split("_")[2])
    user = User.get(tg_id=user_id)
    regDatetime = (user.reg_datetime + datetime.timedelta(hours=3)).strftime("%d/%m/%Y, %H:%M:%S")
    await call.message.edit_text(f"Юзер {user_id}\n{user.fullname} (@{user.username})\nДата регистрации - {regDatetime}", reply_markup=user_admin_mpk(pageNum))

@dp.callback_query(F.data == "restart_bot")
@require_admin_callback
async def restartBotCall(call: types.CallbackQuery, state: FSMContext):
    await tryFinish(state)
    await call.message.answer("Перезапуск бота...")
    os.system(f'sudo systemctl restart {systemdServiceName}')
