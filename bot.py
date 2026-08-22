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
GROUP_CHAT_ID = -1004493287292              # Чат для ивентов
OWNER_IDS = [7545129896, 8184136446]
groza_chat_id = 1003843695003              # Чат "Гроза"

COMMAND_PREFIXES = ("!", ".", "/", "?")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Глобальные переменные
game_task = None
game_message = None
game_leader = None
game_end_time = None
active_free = set()
bot_enabled = True

active_event = None
roulette_chance = None
roulette_message_count = 0
perebiv_minutes = None

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
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            last_seen TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS wins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            win_time TIMESTAMP,
            prize TEXT DEFAULT 'Победа в игре'
        )
    ''')
    conn.commit()
    conn.close()

def get_or_create_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_seen)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, datetime.now()))
    conn.commit()
    conn.close()

def get_user_wins(user_id):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT win_time, prize FROM wins WHERE user_id = ? ORDER BY win_time DESC
    ''', (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def add_win(user_id, prize='Победа в игре'):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO wins (user_id, win_time, prize) VALUES (?, ?, ?)',
                (user_id, datetime.now(), prize))
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
    cur.execute('''
        SELECT wins.user_id, wins.win_time, users.username, users.first_name
        FROM wins
        LEFT JOIN users ON wins.user_id = users.user_id
        ORDER BY wins.win_time DESC
    ''')
    rows = cur.fetchall()
    conn.close()
    return rows

async def is_chat_admin(chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ('administrator', 'creator')
    except Exception:
        return False

# --- Текстовые команды ---
HELP_TEXT = (
    "Я #нетпуза, вот мои команды:\n\n"
    ".тише/.помолчи/.успокойся - отправляет успокоится на 1 минуту.\n"
    ".команды/.командс/.инфо/.инфа - показывает список команд\n"
    ".пинг - показывает пинг бота\n"
    ".чек/.check/.файл - анализ файла и проверка на трояны, майнеры и всякие вирусы\n\n"
    "Использование административных команд в отношении других администраторов чата присекаются."
)

COMMAND_ALIASES = {
    "тише": "mute",
    "помолчи": "mute",
    "успокойся": "mute",
    "команды": "help",
    "командс": "help",
    "инфа": "help",
    "инфо": "help",
    "пинг": "ping",
    "ping": "ping",
    "чек": "check",
    "check": "check",
    "файл": "check",
    "help": "help",
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
    matches = difflib.get_close_matches(first_word, COMMAND_ALIASES.keys(), n=1, cutoff=0.6)
    if matches:
        return COMMAND_ALIASES[matches[0]]
    return None

def command_filter(message: types.Message) -> bool:
    return bool(message.text) and resolve_command(message.text) is not None

# --- Клавиатуры ---
def main_menu_keyboard(enabled):
    status = "Включен" if enabled else "Выключен"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Переключатель", callback_data="toggle_menu")],
        [InlineKeyboardButton(text="🏆 Победители", callback_data="winners")],
        [InlineKeyboardButton(text="🎉 Ивенты", callback_data="events_menu")],
    ])
    return keyboard, status

def toggle_menu_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Turn ON", callback_data="turn_on"),
         InlineKeyboardButton(text="🔴 Turn OFF", callback_data="turn_off")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return keyboard

def winners_menu_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    return keyboard

