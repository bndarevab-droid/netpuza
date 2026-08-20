import asyncio
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
import logging

TOKEN = '8655620590:AAFIkLnlmCN9kVd_8xggI9isiiFC1QL3AR4'
GROUP_CHAT_ID = -1004493287292
OWNER_ID = 7545129896

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        connect_timeout=120,
        read_timeout=120,
        write_timeout=120
    )
)
dp = Dispatcher(storage=MemoryStorage())

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

def add_win(user_id):
    conn = sqlite3.connect('bot_data.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO wins (user_id, win_time) VALUES (?, ?)',
                (user_id, datetime.now()))
    conn.commit()
    conn.close()

init_db()

# --- Глобальные переменные ---
game_task = None
game_message = None
game_leader = None
game_end_time = None
active_free = set()
bot_started = False  # флаг, чтобы отправить сообщение только один раз

def format_user_link(user_id, username, first_name):
    if username:
        return f"@{username}"
    else:
        return f'<a href="tg://user?id={user_id}">{first_name}</a>'

# --- Игра ---
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

    if message.chat.type not in ('group', 'supergroup'):
        await message.reply("⚠️ Команда /free работает только в группе")
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
                await message.reply("❌ Укажите числовой id пользователя или ответьте на его сообщение")
                return
        else:
            await message.reply("❌ Укажи id или юзернейм цели")
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
            await message.reply("⚠️ У пользователя уже есть фри сообщение")
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

# --- Обработчик сообщений в группе ---
@dp.message(F.chat.id == GROUP_CHAT_ID, F.content_type == 'text')
async def group_message_handler(message: types.Message):
    if message.from_user.is_bot:
        return

    user = message.from_user
    chat_id = message.chat.id

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
    global bot_started
    print("✅ Бот запущен! Ожидание соединения...")
    
    while True:
        try:
            await dp.start_polling(bot, skip_updates=True)
        except Exception as e:
            logging.error(f"Ошибка: {e}. Переподключение через 10 секунд...")
            await asyncio.sleep(10)
            continue
        
        # Если дошли сюда — бот успешно подключился (но start_polling завершился)
        # Отправляем сообщение только один раз
        if not bot_started:
            try:
                await bot.send_message(GROUP_CHAT_ID, "✅ Бот подключен!")
                bot_started = True
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение: {e}")

if __name__ == '__main__':
    asyncio.run(main())
