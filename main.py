import asyncio
import sqlite3
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import logging

TOKEN = "8655620590:AAGehKB669q07WzgY8vzyUT5Ys2JyswnL0A"
GROUP_CHAT_ID = -1004493287292
OWNER_IDS = [7545129896, 8184136446]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Глобальные переменные ---
game_task = None          # задача таймера для перебива
game_message = None       # сообщение с игрой перебив
game_leader = None        # (user_id, username, first_name) текущего лидера
game_end_time = None      # время окончания перебива
active_free = set()       # (chat_id, user_id) для бесплатных сообщений
bot_enabled = True        # общий режим работы бота

# --- Переменные для ивентов ---
active_event = None       # None, 'roulette', 'perebiv'
event_params = {}         # {'chance': int} или {'minutes': int}

# --- Состояния FSM для ввода параметров ивентов ---
class EventStates(StatesGroup):
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

def add_win(user_id, prize="Победа в игре"):
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
    else:
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

# --- Клавиатуры ---
def main_menu_keyboard(enabled):
    status = "Включен" if enabled else "Выключен"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Переключатель", callback_data="toggle_menu")],
        [InlineKeyboardButton(text="🏆 Победители", callback_data="winners")],
        [InlineKeyboardButton(text="🎲 Ивенты", callback_data="events")],
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