def events_status_text():
    if active_event == "roulette":
        return (
            f"🎉 <b>Ивенты</b>\n\n"
            f"Активный ивент: {EVENT_TITLES['roulette']}\n"
            f"Шанс приза за сообщение: <b>{roulette_chance}%</b>\n\n"
            f"Каждое сообщение в чате (кроме первых 5 с начала ивента) имеет "
            f"{roulette_chance}% шанса на получение приза. Ивент завершается автоматически "
            f"при выигрыше."
        )
    elif active_event == "perebiv":
        return (
            f"🎉 <b>Ивенты</b>\n\n"
            f"Активный ивент: {EVENT_TITLES['perebiv']}\n"
            f"Время удержания: <b>{perebiv_minutes} мин.</b>\n\n"
            f"Сообщение, которое остаётся последним {perebiv_minutes} мин., получает приз."
        )
    else:
        return (
            "🎉 <b>Ивенты</b>\n\n"
            "Активных ивентов нет.\n\n"
            "🎲 <b>Legal Roulette</b> — каждое сообщение в чате (кроме первых 5 с начала "
            "ивента) имеет custom% шанс на получение приза. Завершается автоматически "
            "при выигрыше.\n"
            "⚡ <b>Перебив</b> — сообщение, которое остаётся последним custom минут, "
            "получает приз."
        )

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
        await message.answer(
            f"Добро пожаловать назад, админ панель:\nРежим: {status}",
            reply_markup=keyboard
        )
        return
    rows = get_user_wins(user_id)
    if not rows:
        await message.answer(
            "Привет! 👋\nПобед пока нет. Участвуй в ивентах в чате, чтобы выиграть приз 🎉"
        )
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
        await message.reply("⚠️ Бот временно отключен")
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    chat_id = message.chat.id
    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            arg = args[1].strip()
            try:
                user_id = int(arg)
                try:
                    member = await bot.get_chat_member(chat_id, user_id)
                    target_user = member.user
                except Exception:
                    await message.reply("❌ Пользователь с таким ID не найден в чате")
                    return
            except ValueError:
                if arg.startswith('@'):
                    arg = arg[1:]
                try:
                    member = await bot.get_chat_member(chat_id, arg)
                    target_user = member.user
                except Exception:
                    await message.reply("❌ Пользователь с таким юзернеймом не найден в чате")
                    return
        else:
            await message.reply("❌ Укажи id или юзернейм цели или ответь на её сообщение")
            return
    if not target_user:
        await message.reply("❌ Не удалось определить пользователя")
        return
    if target_user.is_bot:
        await message.reply("❌ Выдача ботам запрещена")
        return
    user_id = target_user.id
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ('administrator', 'creator'):
            await message.reply("⚠️ У пользователя уже есть права администратора")
            return
    except Exception:
        pass
    try:
        await bot.promote_chat_member(
            chat_id,
            user_id,
            can_change_info=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_chat=False,
            can_manage_video_chats=False,
            can_manage_topics=False
        )
        active_free.add((chat_id, user_id))
        await message.reply(f"✅ Пользователь {format_user_link(user_id, target_user.username, target_user.first_name)} получил одно бесплатное сообщение.")
    except Exception as e:
        await message.reply(f"❌ Ошибка при выдаче прав: {e}")

# --- ПРИВЕТСТВИЕ (только в чате "Гроза") ---
@dp.my_chat_member(F.chat.id == groza_chat_id)
async def bot_added_to_groza(event: ChatMemberUpdated):
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    was_out = old_status in ("left", "kicked")
    now_in = new_status in ("member", "administrator")
    if not (was_out and now_in):
        return
    chat_id = event.chat.id
    greeting = await bot.send_message(chat_id, "Приветствую, господа!")
    await asyncio.sleep(3)
    try:
        await bot.delete_message(chat_id, greeting.message_id)
    except Exception:
        pass
    loading_parts = ["За", "гру", "зка", "Готово"]
    loading_msgs = []
    for i, part in enumerate(loading_parts):
        m = await bot.send_message(chat_id, part)
        loading_msgs.append(m)
        if i < len(loading_parts) - 1:
            await asyncio.sleep(0.5)
    await asyncio.sleep(1)
    for m in loading_msgs:
        try:
            await bot.delete_message(chat_id, m.message_id)
        except Exception:
            pass
    await bot.send_message(chat_id, HELP_TEXT)
    # --- Callback-обработчики ---
@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    keyboard, status = main_menu_keyboard(bot_enabled)
    await callback.message.edit_text(
        f"Добро пожаловать назад, админ панель:\nРежим: {status}",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "toggle_menu")
async def toggle_menu_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    keyboard = toggle_menu_keyboard()
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
    await callback.message.edit_text(
        f"Добро пожаловать назад, админ панель:\nРежим: {status}",
        reply_markup=keyboard
    )
    await callback.answer("Режим включён ✅")

@dp.callback_query(F.data == "turn_off")
async def turn_off_callback(callback: CallbackQuery):
    global bot_enabled
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    bot_enabled = False
    keyboard, status = main_menu_keyboard(bot_enabled)
    await callback.message.edit_text(
        f"Добро пожаловать назад, админ панель:\nРежим: {status}",
        reply_markup=keyboard
    )
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
        text = f"🏆 Список победителей (всего {len(rows)}):\n\n" + "\n".join(lines)
    keyboard = winners_menu_keyboard()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "events_menu")
