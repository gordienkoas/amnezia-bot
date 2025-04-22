from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher.filters.state import State, StatesGroup
import subprocess
from loader import dp, bot  # Убедись, что loader.py существует

class AddClient(StatesGroup):
    protocol = State()
    username = State()

@dp.message_handler(commands=["add_client"])
async def cmd_add_client(message: types.Message):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("WireGuard", callback_data="proto_wg"),
        InlineKeyboardButton("XRay", callback_data="proto_xray")
    )
    await message.answer("Выберите протокол для нового клиента:", reply_markup=keyboard)
    await AddClient.protocol.set()

@dp.callback_query_handler(lambda c: c.data.startswith("proto_"), state=AddClient.protocol)
async def process_protocol_choice(callback_query: types.CallbackQuery, state: FSMContext):
    protocol = callback_query.data.replace("proto_", "")
    await state.update_data(protocol=protocol)
    await bot.send_message(callback_query.from_user.id, f"Выбран протокол: {protocol.upper()}\nВведите имя клиента:")
    await AddClient.username.set()

@dp.message_handler(state=AddClient.username)
async def process_username(message: types.Message, state: FSMContext):
    data = await state.get_data()
    username = message.text.strip()
    protocol = data.get("protocol", "wg")

    try:
        subprocess.run(
            ["amnezia", "client", "add", "--proto", protocol, "--name", username],
            check=True
        )
        await message.answer(f"✅ Клиент `{username}` с протоколом `{protocol.upper()}` успешно создан.", parse_mode="Markdown")
    except subprocess.CalledProcessError as e:
        await message.answer(f"❌ Ошибка при создании клиента:\n```\n{str(e)}\n```", parse_mode="Markdown")

    await state.finish()
