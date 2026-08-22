import asyncio
import random
import sqlite3
import time
import difflib
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ChatPermissions, ChatMemberUpdated
)

# --- Конфигурация ---
TOKEN = "8655620590:AAERDoqUPU-jeo-DMRbRTGf4p6-iUUr4stg"
GROUP_CHAT_ID = -1004493287292
OWNER_IDS = [7545129896, 8184136446]
groza_chat_id = -1003843695003

COMMAND_PREFIXES = ("!", ".", "/", "?")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Глобальные переменные
bot_enabled = True
active_event = None
roulette_chance = None
roulette_message_count = 0
perebiv_minutes = None
game_task = None
game_message = None
game_leader = None
game_end_time = None

EVENT_TITLES = {
    "roulette": "🎲 Legal Roulette",
    "perebiv": "⚡ Перебив",
}

class EventSetup(StatesGroup):
    waiting_roulette_chance = State()
    waiting_perebiv_minutes = State()

# --- База данных ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, last_seen TIMESTAMP)')
    cur.execute('CREATE TABLE IF NOT EXISTS wins (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, win_time TIMESTAMP, prize TEXT DEFAULT "Победа в игре")')
    conn.commit()
    conn.close()

def get_user_wins(user_id):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('SELECT win_time, prize FROM wins WHERE user_id = ? ORDER BY win_time DESC', (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def add_win(user_id, prize='Победа в игре'):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO wins (user_id, win_time, prize) VALUES (?, ?, ?)', (user_id, datetime.now(), prize))
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательные функции ---
def format_user_link(user_id, username, first_name):
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">{first_name}</a>'

def format_datetime_moscow(dt):
    msk_dt = dt + timedelta(hours=3)
    return msk_dt.strftime("%Y-%m-%d %H:%M MSK")

def get_winners_list():
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('SELECT wins.user_id, wins.win_time, users.username, users.first_name FROM wins LEFT JOIN users ON wins.user_id = users.user_id ORDER BY wins.win_time DESC')
    rows = cur.fetchall()
    conn.close()
    return rows

async def is_chat_admin(chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('administrator', 'creator')
    except Exception:
        return False

# --- Команды ---
HELP_TEXT = (
    "Я #нетпуза, вот мои команды:\n\n"
    ".тише - мут на 1 минуту\n"
    ".команды/.командс/.инфо/.инфа - список команд\n"
    ".пинг - пинг бота\n"
    ".чек/.check - проверка файла\n"
)

COMMAND_ALIASES = {
    "тише": "mute",
    "помолчи": "mute",
    "успокойся": "mute",
    "команды": "help",
    "командс": "help",
    "инфа": "help",
    "инфо": "help",
    "help": "help",
    "пинг": "ping",
    "ping": "ping",
    "чек": "check",
    "check": "check",
    "файл": "check",
    "mute": "mute",
}

def resolve_command(text: str):
    if not text:
        return None
    if text[0] not in COMMAND_PREFIXES:
        return None
    rest = text[1:].strip()
    if not rest:
        return None
    first_word = rest.split(maxsplit=1)[0].lower().strip(".,!?")
    if not first_word:
        return None
    if first_word in COMMAND_ALIASES:
        return COMMAND_ALIASES[first_word]
    return None

# --- Клавиатуры ---
def main_menu_keyboard(enabled):
    status = "Включен" if enabled else "Выключен"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Переключатель", callback_data="toggle_menu")],
        [InlineKeyboardButton(text="🏆 Победители", callback_data="winners")],
        [InlineKeyboardButton(text="🎉 Ивенты", callback_data="events_menu")],
    ])
    return keyboard, status

def events_menu_keyboard():
    rows = []
    if active_event == "roulette":
        rows.append([InlineKeyboardButton(text="⏹ Завершить Legal Roulette", callback_data="stop_roulette")])
    elif active_event is None:
        rows.append([InlineKeyboardButton(text="🎲 Legal Roulette", callback_data="start_roulette")])
    if active_event == "perebiv":
        rows.append([InlineKeyboardButton(text="⏹ Завершить Перебив", callback_data="stop_perebiv")])
    elif active_event is None:
        rows.append([InlineKeyboardButton(text="⚡ Перебив", callback_data="start_perebiv")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cancel_setup_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_event_setup")]
    ])
    # --- Команда /start (ЛС) ---
@dp.message(Command("start"), F.chat.type == "private")
async def start_command(message: types.Message):
    user_id = message.from_user.id
    if user_id in OWNER_IDS:
        keyboard, status = main_menu_keyboard(bot_enabled)
        await message.answer(f"Добро пожаловать назад, админ панель:\nРежим: {status}", reply_markup=keyboard)
        return
    rows = get_user_wins(user_id)
    if not rows:
        await message.answer("Привет! 👋\nПобед пока нет.")
        return
    lines = []
    for idx, (win_time, prize) in enumerate(rows, 1):
        time_str = format_datetime_moscow(datetime.fromisoformat(win_time))
        lines.append(f"{idx}. {prize} – {time_str}")
    text = f"🏆 Твои победы (всего {len(rows)}):\n\n" + "\n".join(lines)
    await message.answer(text)

# --- Команда /free ---
@dp.message(Command("free"), F.chat.type.in_({"group", "supergroup"}))
async def free_command(message: types.Message):
    if not bot_enabled:
        return
    await message.reply("✅ Команда /free работает")

# --- Приветствие в чате "Гроза" ---
@dp.my_chat_member(F.chat.id == groza_chat_id)
async def bot_added_to_groza(event: ChatMemberUpdated):
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        await bot.send_message(event.chat.id, "Приветствую, господа!")

# --- Callback-обработчики ---
@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    keyboard, status = main_menu_keyboard(bot_enabled)
    await callback.message.edit_text(f"Админ панель:\nРежим: {status}", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "toggle_menu")
async def toggle_menu_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Turn ON", callback_data="turn_on"),
         InlineKeyboardButton(text="🔴 Turn OFF", callback_data="turn_off")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text("Выберите действие:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "turn_on")
async def turn_on_callback(callback: CallbackQuery):
    global bot_enabled
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    bot_enabled = True
    keyboard, status = main_menu_keyboard(bot_enabled)
    await callback.message.edit_text(f"Админ панель:\nРежим: {status}", reply_markup=keyboard)
    await callback.answer("Режим включён ✅")

@dp.callback_query(F.data == "turn_off")
async def turn_off_callback(callback: CallbackQuery):
    global bot_enabled
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    bot_enabled = False
    keyboard, status = main_menu_keyboard(bot_enabled)
    await callback.message.edit_text(f"Админ панель:\nРежим: {status}", reply_markup=keyboard)
    await callback.answer("Режим выключен ❌")

@dp.callback_query(F.data == "winners")
async def winners_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = get_winners_list()
    if not rows:
        text = "🏆 Победителей пока нет."
    else:
        lines = []
        for idx, (user_id, win_time, username, first_name) in enumerate(rows, 1):
            link = format_user_link(user_id, username, first_name)
            time_str = format_datetime_moscow(datetime.fromisoformat(win_time))
            lines.append(f"{idx}. {link} (ID: {user_id}) – {time_str}")
        text = f"🏆 Список победителей:\n\n" + "\n".join(lines)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "events_menu")
async def events_menu_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    if active_event == "roulette":
        text = f"🎉 Активный ивент: {EVENT_TITLES['roulette']}\nШанс: {roulette_chance}%"
    elif active_event == "perebiv":
        text = f"🎉 Активный ивент: {EVENT_TITLES['perebiv']}\nВремя: {perebiv_minutes} мин."
    else:
        text = "🎉 Активных ивентов нет."
    await callback.message.edit_text(text, reply_markup=events_menu_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "start_roulette")
async def start_roulette_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(EventSetup.waiting_roulette_chance)
    await callback.message.edit_text("🎲 Введите шанс победы в % (например: 5):", reply_markup=cancel_setup_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "start_perebiv")
async def start_perebiv_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(EventSetup.waiting_perebiv_minutes)
    await callback.message.edit_text("⚡ Сколько минут держать лидерство? (1-30):", reply_markup=cancel_setup_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "cancel_event_setup")
async def cancel_event_setup_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отменено", reply_markup=events_menu_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "stop_roulette")
async def stop_roulette_callback(callback: CallbackQuery):
    global active_event, roulette_chance, roulette_message_count
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    active_event = None
    roulette_chance = None
    roulette_message_count = 0
    await callback.message.edit_text("Ивент завершён", reply_markup=events_menu_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "stop_perebiv")
async def stop_perebiv_callback(callback: CallbackQuery):
    global active_event, perebiv_minutes, game_task
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if game_task:
        game_task.cancel()
    active_event = None
    perebiv_minutes = None
    await callback.message.edit_text("Ивент завершён", reply_markup=events_menu_keyboard())
    await callback.answer()

# --- Состояния ---
@dp.message(EventSetup.waiting_roulette_chance)
async def process_roulette_chance(message: types.Message, state: FSMContext):
    global active_event, roulette_chance, roulette_message_count
    try:
        chance = float(message.text.replace(',', '.').strip())
        if chance <= 0 or chance > 100:
            await message.answer("❌ Введите число от 0 до 100.")
            return
        roulette_chance = chance
        active_event = "roulette"
        roulette_message_count = 0
        await state.clear()
        await message.answer(f"✅ Ивент {EVENT_TITLES['roulette']} запущен!\nШанс: {chance}%", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введите корректное число.")

@dp.message(EventSetup.waiting_perebiv_minutes)
async def process_perebiv_minutes(message: types.Message, state: FSMContext):
    global active_event, perebiv_minutes
    try:
        minutes = int(message.text.strip())
        if minutes < 1 or minutes > 30:
            await message.answer("❌ Введите число от 1 до 30.")
            return
        perebiv_minutes = minutes
        active_event = "perebiv"
        await state.clear()
        await message.answer(f"✅ Ивент {EVENT_TITLES['perebiv']} запущен!\nВремя: {minutes} мин.", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введите целое число.")
        # --- Обработка ВСЕХ сообщений в группах (команды) ---
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_all_messages(message: types.Message):
    if not message.text:
        return
    
    text = message.text.lower().strip()
    
    # Пинг
    if text in ('.пинг', '!пинг', '/пинг', '.ping', '!ping', '/ping'):
        start = time.time()
        msg = await message.reply("📡 Пинг...")
        end = time.time()
        await msg.edit_text(f"🏓 Понг! {round((end - start) * 1000, 2)} мс")
    
    # Помощь
    elif text in ('.команды', '.командс', '.инфа', '.инфо', '.help', '!команды', '/команды', '/help'):
        await message.reply(HELP_TEXT)
    
    # Чек
    elif text in ('.чек', '.check', '.файл', '!чек', '/чек', '/check'):
        if message.reply_to_message and message.reply_to_message.document:
            await message.reply("🔍 Файл получен, проверяю... (заглушка)")
        else:
            await message.reply("❌ Ответьте на сообщение с файлом для проверки.")
    
    # Мут
    elif text.startswith(('.тише', '!тише', '/тише', '.mute', '!mute', '/mute')):
        if not await is_chat_admin(message.chat.id, message.from_user.id):
            await message.reply("❌ У вас нет прав администратора.")
            return
        target_user = None
        if message.reply_to_message:
            target_user = message.reply_to_message.from_user
        else:
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                arg = parts[1].strip()
                try:
                    user_id = int(arg)
                    member = await bot.get_chat_member(message.chat.id, user_id)
                    target_user = member.user
                except:
                    if arg.startswith('@'):
                        arg = arg[1:]
                    try:
                        member = await bot.get_chat_member(message.chat.id, arg)
                        target_user = member.user
                    except:
                        await message.reply("❌ Пользователь не найден.")
                        return
            else:
                await message.reply("❌ Ответьте на сообщение или укажите ID/username.")
                return
        if not target_user:
            return
        if target_user.is_bot:
            await message.reply("❌ Ботам нельзя выдавать мут.")
            return
        try:
            target_member = await bot.get_chat_member(message.chat.id, target_user.id)
            if target_member.status in ('administrator', 'creator'):
                await message.reply("⚠️ Нельзя мутить администраторов.")
                return
            until_date = datetime.now() + timedelta(minutes=1)
            await bot.restrict_chat_member(
                message.chat.id,
                target_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            await message.reply(f"🔇 {format_user_link(target_user.id, target_user.username, target_user.first_name)} отправлен(а) успокоиться на 1 минуту.")
        except Exception as e:
            await message.reply(f"❌ Ошибка: {e}")

# --- Запуск ---
async def main():
    # Уведомление владельцев
    for owner_id in OWNER_IDS:
        try:
            await bot.send_message(owner_id, "✅ Бот запущен и готов к работе!")
        except Exception:
            pass
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.exception("Bot crashed")
        for owner_id in OWNER_IDS:
            try:
                await bot.send_message(owner_id, f"❌ Бот упал с ошибкой:\n{e}")
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
