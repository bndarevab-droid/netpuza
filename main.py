import asyncio
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
import logging
import re

TOKEN = '8655620590:AAFIkLnlmCN9kVd_8xggI9isiiFC1QL3AR4'
GROUP_CHAT_ID = -1004493287292
OWNER_ID = 7545129896  # только этот пользователь может использовать /free

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- База данных (для побед) ---
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

def add_win(user_id):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO wins (user_id, win_time) VALUES (?, ?)',
                (user_id, datetime.now()))
    conn.commit()
    conn.close()

init_db()

# --- Глобальные переменные игры ---
game_task = None
game_message = None
game_leader = None
game_end_time = None

# --- Активные "фришки" (множество кортежей (chat_id, user_id)) ---
active_free = set()

def format_user_link(user_id, username, first_name):
    if username:
        return f"@{username}"
    else:
        return f'<a href="tg://user?id={user_id}">{first_name}</a>'

# --- Функция игры (без изменений) ---
async def start_game(chat_id, user_id, username, first_name, message_id_to_edit=None):
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
            f"⏳ До конца: <b>4 мин.</b>",
            parse_mode="HTML"
        )
        game_message = msg
    else:
        game_message = await bot.edit_message_text(
            f"⚡ <b>Перебито!</b>\n"
            f"Новый лидер: {leader_display}\n"
            f"⏳ До конца: <b>4 мин.</b>",
            chat_id=chat_id,
            message_id=message_id_to_edit,
            parse_mode="HTML"
        )

    game_end_time = datetime.now() + timedelta(minutes=4)

    async def timer_loop():
        global game_task, game_message, game_leader, game_end_time
        nonlocal leader_display
        remaining = 4
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
                    add_win(user_id)
                except Exception:
                    pass
                game_task = None
                game_message = None
                game_leader = None
                game_end_time = None
                break

    game_task = asyncio.create_task(timer_loop())

# --- Команда /free (только для владельца) ---
@dp.message(Command('free'))
async def free_cmd(message: types.Message):
    # Проверяем, что команда вызвана владельцем
    if message.from_user.id != OWNER_ID:
        await message.reply("⛔ У вас нет прав на использование этой команды.")
        return

    # Проверяем, что команда вызвана в группе
    if message.chat.type not in ('group', 'supergroup'):
        await message.reply("⚠️ Команда /free работает только в группе.")
        return

    chat_id = message.chat.id

    # Определяем цель
    target_user = None

    # 1. Если команда в ответ на сообщение
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        # 2. Если есть аргумент (числовой ID)
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            arg = args[1].strip()
            # Пробуем распарсить как число
            try:
                user_id = int(arg)
                # Получаем информацию о пользователе через get_chat_member
                try:
                    member = await bot.get_chat_member(chat_id, user_id)
                    target_user = member.user
                except Exception:
                    await message.reply("❌ Пользователь с таким ID не найден в этой группе.")
                    return
            except ValueError:
                # Возможно, это @username, но получить ID по username сложно, поэтому откажем
                await message.reply("❌ Укажите числовой ID пользователя или ответьте на его сообщение.")
                return
        else:
            await message.reply("❌ Укажите ID пользователя или ответьте на его сообщение.")
            return

    if not target_user:
        await message.reply("❌ Не удалось определить пользователя.")
        return

    if target_user.is_bot:
        await message.reply("❌ Нельзя выдать админку боту.")
        return

    user_id = target_user.id

    # Проверяем, не является ли цель уже администратором (опционально)
    # Можно пропустить, но чтобы не сломать, лучше проверить
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ('administrator', 'creator'):
            await message.reply("⚠️ Пользователь уже является администратором.")
            return
    except Exception:
        pass

    # Выдаём права администратора без прав (все False)
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
        # Добавляем в активные фришки
        active_free.add((chat_id, user_id))
        await message.reply(f"✅ Пользователь {format_user_link(user_id, target_user.username, target_user.first_name)} получил одно бесплатное сообщение. После отправки права будут сняты.")
    except Exception as e:
        await message.reply(f"❌ Ошибка при выдаче прав: {e}")

# --- Обработчик сообщений в группе (добавлена проверка на активные фришки) ---
@dp.message(F.chat.id == GROUP_CHAT_ID, F.content_type == 'text')
async def group_message_handler(message: types.Message):
    if message.from_user.is_bot:
        return

    user = message.from_user
    chat_id = message.chat.id

    # --- Проверка на активную фришку ---
    if (chat_id, user.id) in active_free:
        # Снимаем админку
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
            # Удаляем из активных
            active_free.remove((chat_id, user.id))
            # Не продолжаем обработку этого сообщения (чтобы не запускать игру)
            return
        except Exception as e:
            logging.error(f"Ошибка снятия админки у {user.id}: {e}")

    # --- Далее идёт стандартная обработка для игры ---
    if game_leader and game_leader[0] == user.id:
        return

    get_or_create_user(user.id, user.username, user.first_name, user.last_name)

    if game_message:
        await start_game(
            GROUP_CHAT_ID,
            user.id,
            user.username,
            user.first_name,
            message_id_to_edit=game_message.message_id
        )
    else:
        await start_game(
            GROUP_CHAT_ID,
            user.id,
            user.username,
            user.first_name,
            message_id_to_edit=None
        )

# --- Запуск ---
async def main():
    print("✅ Бот запущен (добавлена команда /free)!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
