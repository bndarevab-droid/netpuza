import asyncio
import random
import sqlite3
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

# Инициализация бота
bot = Bot(token=TOKEN)

dp = Dispatcher(storage=MemoryStorage())

# --- Глобальные переменные ---
game_task = None
game_message = None
game_leader = None
game_end_time = None
active_free = set()
bot_enabled = True  # режим работы бота

# --- Ивенты ---
active_event = None          # None | "roulette" | "perebiv"
roulette_chance = None       # float, % шанс приза за сообщение
perebiv_minutes = None       # int, сколько минут нужно продержаться лидером

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
    else:
        return f'<a href="tg://user?id={user_id}">{first_name}</a>'


def format_datetime_moscow(dt):
    """Преобразует UTC время в MSK (UTC+3) и форматирует"""
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
            f"Каждое сообщение в чате имеет {roulette_chance}% шанса на получение приза."
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
            "🎲 <b>Legal Roulette</b> — каждое сообщение в чате имеет custom% шанс "
            "на получение приза.\n"
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


# --- Команда /start только для владельцев в ЛС ---
@dp.message(Command("start"), F.chat.type == "private")
async def start_command(message: types.Message):
    user_id = message.from_user.id
    if user_id not in OWNER_IDS:
        # Игнорируем невладельцев
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

    # ИСПРАВЛЕНИЕ: Игнорируем ботов и каналы (если нет from_user)
    if message.from_user is None or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    target_user = None

    # Проверяем, есть ли ответ на сообщение
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        # Пытаемся найти пользователя по тексту
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            arg = args[1].strip()
            # Пробуем как ID
            try:
                user_id = int(arg)
                try:
                    member = await bot.get_chat_member(chat_id, user_id)
                    target_user = member.user
                except Exception:
                    await message.reply("❌ Пользователь с таким ID не найден в чате")
                    return
            except ValueError:
                # Пробуем как username
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

    # Проверяем, не админ ли уже
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ('administrator', 'creator'):
            await message.reply("⚠️ У пользователя уже есть права администратора")
            return
    except Exception:
        pass

    # Выдаём права
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


# --- Callback-обработчики (главное меню) ---
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
            link = format_user_link(user_id, username, first_name)
            time_str = format_datetime_moscow(datetime.fromisoformat(win_time))
            lines.append(f"{idx}. {link} (ID: {user_id}) – {time_str}")
        text = f"🏆 Список победителей (всего {len(rows)}):\n\n" + "\n".join(lines)
    keyboard = winners_menu_keyboard()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# --- Callback-обработчики (Ивенты) ---
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
    global active_event, roulette_chance
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if active_event != "roulette":
        await callback.answer("Этот ивент не активен", show_alert=True)
        return
    active_event = None
    roulette_chance = None
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


# --- Приём деталей ивентов (только владельцы, в ЛС) ---
@dp.message(EventSetup.waiting_roulette_chance, F.chat.type == "private")
async def process_roulette_chance(message: types.Message, state: FSMContext):
    global active_event, roulette_chance
    if message.from_user.id not in OWNER_IDS:
        return

    text = message.text.strip().replace('%', '').replace(',', '.')
    try:
        chance = float(text)
    except ValueError:
        await message.reply("❌ Введите число, например 5 или 2.5", reply_markup=cancel_setup_keyboard())
        return

    if not (0 < chance <= 100):
        await message.reply("❌ Шанс должен быть больше 0 и не больше 100", reply_markup=cancel_setup_keyboard())
        return

    if active_event is not None:
        await state.clear()
        await message.answer("⚠️ Другой ивент уже был запущен раньше. Отменено.")
        return

    active_event = "roulette"
    roulette_chance = chance
    await state.clear()
    await message.answer(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )


@dp.message(EventSetup.waiting_perebiv_minutes, F.chat.type == "private")
async def process_perebiv_minutes(message: types.Message, state: FSMContext):
    global active_event, perebiv_minutes
    if message.from_user.id not in OWNER_IDS:
        return

    text = message.text.strip()
    try:
        minutes = int(text)
    except ValueError:
        await message.reply("❌ Введите целое число минут (1-30)", reply_markup=cancel_setup_keyboard())
        return

    if not (1 <= minutes <= 30):
        await message.reply("❌ Число должно быть от 1 до 30", reply_markup=cancel_setup_keyboard())
        return

    if active_event is not None:
        await state.clear()
        await message.answer("⚠️ Другой ивент уже был запущен раньше. Отменено.")
        return

    active_event = "perebiv"
    perebiv_minutes = minutes
    await state.clear()
    await message.answer(
        events_status_text(),
        reply_markup=events_menu_keyboard(),
        parse_mode="HTML"
    )