async def events_menu_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "start_roulette")
async def start_roulette_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event is not None:
        await callback.answer("⚠️ Другой ивент уже активен", show_alert=True)
        return
    await state.set_state(EventSetup.waiting_roulette_chance)
    await callback.message.edit_text(
        "🎲 <b>Legal Roulette</b>\n\n"
        "Введите шанс победы в % (число от 0 до 100, можно дробное), "
        "например: 5 или 2.5",
        reply_markup=cancel_setup_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "start_perebiv")
async def start_perebiv_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event is not None:
        await callback.answer("⚠️ Другой ивент уже активен", show_alert=True)
        return
    await state.set_state(EventSetup.waiting_perebiv_minutes)
    await callback.message.edit_text(
        "⚡ <b>Перебив</b>\n\n"
        "Сколько минут нужно продержаться лидером, чтобы получить приз? "
        "(целое число от 1 до 30)",
        reply_markup=cancel_setup_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "cancel_event_setup")
async def cancel_event_setup_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Отменено")

@dp.callback_query(F.data == "stop_roulette")
async def stop_roulette_callback(callback: CallbackQuery):
    global active_event, roulette_chance, roulette_message_count
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event != "roulette":
        await callback.answer("Этот ивент не активен", show_alert=True)
        return
    active_event = None
    roulette_chance = None
    roulette_message_count = 0
    await callback.message.edit_text(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Ивент завершён")

@dp.callback_query(F.data == "stop_perebiv")
async def stop_perebiv_callback(callback: CallbackQuery):
    global active_event, perebiv_minutes, game_task, game_message, game_leader, game_end_time
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event != "perebiv":
        await callback.answer("Этот ивент не активен", show_alert=True)
        return
    if game_task and not game_task.done():
        game_task.cancel()
        try:
            await game_task
        except asyncio.CancelledError:
            pass
    if game_message:
        try:
            await bot.edit_message_text(
                "⏹ <b>Ивент «Перебив» остановлен администратором.</b>",
                chat_id=GROUP_CHAT_ID,
                message_id=game_message.message_id,
                parse_mode="HTML"
            )
        except Exception:
            pass
    game_task = None
    game_message = None
    game_leader = None
    game_end_time = None
    active_event = None
    perebiv_minutes = None
    await callback.message.edit_text(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Ивент завершён")

# --- Обработчики состояний (ввод параметров ивентов) ---
@dp.message(EventSetup.waiting_roulette_chance)
async def process_roulette_chance(message: types.Message, state: FSMContext):
    global active_event, roulette_chance, roulette_message_count
    if message.from_user.id not in OWNER_IDS:
        return
    try:
        chance = float(message.text.replace(',', '.').strip())
        if chance <= 0 or chance > 100:
            await message.answer("❌ Введите число от 0 до 100 (не включительно 0).")
            return
        roulette_chance = chance
        active_event = "roulette"
        roulette_message_count = 0
        await state.clear()
        await message.answer(
            f"✅ Ивент {EVENT_TITLES['roulette']} запущен!\n"
            f"Шанс: {chance}%",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Введите корректное число.")

@dp.message(EventSetup.waiting_perebiv_minutes)
async def process_perebiv_minutes(message: types.Message, state: FSMContext):
    global active_event, perebiv_minutes, game_task, game_message, game_leader, game_end_time
    if message.from_user.id not in OWNER_IDS:
        return
    try:
        minutes = int(message.text.strip())
        if minutes < 1 or minutes > 30:
            await message.answer("❌ Введите целое число от 1 до 30.")
            return
        perebiv_minutes = minutes
        active_event = "perebiv"
        await state.clear()
        await message.answer(
            f"✅ Ивент {EVENT_TITLES['perebiv']} запущен!\n"
            f"Время удержания: {minutes} мин.",
            parse_mode="HTML"
        )
        # Инициализация перебива
        game_leader = None
        game_end_time = None
        game_message = None
        game_task = None
    except ValueError:
        await message.answer("❌ Введите целое число.")
        # --- Обработка сообщений в группе для ивентов ---
@dp.message(F.chat.id == GROUP_CHAT_ID, F.content_type.in_({'text', 'photo', 'video', 'document', 'sticker', 'voice', 'video_note', 'animation'}))
async def handle_event_messages(message: types.Message):
    global game_leader, game_end_time, game_task, game_message, active_event, roulette_message_count
    if not bot_enabled:
        return
    if message.from_user.is_bot:
        return

    # Legal Roulette
    if active_event == "roulette":
        roulette_message_count += 1
        if roulette_message_count > 5:  # Первые 5 сообщений не участвуют
            if random.random() * 100 < roulette_chance:
                winner = message.from_user
                prize = "Победа в Legal Roulette"
                add_win(winner.id, prize)
                try:
                    await message.reply(
                        f"🎉 <b>{format_user_link(winner.id, winner.username, winner.first_name)}</b> "
                        f"выиграл(а) в {EVENT_TITLES['roulette']}!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                active_event = None
                roulette_chance = None
                roulette_message_count = 0
                return
        return

    # Перебив
    if active_event == "perebiv":
        # Новый лидер
        game_leader = (message.from_user.id, message.from_user.username, message.from_user.first_name)
        game_end_time = datetime.now() + timedelta(minutes=perebiv_minutes)

        if game_task and not game_task.done():
            game_task.cancel()
            try:
                await game_task
            except asyncio.CancelledError:
                pass

        # Удаляем прошлое сообщение-таймер
        if game_message:
            try:
                await bot.delete_message(GROUP_CHAT_ID, game_message.message_id)
            except Exception:
                pass

        # Отправляем новое сообщение-таймер
        game_message = await bot.send_message(
            GROUP_CHAT_ID,
            f"⚡ <b>Перебив!</b>\n"
            f"Лидер: {format_user_link(game_leader[0], game_leader[1], game_leader[2])}\n"
            f"Удерживайте лидерство {perebiv_minutes} мин.",
            parse_mode="HTML"
        )

        async def check_perebiv():
            global active_event, perebiv_minutes, game_task, game_message, game_leader, game_end_time
            await asyncio.sleep(perebiv_minutes * 60)
            if game_leader and game_leader[0] == message.from_user.id and active_event == "perebiv":
                winner_id, username, first_name = game_leader
                prize = "Победа в Перебиве"
                add_win(winner_id, prize)
                try:
                    await bot.send_message(
                        GROUP_CHAT_ID,
                        f"🎉 <b>{format_user_link(winner_id, username, first_name)}</b> "
                        f"удержал(а) лидерство и победил(а) в {EVENT_TITLES['perebiv']}!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                active_event = None
                perebiv_minutes = None
                game_task = None
                if game_message:
                    try:
                        await bot.delete_message(GROUP_CHAT_ID, game_message.message_id)
                    except Exception:
                        pass
                game_message = None
                game_leader = None
                game_end_time = None

        game_task = asyncio.create_task(check_perebiv())

# --- Текстовые команды (mute, help, ping, check) ---
@dp.message(F.chat.type.in_({"group", "supergroup"}), command_filter)
async def handle_text_commands(message: types.Message):
    if not bot_enabled:
        return
    command = resolve_command(message.text)
    if command == "mute":
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
            await message.reply(
                f"🔇 {format_user_link(target_user.id, target_user.username, target_user.first_name)} "
                f"отправлен(а) успокоиться на 1 минуту."
            )
        except Exception as e:
            await message.reply(f"❌ Ошибка: {e}")

    elif command == "help":
        await message.reply(HELP_TEXT)

    elif command == "ping":
        start = time.time()
        msg = await message.reply("📡 Пинг...")
        end = time.time()
        await msg.edit_text(f"🏓 Понг! Время отклика: {round((end - start) * 1000, 2)} мс")

    elif command == "check":
        if message.reply_to_message and message.reply_to_message.document:
            file_id = message.reply_to_message.document.file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path
            file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
            # Здесь можно реализовать проверку через VirusTotal
            await message.reply("🔍 Файл получен, проверяю... (заглушка)")
        else:
            await message.reply("❌ Ответьте на сообщение с файлом для проверки.")

# --- Запуск ---
async def main():
    # Уведомление владельцев о запуске
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