def events_menu_keyboard(active_event, event_params):
    keyboard = []
    if active_event is None:
        keyboard.append([InlineKeyboardButton(text="🎰 Legal Roulette", callback_data="start_roulette")])
        keyboard.append([InlineKeyboardButton(text="⚡ Перебив", callback_data="start_perebiv")])
    else:
        info = ""
        if active_event == 'roulette':
            info = f"Шанс: {event_params.get('chance', '?')}%"
        elif active_event == 'perebiv':
            info = f"Время: {event_params.get('minutes', '?')} мин."
        keyboard.append([InlineKeyboardButton(text=f"⏹ Завершить ивент ({info})", callback_data="stop_event")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Команда /start только для владельцев в ЛС ---
@dp.message(Command("start"), F.chat.type == "private")
async def start_command(message: types.Message):
    user_id = message.from_user.id
    if user_id not in OWNER_IDS:
        return
    keyboard, status = main_menu_keyboard(bot_enabled)
    await message.answer(
        f"Добро пожаловать назад, админ панель:\nРежим: {status}",
        reply_markup=keyboard
    )

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

# --- Callback-обработчики ---
@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
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
    await callback.message.edit_text(
        "Выберите действие:",
        reply_markup=keyboard
    )
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
            name = username if username else (first_name or str(user_id))
            link = format_user_link(user_id, username, first_name)
            time_str = format_datetime_moscow(datetime.fromisoformat(win_time))
            lines.append(f"{idx}. {link} (ID: {user_id}) – {time_str}")
        text = f"🏆 Список победителей (всего {len(rows)}):\n\n" + "\n".join(lines)
    keyboard = winners_menu_keyboard()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

# --- Обработчики ивентов ---
@dp.callback_query(F.data == "events")
async def events_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    keyboard = events_menu_keyboard(active_event, event_params)
    if active_event is None:
        text = "🎲 Управление ивентами\n\nВыберите ивент для запуска:"
    else:
        event_name = "Legal Roulette" if active_event == 'roulette' else "Перебив"
        text = f"🎲 Активный ивент: <b>{event_name}</b>\n"
        if active_event == 'roulette':
            text += f"Шанс победы: {event_params.get('chance', '?')}%"
        else:
            text += f"Время на перебив: {event_params.get('minutes', '?')} мин."
        text += "\n\nНажмите кнопку ниже, чтобы завершить."
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "start_roulette")
async def start_roulette_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event is not None:
        await callback.answer("⚠️ Сначала завершите текущий ивент", show_alert=True)
        return
    await state.set_state(EventStates.waiting_roulette_chance)
    await callback.message.edit_text("Введите шанс победы (число от 1 до 100):")
    await callback.answer()

@dp.callback_query(F.data == "start_perebiv")
async def start_perebiv_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event is not None:
        await callback.answer("⚠️ Сначала завершите текущий ивент", show_alert=True)
        return
    await state.set_state(EventStates.waiting_perebiv_minutes)
    await callback.message.edit_text("Введите время на перебив (минуты, от 1 до 30):")
    await callback.answer()

@dp.callback_query(F.data == "stop_event")
async def stop_event_callback(callback: CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    global active_event, event_params, game_task, game_message, game_leader, game_end_time
    if active_event is None:
        await callback.answer("Нет активного ивента", show_alert=True)
        return
    event_name = "Legal Roulette" if active_event == 'roulette' else "Перебив"
    # Если это перебив и игра запущена, останавливаем таймер
    if active_event == 'perebiv' and game_task and not game_task.done():
        game_task.cancel()
        try:
            await game_task
        except asyncio.CancelledError:
            pass
        game_task = None
        # Редактируем сообщение игры, чтобы показать остановку
        if game_message:
            try:
                await bot.edit_message_text(
                    f"⏹ Ивент «Перебив» завершён досрочно.",
                    chat_id=game_message.chat.id,
                    message_id=game_message.message_id
                )
            except Exception:
                pass
        game_message = None
        game_leader = None
        game_end_time = None
    # Сбрасываем ивент
    active_event = None
    event_params = {}
    await bot.send_message(GROUP_CHAT_ID, f"⏹ Ивент «{event_name}» завершён.")
    keyboard = events_menu_keyboard(active_event, event_params)
    await callback.message.edit_text("🎲 Управление ивентами\n\nВыберите ивент для запуска:", reply_markup=keyboard)
    await callback.answer("Ивент завершён")

# --- Обработчики ввода параметров через FSM ---
@dp.message(EventStates.waiting_roulette_chance, F.text)
async def process_roulette_chance(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    try:
        chance = int(message.text.strip())
        if not (1 <= chance <= 100):
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите целое число от 1 до 100.")
        return
    global active_event, event_params
    if active_event is not None:
        await message.reply("⚠️ Уже есть активный ивент. Завершите его сначала.")
        await state.clear()
        return
    active_event = 'roulette'
    event_params = {'chance': chance}
    await bot.send_message(GROUP_CHAT_ID, f"🎲 Запущен ивент <b>Legal Roulette</b>!\nШанс победы: {chance}%.\nКаждое сообщение может принести приз!", parse_mode="HTML")
    await state.clear()
    # Обновляем меню ивентов
    keyboard = events_menu_keyboard(active_event, event_params)
    await message.answer("✅ Ивент запущен!", reply_markup=keyboard)
    # Также обновляем админ-меню для всех открытых окон? Можно не заморачиваться.

@dp.message(EventStates.waiting_perebiv_minutes, F.text)
async def process_perebiv_minutes(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    try:
        minutes = int(message.text.strip())
        if not (1 <= minutes <= 30):
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите целое число от 1 до 30.")
        return
    global active_event, event_params
    if active_event is not None:
        await message.reply("⚠️ Уже есть активный ивент. Завершите его сначала.")
        await state.clear()
        return
    active_event = 'perebiv'
    event_params = {'minutes': minutes}
    await bot.send_message(GROUP_CHAT_ID, f"⚡ Запущен ивент <b>Перебив</b>!\nВремя на перебив: {minutes} мин.\nПервое сообщение станет лидером!", parse_mode="HTML")
    await state.clear()
    keyboard = events_menu_keyboard(active_event, event_params)
    await message.answer("✅ Ивент запущен!", reply_markup=keyboard)

# --- Игра "Перебив" (модифицирована с учётом времени) ---
async def start_game(chat_id, user_id, username, first_name, duration_minutes, message_id_to_edit=None):
    global game_task, game_message, game_leader, game_end_time

    if game_task and not game_task.done():
        game_task.cancel()
        try:
            await game_task
        except asyncio.CancelledError:
            pass

    leader_display = format_user_link(user_id, username, first_name)
    game_leader = (user_id, username, first_name)

    if message_id_to_edit is None:
        msg = await bot.send_message(
            chat_id,
            f"⚡ <b>Перебито!</b>\n"
            f"Новый лидер: {leader_display}\n"
            f"⏳ До конца: <b>{duration_minutes} мин.</b>",
            parse_mode="HTML"
        )
        game_message = msg
    else:
        game_message = await bot.edit_message_text(
            f"⚡ <b>Перебито!</b>\n"
            f"Новый лидер: {leader_display}\n"
            f"⏳ До конца: <b>{duration_minutes} мин.</b>",
            chat_id=chat_id,
            message_id=message_id_to_edit,
            parse_mode="HTML"
        )

    game_end_time = datetime.now() + timedelta(minutes=duration_minutes)

    async def timer_loop():
        nonlocal duration_minutes, leader_display
        remaining = duration_minutes
        while remaining > 0:
            await asyncio.sleep(60)
            remaining -= 1
            if remaining > 0:
                try:
                    await bot.edit_message_text(
                        f"⚡ <b>Перебито!</b>\n"
                        f"Новый лидер: {leader_display}\n"
                        f"⏳ До конца: <b>{remaining} мин.</b>",
                        chat_id=chat_id,
                        message_id=game_message.message_id,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            else:
                # Время вышло, объявляем победителя
                try:
                    await bot.edit_message_text(
                        f"⏰ <b>Время закончилось.</b>\n"
                        f"🏆 Победитель: {leader_display}",
                        chat_id=chat_id,
                        message_id=game_message.message_id,
                        parse_mode="HTML"
                    )
                    add_win(user_id, "Победа в Перебиве")
                except Exception:
                    pass
                # Сбрасываем глобальные переменные игры
                global game_task, game_message, game_leader, game_end_time
                game_task = None
                game_message = None
                game_leader = None
                game_end_time = None
                break

    game_task = asyncio.create_task(timer_loop())

# --- Обработчик сообщений в группе ---
@dp.message(F.chat.id == GROUP_CHAT_ID, F.text)
async def group_message_handler(message: types.Message):
    global bot_enabled, active_event, event_params, game_leader

    if not bot_enabled:
        return

    if message.fro