# --- Игра "Перебив" ---
async def start_game(chat_id, user_id, username, first_name, minutes, message_id_to_edit=None):
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
            f"⏳ До конца: <b>{minutes} мин.</b>",
            parse_mode="HTML"
        )
        game_message = msg
    else:
        game_message = await bot.edit_message_text(
            f"⚡ <b>Перебито!</b>\n"
            f"Новый лидер: {leader_display}\n"
            f"⏳ До конца: <b>{minutes} мин.</b>",
            chat_id=chat_id,
            message_id=message_id_to_edit,
            parse_mode="HTML"
        )

    game_end_time = datetime.now() + timedelta(minutes=minutes)

    async def timer_loop():
        global game_task, game_message, game_leader, game_end_time, active_event, perebiv_minutes
        nonlocal leader_display
        remaining = minutes
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
                try:
                    await bot.edit_message_text(
                        f"⏰ <b>Время закончилось.</b>\n"
                        f"🏆 Победитель: {leader_display}",
                        chat_id=chat_id,
                        message_id=game_message.message_id,
                        parse_mode="HTML"
                    )
                    add_win(user_id, prize='Перебив')
                except Exception:
                    pass
                game_task = None
                game_message = None
                game_leader = None
                game_end_time = None
                active_event = None
                perebiv_minutes = None
                break

    game_task = asyncio.create_task(timer_loop())


# --- Обработчик сообщений в группе ---
@dp.message(F.chat.id == GROUP_CHAT_ID, F.text)
async def group_message_handler(message: types.Message):
    global bot_enabled
    if not bot_enabled:
        # Бот выключен – игнорируем все сообщения
        return

    # ИСПРАВЛЕНИЕ: Если сообщение от канала или бота - игнорируем его
    if message.from_user is None or message.from_user.is_bot:
        return

    # Проверяем, не команда ли это /free (чтобы не запускать игру на неё)
    if message.text and message.text.startswith('/free'):
        return

    user = message.from_user
    chat_id = message.chat.id

    # Проверка на бесплатное сообщение
    if (chat_id, user.id) in active_free:
        try:
            await bot.promote_chat_member(
                chat_id,
                user.id,
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
            active_free.remove((chat_id, user.id))
            return
        except Exception as e:
            logging.error(f"Ошибка снятия админки у {user.id}: {e}")

    get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    # --- Ивент: Legal Roulette ---
    if active_event == "roulette":
        if random.uniform(0, 100) < roulette_chance:
            add_win(user.id, prize='Legal Roulette')
            await message.reply(
                f"🎉 <b>Приз!</b>\n"
                f"{format_user_link(user.id, user.username, user.first_name)} выиграл(а) в Legal Roulette!",
                parse_mode="HTML"
            )
        return

    # --- Ивент: Перебив ---
    if active_event == "perebiv":
        if game_leader and game_leader[0] == user.id:
            return

        if game_message:
            await start_game(
                GROUP_CHAT_ID,
                user.id,
                user.username,
                user.first_name,
                perebiv_minutes,
                message_id_to_edit=game_message.message_id
            )
        else:
            await start_game(
                GROUP_CHAT_ID,
                user.id,
                user.username,
                user.first_name,
                perebiv_minutes,
                message_id_to_edit=None
            )
        return

    # Ни один ивент не активен - просто ничего не делаем


# --- Запуск ---
async def send_startup_notification():
    """Отправляет сообщение о запуске всем владельцам"""
    for owner_id in OWNER_IDS:
        try:
            await bot.send_message(owner_id, "✅ Бот запущен и подключен!")
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление владельцу {owner_id}: {e}")


async def main():
    print("✅ Бот запущен! Ожидание соединения...")
    # Попытка отправить уведомление при старте
    await send_startup_notification()

    while True:
        try:
            await dp.start_polling(bot, skip_updates=True)
        except Exception as e:
            logging.error(f"Ошибка: {e}. Переподключение через 10 секунд...")
            await asyncio.sleep(10)
            continue


if __name__ == '__main__':
    asyncio.run(main())
