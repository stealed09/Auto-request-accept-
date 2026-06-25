import asyncio
import logging
import sqlite3
import time
import json
import os
import re
import shutil
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, ChatJoinRequest, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ChatPermissions, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int(os.environ.get("OWNER_ID", "0"))

START_TIME = time.time()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_errors.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DATABASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
conn = sqlite3.connect("satoru.db", check_same_thread=False)
cur  = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id   INTEGER PRIMARY KEY,
    title     TEXT,
    username  TEXT,
    chat_type TEXT,
    accept    INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    first_name TEXT,
    username   TEXT,
    joined_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS join_logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER,
    chat_id   INTEGER,
    chat_title TEXT,
    joined_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS warnings (
    user_id INTEGER,
    chat_id INTEGER,
    count   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);
""")

for col_sql in [
    "ALTER TABLE chats ADD COLUMN username TEXT",
    "ALTER TABLE chats ADD COLUMN chat_type TEXT",
    "ALTER TABLE chats ADD COLUMN accept INTEGER DEFAULT 1",
]:
    try:
        cur.execute(col_sql)
        conn.commit()
    except Exception:
        pass
conn.commit()

def get_setting(key, default=None):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default

def set_setting(key, value):
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    conn.commit()

def is_admin(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    cur.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return cur.fetchone() is not None

def is_blacklisted(uid: int) -> bool:
    cur.execute("SELECT 1 FROM blacklist WHERE user_id=?", (uid,))
    return cur.fetchone() is not None

def uptime_str() -> str:
    secs = int(time.time() - START_TIME)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUTTON TEXT PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def parse_button_text(text: str):
    lines = text.strip().splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if " || " in line:
            parts = line.split(" || ", 1)
            name = parts[0].strip()
            link = parts[1].strip()
            if name and link.startswith("http"):
                entries.append((name, link, True))
        elif "|" in line:
            parts = line.split("|", 1)
            name = parts[0].strip()
            link = parts[1].strip()
            if name and link.startswith("http"):
                entries.append((name, link, False))

    if not entries:
        return [], ""

    rows_data = []
    i = 0
    while i < len(entries):
        name, link, full = entries[i]
        if full:
            rows_data.append([{"text": name, "url": link}])
            i += 1
        else:
            if i + 1 < len(entries) and not entries[i+1][2]:
                n2, l2, _ = entries[i+1]
                rows_data.append([
                    {"text": name, "url": link},
                    {"text": n2, "url": l2}
                ])
                i += 2
            else:
                rows_data.append([{"text": name, "url": link}])
                i += 1

    btn_list = "\n".join([f"• {e[0]}" for e in entries])
    return rows_data, btn_list

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  KEYBOARD BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def saved_keyboard() -> InlineKeyboardMarkup | None:
    raw = get_setting("welcome_buttons")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        rows = []
        for row in data:
            rows.append([InlineKeyboardButton(**btn) for btn in row])
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    except Exception:
        return None

def dm_keyboard() -> InlineKeyboardMarkup:
    """DM message ke niche buttons"""
    report_link = get_setting("report_link", "https://t.me/TALK_WITH_STEALED")
    episodes_link = get_setting("episodes_link", "")
    rows = []
    row1 = [InlineKeyboardButton(text="🚨 Report Issue", url=report_link)]
    if episodes_link:
        row1.append(InlineKeyboardButton(text="🎬 Latest Episodes", url=episodes_link))
    rows.append(row1)
    rows.append([InlineKeyboardButton(text="📊 Dashboard", url=f"https://t.me/{get_setting('bot_username', 'me')}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WELCOME SEND
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_welcome(chat_id: int, mention: str = None):
    msg_type = get_setting("welcome_type")
    file_id  = get_setting("welcome_file_id")
    caption  = get_setting("welcome_caption", "")
    keyboard = saved_keyboard()
    text = f"👋 {mention}\n{caption}" if mention else caption
    try:
        if msg_type == "video" and file_id:
            await bot.send_video(chat_id=chat_id, video=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "photo" and file_id:
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "animation" and file_id:
            await bot.send_animation(chat_id=chat_id, animation=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif text:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        log.warning(f"send_welcome failed {chat_id}: {e}")

async def send_welcome_autodelete(chat_id: int, mention: str = None):
    """Welcome bhejo aur 15 min baad delete karo"""
    msg_type = get_setting("welcome_type")
    file_id  = get_setting("welcome_file_id")
    caption  = get_setting("welcome_caption", "")
    keyboard = saved_keyboard()
    text = f"👋 {mention}\n{caption}" if mention else caption
    sent = None
    try:
        if msg_type == "video" and file_id:
            sent = await bot.send_video(chat_id=chat_id, video=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "photo" and file_id:
            sent = await bot.send_photo(chat_id=chat_id, photo=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "animation" and file_id:
            sent = await bot.send_animation(chat_id=chat_id, animation=file_id, caption=text or None, reply_markup=keyboard, parse_mode="HTML")
        elif text:
            sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")
        if sent:
            await asyncio.sleep(900)  # 15 min
            try:
                await sent.delete()
            except Exception:
                pass
    except Exception as e:
        log.warning(f"send_welcome_autodelete failed {chat_id}: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SaveFlow(StatesGroup):
    waiting_buttons = State()

class BroadcastFlow(StatesGroup):
    waiting    = State()
    choose_type = State()

class AddButtonFlow(StatesGroup):
    waiting = State()

class SetLinkFlow(StatesGroup):
    report   = State()
    episodes = State()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BOT + DISPATCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(CommandStart())
async def cmd_start(msg: Message):
    welcome_type = get_setting("welcome_type")
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"

    if welcome_type:
        await send_welcome(msg.chat.id)
    else:
        await msg.reply(
            f"⚔️ <b>Satoru Gojo Bot</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Auto-Accept:</b> {st}\n"
            f"➺ <b>Welcome:</b> ❌ Not set\n"
            f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"/help — sab commands dekho",
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHAT ID RESOLVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def resolve_chat_id(raw: str):
    raw = raw.strip()
    if raw.startswith("@"):
        username = raw.lstrip("@")
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, chat.title or "Unknown", chat.username, chat.type.value
        except Exception:
            return None
    elif "t.me/" in raw:
        m = re.search(r"t\.me/([A-Za-z0-9_]+)", raw)
        if not m:
            return None
        username = m.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, chat.title or "Unknown", chat.username, chat.type.value
        except Exception:
            return None
    else:
        try:
            chat_id = int(raw)
        except ValueError:
            return None
        try:
            chat = await bot.get_chat(chat_id)
            return chat.id, chat.title or "Unknown", chat.username, chat.type.value
        except Exception:
            if str(chat_id).startswith("-100"):
                return chat_id, None, None, "unknown"
            return None

async def check_bot_admin(chat_id: int) -> dict:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        status = member.status.value if hasattr(member.status, "value") else str(member.status)
        is_adm = status in ("administrator", "creator")
        can_invite = getattr(member, "can_invite_users", False) if is_adm else False
        return {"is_admin": is_adm, "can_invite": bool(can_invite)}
    except Exception as e:
        log.warning(f"check_bot_admin error: {e}")
        return {"is_admin": False, "can_invite": False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addchannel /addgroup /addchat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addchannel", "addgroup", "addchat"))
async def cmd_add_chat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cmd = msg.text.split()[0].lstrip("/")
    parts = msg.text.split(maxsplit=2)
    custom_name = parts[2].strip() if len(parts) == 3 else None
    args = [parts[0], parts[1]] if len(parts) >= 2 else parts
    if len(args) < 2:
        if cmd == "addchannel":
            return await msg.reply(
                "📢 <b>Channel add karo:</b>\n\n"
                "<code>/addchannel @username</code>\n"
                "<code>/addchannel -100xxxxxxxxxx</code>\n"
                "<code>/addchannel -100xxxxxxxxxx My Channel</code>\n\n"
                "<i>Bot ko pehle channel mein admin banao (Add Members permission).</i>",
                parse_mode="HTML"
            )
        elif cmd == "addgroup":
            return await msg.reply(
                "👥 <b>Group add karo:</b>\n\n"
                "<code>/addgroup @username</code>\n"
                "<code>/addgroup -100xxxxxxxxxx</code>\n\n"
                "<i>Bot ko pehle group mein admin banao (Add Members permission).</i>",
                parse_mode="HTML"
            )
        else:
            return await msg.reply("<code>/addchat @username</code>", parse_mode="HTML")

    if cmd == "addchannel":
        allowed = {"channel"}
        label = "Channel"
    elif cmd == "addgroup":
        allowed = {"group", "supergroup"}
        label = "Group"
    else:
        allowed = {"channel", "group", "supergroup"}
        label = "Chat"

    status_msg = await msg.reply(f"⏳ Checking <code>{args[1]}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(args[1])
    if not result:
        return await status_msg.edit_text("❌ Chat resolve nahi hua. Username/ID check karo.", parse_mode="HTML")

    chat_id, title, username, chat_type = result
    if chat_type == "unknown":
        chat_type = "channel" if cmd == "addchannel" else "supergroup"
        title = custom_name if custom_name else f"Private {label}"
    elif custom_name:
        title = custom_name

    if chat_type not in allowed and cmd != "addchat":
        return await status_msg.edit_text(f"❌ Ye <b>{chat_type}</b> hai, {label} nahi.", parse_mode="HTML")

    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status_msg.edit_text(f"❌ Bot <b>{title}</b> mein admin nahi hai!", parse_mode="HTML")
    if not admin["can_invite"]:
        return await status_msg.edit_text(
            f"⚠️ Bot admin hai but <b>\"Add Members\"</b> permission nahi hai.", parse_mode="HTML"
        )

    cur.execute(
        "INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
        (chat_id, title, username, chat_type)
    )
    conn.commit()
    uname = f"@{username}" if username else "—"
    type_emoji = {"channel": "📢", "supergroup": "👥", "group": "👥"}.get(chat_type, "💬")
    await status_msg.edit_text(
        f"✅ <b>{type_emoji} {label} added!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Title:</b> {title}\n"
        f"➺ <b>Username:</b> {uname}\n"
        f"➺ <b>ID:</b> <code>{chat_id}</code>\n"
        f"➺ <b>Auto-Accept:</b> 🟢 ON",
        parse_mode="HTML"
    )

@dp.message(Command("removechat"))
async def cmd_removechat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply("<code>/removechat @username</code> ya <code>/removechat -100xxx</code>", parse_mode="HTML")
    result = await resolve_chat_id(args[1])
    if not result:
        return await msg.reply("❌ Chat resolve nahi hua.")
    chat_id, title, _, _ = result
    cur.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    await msg.reply(f"🗑 <b>{title}</b> removed.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /setlog
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("setlog"))
async def cmd_setlog(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        cur_log = get_setting("log_channel", "❌ Not set")
        return await msg.reply(
            f"📋 <b>Log Channel:</b> <code>{cur_log}</code>\n\n"
            f"Usage: <code>/setlog -100xxxxxxxxxx</code>",
            parse_mode="HTML"
        )
    try:
        log_id = int(args[1].strip())
    except ValueError:
        return await msg.reply("❌ Valid chat ID do (e.g. -100xxxxxxxxxx)")
    set_setting("log_channel", str(log_id))
    await msg.reply(f"✅ Log channel set: <code>{log_id}</code>", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /save — welcome message
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("save", "setwelcome"))
async def cmd_save(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    r = msg.reply_to_message
    if not r:
        return await msg.reply(
            "↩️ Kisi message ko reply karke /save karo.\n\n"
            "Support: Video, Photo, GIF, Text",
            parse_mode="HTML"
        )
    if r.video:
        set_setting("welcome_type", "video")
        set_setting("welcome_file_id", r.video.file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🎥 Video"
    elif r.animation:
        set_setting("welcome_type", "animation")
        set_setting("welcome_file_id", r.animation.file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🎞 GIF"
    elif r.photo:
        set_setting("welcome_type", "photo")
        set_setting("welcome_file_id", r.photo[-1].file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🖼 Photo"
    elif r.text:
        set_setting("welcome_type", "text")
        set_setting("welcome_file_id", "")
        set_setting("welcome_caption", r.text)
        media_type = "📝 Text"
    else:
        return await msg.reply("❌ Ye message type support nahi hota.")

    existing_btns = get_setting("welcome_buttons")
    existing_note = "\n\n<i>Purane buttons hain. /skip karo unhe rakhne ke liye.</i>" if existing_btns else ""
    await state.set_state(SaveFlow.waiting_buttons)
    await state.update_data(media_type=media_type)
    await msg.reply(
        f"✅ <b>{media_type} saved!</b>\n\n"
        f"🔗 Buttons bhejo:\n"
        f"<code>Button Name | https://link</code>\n\n"
        f"Multiple buttons — ek line mein ek.\n"
        f"Full width ke liye: <code>Name || https://link</code>\n"
        f"Buttons nahi chahiye to /skip karo.{existing_note}",
        parse_mode="HTML"
    )

@dp.message(SaveFlow.waiting_buttons, F.text)
async def save_got_buttons(msg: Message, state: FSMContext):
    data = await state.get_data()
    media_type = data.get("media_type", "")
    if msg.text.strip() == "/skip":
        await state.clear()
        return await msg.reply(f"✅ <b>Welcome saved!</b> Type: {media_type}", parse_mode="HTML")
    rows_data, btn_list = parse_button_text(msg.text)
    if not rows_data:
        return await msg.reply("❌ Format galat hai.\n<code>Name | https://link</code>", parse_mode="HTML")
    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()
    await msg.reply(
        f"✅ <b>Welcome fully saved!</b>\n"
        f"➺ Type: {media_type}\n"
        f"➺ Buttons:\n{btn_list}",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addbutton /clearbuttons /skip
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addbutton"))
async def cmd_addbutton(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    await state.set_state(AddButtonFlow.waiting)
    await msg.reply(
        "🔗 <b>Buttons bhejo:</b>\n"
        "<code>Button Name | https://link</code>\n\n"
        "Multiple — ek line mein ek. Purane replace honge.\n"
        "Full width: <code>Name || https://link</code>",
        parse_mode="HTML"
    )

@dp.message(AddButtonFlow.waiting, F.text)
async def addbutton_done(msg: Message, state: FSMContext):
    rows_data, btn_list = parse_button_text(msg.text)
    if not rows_data:
        return await msg.reply("❌ Format galat hai.\n<code>Name | https://link</code>", parse_mode="HTML")
    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()
    await msg.reply(f"✅ <b>Buttons updated!</b>\n{btn_list}", parse_mode="HTML")

@dp.message(Command("clearbuttons"))
async def cmd_clearbuttons(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    set_setting("welcome_buttons", "")
    await msg.reply("🗑 Sab buttons remove ho gaye.")

@dp.message(Command("skip"))
async def cmd_skip(msg: Message, state: FSMContext):
    await state.clear()
    await msg.reply("↩️ Step skip ho gaya.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /setreportlink /setepisodeslink — custom button links
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("setreportlink"))
async def cmd_setreportlink(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        cur_link = get_setting("report_link", "https://t.me/TALK_WITH_STEALED")
        return await msg.reply(
            f"🚨 <b>Report Issue Link:</b>\n<code>{cur_link}</code>\n\n"
            f"Change karo: <code>/setreportlink https://t.me/yourlink</code>",
            parse_mode="HTML"
        )
    link = args[1].strip()
    if not link.startswith("http"):
        return await msg.reply("❌ Valid URL do (https:// se start karo)")
    set_setting("report_link", link)
    await msg.reply(f"✅ Report Issue link updated!", parse_mode="HTML")

@dp.message(Command("setepisodeslink"))
async def cmd_setepisodeslink(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        cur_link = get_setting("episodes_link", "❌ Not set")
        return await msg.reply(
            f"🎬 <b>Latest Episodes Link:</b>\n<code>{cur_link}</code>\n\n"
            f"Change karo: <code>/setepisodeslink https://t.me/yourlink</code>",
            parse_mode="HTML"
        )
    link = args[1].strip()
    if not link.startswith("http"):
        return await msg.reply("❌ Valid URL do (https:// se start karo)")
    set_setting("episodes_link", link)
    await msg.reply(f"✅ Latest Episodes link updated!", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AUTO-ACCEPT JOIN REQUESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_join_log(user_id: int, first_name: str, username: str, chat_id: int, chat_title: str):
    """Admin log channel mein join notification bhejo"""
    log_ch = get_setting("log_channel")
    if not log_ch:
        return
    try:
        uname_str = f"@{username}" if username else "No username"
        await bot.send_message(
            int(log_ch),
            f"👤 <b>New Join</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Name:</b> {first_name}\n"
            f"➺ <b>Username:</b> {uname_str}\n"
            f"➺ <b>User ID:</b> <code>{user_id}</code>\n"
            f"➺ <b>Chat:</b> {chat_title}\n"
            f"➺ <b>Time:</b> {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            parse_mode="HTML"
        )
    except Exception as e:
        log.warning(f"Join log failed: {e}")

@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    chat_type = req.chat.type.value if hasattr(req.chat.type, "value") else str(req.chat.type)
    title = req.chat.title or str(req.chat.id)
    user_id = req.from_user.id
    first_name = req.from_user.first_name or "User"
    username = req.from_user.username or ""

    cur.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, accept) VALUES (?,?,?,1)",
        (req.chat.id, title, chat_type)
    )
    cur.execute("UPDATE chats SET title=?, chat_type=? WHERE chat_id=?", (title, chat_type, req.chat.id))
    conn.commit()

    # Blacklist check
    if is_blacklisted(user_id):
        try:
            await req.decline()
        except Exception:
            pass
        return

    mode = get_setting("accept_mode", "auto")
    if mode != "auto":
        return

    cur.execute("SELECT accept FROM chats WHERE chat_id=?", (req.chat.id,))
    row = cur.fetchone()
    if row and row[0] == 0:
        return

    try:
        await req.approve()

        # DB mein user save karo
        cur.execute(
            "INSERT OR REPLACE INTO users (user_id, first_name, username) VALUES (?,?,?)",
            (user_id, first_name, username)
        )
        cur.execute(
            "INSERT INTO join_logs (user_id, chat_id, chat_title) VALUES (?,?,?)",
            (user_id, req.chat.id, title)
        )
        conn.commit()

        mention = req.from_user.mention_html()

        # DM to user
        dm_text = (
            f"Hello <b>{first_name}</b>,\n\n"
            f"Your request to join <b>{title}</b> has been approved!!!\n\n"
            f"<b>Please Read:</b>\n"
            f"<i>This bot is an automated system and is not connected with <b>{title}</b>. "
            f"It only manages join requests. Please be cautious of any unauthorized links, "
            f"messages, or requests shared in the channel/group. We are not responsible for "
            f"any misuse, spam, or security issues arising from third-party actions.</i>\n\n"
            f"Click /start to know more.\n\n"
            f"<i>Created By: @TALK_WITH_STEALED</i>"
        )
        try:
            await bot.send_message(user_id, dm_text, parse_mode="HTML", reply_markup=dm_keyboard())
        except Exception as e:
            log.warning(f"DM send failed for {user_id}: {e}")

        # Join log bhejo
        asyncio.create_task(send_join_log(user_id, first_name, username, req.chat.id, title))

        # Welcome sirf group/supergroup mein, 15 min baad delete
        if chat_type in ("group", "supergroup"):
            asyncio.create_task(send_welcome_autodelete(req.chat.id, mention))

    except Exception as e:
        log.warning(f"Join approve failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NEW MEMBER (direct join)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.chat_member()
async def on_new_member(update: ChatMemberUpdated):
    old = update.old_chat_member.status.value if hasattr(update.old_chat_member.status, "value") else str(update.old_chat_member.status)
    new = update.new_chat_member.status.value if hasattr(update.new_chat_member.status, "value") else str(update.new_chat_member.status)
    if old in ("left", "kicked") and new == "member":
        cur.execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, chat_type) VALUES (?,?,?)",
            (update.chat.id, update.chat.title or "", update.chat.type.value if hasattr(update.chat.type, "value") else str(update.chat.type))
        )
        conn.commit()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /autoaccept
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("autoaccept"))
async def cmd_autoaccept(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split()
    if len(args) < 2 or args[1] not in ("on", "off"):
        mode = get_setting("accept_mode", "auto")
        st = "🟢 ON" if mode == "auto" else "🔴 OFF"
        return await msg.reply(f"Auto-accept is <b>{st}</b>\nUsage: /autoaccept on|off", parse_mode="HTML")
    new_mode = "auto" if args[1] == "on" else "manual"
    set_setting("accept_mode", new_mode)
    emoji = "🟢" if new_mode == "auto" else "🔴"
    await msg.reply(f"{emoji} Auto-accept <b>{'ON' if new_mode=='auto' else 'OFF'}</b>.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /chats — per-chat toggle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_chats_keyboard(page: int = 0, filter_type: str = "all"):
    if filter_type == "all":
        cur.execute("SELECT chat_id, title, COALESCE(username,''), COALESCE(chat_type,'channel'), accept FROM chats ORDER BY chat_type, title")
    elif filter_type == "supergroup":
        cur.execute("SELECT chat_id, title, COALESCE(username,''), COALESCE(chat_type,'channel'), accept FROM chats WHERE chat_type IN ('group','supergroup') ORDER BY title")
    else:
        cur.execute("SELECT chat_id, title, COALESCE(username,''), COALESCE(chat_type,'channel'), accept FROM chats WHERE chat_type=? ORDER BY title", (filter_type,))
    rows = cur.fetchall()
    PER_PAGE = 5
    total = len(rows)
    start = page * PER_PAGE
    chunk = rows[start:start + PER_PAGE]

    buttons = []
    type_emoji = {"channel": "📢", "group": "👥", "supergroup": "👥"}
    for chat_id, title, username, chat_type, accept in chunk:
        emoji = type_emoji.get(chat_type, "💬")
        display = title or f"ID:{chat_id}"
        if display.startswith("Private "):
            display = f"ID: {chat_id}"
        display = display[:30]
        status = "🟢" if accept else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {emoji} {display}",
            callback_data=f"chtoggle:{chat_id}:{page}:{filter_type}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"chpage:{page-1}:{filter_type}"))
    if start + PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"chpage:{page+1}:{filter_type}"))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="📢 Channels", callback_data="chfilter:channel:0"),
        InlineKeyboardButton(text="👥 Groups",   callback_data="chfilter:supergroup:0"),
        InlineKeyboardButton(text="🔄 All",      callback_data="chfilter:all:0"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ All ON",  callback_data="chall:on"),
        InlineKeyboardButton(text="❌ All OFF", callback_data="chall:off"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons), total

@dp.message(Command("chats"))
async def cmd_chats(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    kb, total = build_chats_keyboard(0, "all")
    if total == 0:
        return await msg.reply("📋 Koi chat add nahi hai.\n/addchannel ya /addgroup se add karo.", parse_mode="HTML")
    await msg.reply(f"📋 <b>Chat List</b> — {total} total\nToggle karo auto-accept per chat:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("chtoggle:"))
async def cb_chtoggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, chat_id, page, filter_type = cb.data.split(":")
    chat_id, page = int(chat_id), int(page)
    cur.execute("SELECT accept, title FROM chats WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        return await cb.answer("Chat not found.", show_alert=True)
    current, title = row
    new_val = 0 if current else 1
    cur.execute("UPDATE chats SET accept=? WHERE chat_id=?", (new_val, chat_id))
    conn.commit()
    status = "🟢 ON" if new_val else "🔴 OFF"
    await cb.answer(f"{title}: {status}", show_alert=False)
    kb, _ = build_chats_keyboard(page, filter_type)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chpage:"))
async def cb_chpage(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, page, filter_type = cb.data.split(":")
    kb, _ = build_chats_keyboard(int(page), filter_type)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chfilter:"))
async def cb_chfilter(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, filter_type, page = cb.data.split(":")
    kb, _ = build_chats_keyboard(int(page), filter_type)
    await cb.answer(f"Filter: {filter_type}", show_alert=False)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chall:"))
async def cb_chall(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    val = 1 if cb.data.split(":")[1] == "on" else 0
    cur.execute("UPDATE chats SET accept=?", (val,))
    conn.commit()
    label = "🟢 ON" if val else "🔴 OFF"
    await cb.answer(f"Sab chats: {label}", show_alert=True)
    kb, _ = build_chats_keyboard(0, "all")
    await cb.message.edit_reply_markup(reply_markup=kb)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /broadcast — reply karke 3 options
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")

    # Reply to message se direct broadcast option
    if msg.reply_to_message:
        await state.update_data(broadcast_msg_id=msg.reply_to_message.message_id, broadcast_chat_id=msg.chat.id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Normal Broadcast", callback_data="bc:normal")],
            [InlineKeyboardButton(text="📌 Pin Broadcast", callback_data="bc:pin")],
            [InlineKeyboardButton(text="↩️ Forward Tag Broadcast", callback_data="bc:forward")],
        ])
        await msg.reply("📡 <b>Broadcast type choose karo:</b>", reply_markup=kb, parse_mode="HTML")
    else:
        await state.set_state(BroadcastFlow.waiting)
        await msg.reply("📢 Jo message broadcast karna hai wo bhejo.")

@dp.message(BroadcastFlow.waiting)
async def do_broadcast_msg(msg: Message, state: FSMContext):
    await state.update_data(broadcast_msg_id=msg.message_id, broadcast_chat_id=msg.chat.id)
    await state.set_state(BroadcastFlow.choose_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Normal Broadcast", callback_data="bc:normal")],
        [InlineKeyboardButton(text="📌 Pin Broadcast", callback_data="bc:pin")],
        [InlineKeyboardButton(text="↩️ Forward Tag Broadcast", callback_data="bc:forward")],
    ])
    await msg.reply("📡 <b>Broadcast type choose karo:</b>", reply_markup=kb, parse_mode="HTML")

async def _do_broadcast(source_chat_id: int, source_msg_id: int, mode: str, status_msg: Message):
    cur.execute("SELECT chat_id FROM chats")
    chats = cur.fetchall()
    ok, fail = 0, 0
    for (chat_id,) in chats:
        try:
            if mode == "forward":
                await bot.forward_message(chat_id, source_chat_id, source_msg_id)
            else:
                sent = await bot.copy_message(chat_id, source_chat_id, source_msg_id)
                if mode == "pin":
                    try:
                        await bot.pin_chat_message(chat_id, sent.message_id)
                    except Exception:
                        pass
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.5)

    mode_label = {"normal": "Normal", "pin": "Pinned", "forward": "Forward Tag"}.get(mode, mode)
    await status_msg.edit_text(
        f"📢 <b>Broadcast Done! [{mode_label}]</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"✅ Sent: {ok}\n❌ Failed: {fail}\n📊 Total: {ok+fail}",
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("bc:"))
async def cb_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    mode = cb.data.split(":")[1]
    data = await state.get_data()
    source_msg_id = data.get("broadcast_msg_id")
    source_chat_id = data.get("broadcast_chat_id")
    await state.clear()
    if not source_msg_id:
        return await cb.answer("❌ Message nahi mila.", show_alert=True)
    await cb.answer(f"Broadcasting [{mode}]...", show_alert=False)
    status = await cb.message.edit_text("⏳ Broadcasting...", parse_mode="HTML")
    asyncio.create_task(_do_broadcast(source_chat_id, source_msg_id, mode, status))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /fbroadcast /pinbroadcast
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("fbroadcast"))
async def cmd_fbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="forward")
        return await msg.reply("↩️ Jo message forward karna hai usse reply karke /fbroadcast karo.")
    status = await msg.reply("⏳ Forward broadcasting...")
    asyncio.create_task(_do_broadcast(msg.chat.id, msg.reply_to_message.message_id, "forward", status))

@dp.message(Command("pinbroadcast"))
async def cmd_pinbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="pin")
        return await msg.reply("↩️ Jo message pin karke broadcast karna hai usse reply karke /pinbroadcast karo.")
    status = await msg.reply("⏳ Pin broadcasting...")
    asyncio.create_task(_do_broadcast(msg.chat.id, msg.reply_to_message.message_id, "pin", status))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ADMIN MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addadmin"))
async def cmd_addadmin(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    if not msg.reply_to_message:
        return await msg.reply("↩️ Reply to a user's message.")
    uid = msg.reply_to_message.from_user.id
    uname = msg.reply_to_message.from_user.full_name
    cur.execute("INSERT OR IGNORE INTO admins VALUES (?)", (uid,))
    conn.commit()
    await msg.reply(f"✅ <b>{uname}</b> added as admin.", parse_mode="HTML")

@dp.message(Command("removeadmin"))
async def cmd_removeadmin(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    if not msg.reply_to_message:
        return await msg.reply("↩️ Reply to a user's message.")
    uid = msg.reply_to_message.from_user.id
    uname = msg.reply_to_message.from_user.full_name
    cur.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    conn.commit()
    await msg.reply(f"🗑 <b>{uname}</b> removed from admins.", parse_mode="HTML")

@dp.message(Command("admins"))
async def cmd_admins(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT user_id FROM admins")
    rows = cur.fetchall()
    if not rows:
        return await msg.reply(f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\nKoi extra admin nahi.", parse_mode="HTML")
    lines = [f"• <code>{r[0]}</code>" for r in rows]
    await msg.reply(
        f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n\n"
        f"🛡 <b>Admins:</b>\n" + "\n".join(lines),
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BLACKLIST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("blacklist"))
async def cmd_blacklist(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        cur.execute("SELECT user_id FROM blacklist")
        rows = cur.fetchall()
        if not rows:
            return await msg.reply("🚫 Blacklist empty hai.")
        lines = [f"• <code>{r[0]}</code>" for r in rows]
        return await msg.reply("🚫 <b>Blacklisted Users:</b>\n" + "\n".join(lines), parse_mode="HTML")
    try:
        uid = int(args[1].strip())
    except ValueError:
        return await msg.reply("❌ Valid user ID do.")
    cur.execute("INSERT OR IGNORE INTO blacklist VALUES (?)", (uid,))
    conn.commit()
    await msg.reply(f"🚫 User <code>{uid}</code> blacklisted.", parse_mode="HTML")

@dp.message(Command("unblacklist"))
async def cmd_unblacklist(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply("Usage: <code>/unblacklist USER_ID</code>", parse_mode="HTML")
    try:
        uid = int(args[1].strip())
    except ValueError:
        return await msg.reply("❌ Valid user ID do.")
    cur.execute("DELETE FROM blacklist WHERE user_id=?", (uid,))
    conn.commit()
    await msg.reply(f"✅ User <code>{uid}</code> blacklist se remove.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  EXPORT USERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("exportusers"))
async def cmd_exportusers(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT user_id, first_name, username, joined_at FROM users ORDER BY joined_at DESC")
    rows = cur.fetchall()
    if not rows:
        return await msg.reply("📂 Koi user nahi hai abhi.")
    lines = ["user_id,first_name,username,joined_at"]
    for r in rows:
        lines.append(f"{r[0]},{r[1]},{r[2] or ''},{r[3]}")
    content = "\n".join(lines)
    with open("/tmp/users_export.csv", "w") as f:
        f.write(content)
    await bot.send_document(
        msg.chat.id,
        FSInputFile("/tmp/users_export.csv"),
        caption=f"📊 Total Users: {len(rows)}"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DATABASE BACKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("backup"))
async def cmd_backup(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    backup_path = f"/tmp/satoru_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2("satoru.db", backup_path)
    await bot.send_document(
        msg.chat.id,
        FSInputFile(backup_path),
        caption=f"💾 Database Backup\n📅 {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    )

async def auto_daily_backup():
    """Roz raat 12 baje backup owner ko bhejo"""
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_secs = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_secs)
        try:
            backup_path = f"/tmp/satoru_daily_{datetime.now().strftime('%Y%m%d')}.db"
            shutil.copy2("satoru.db", backup_path)
            await bot.send_document(
                OWNER_ID,
                FSInputFile(backup_path),
                caption=f"💾 Auto Daily Backup\n📅 {datetime.now().strftime('%d %b %Y')}"
            )
            log.info("Daily backup sent to owner.")
        except Exception as e:
            log.error(f"Daily backup failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /id  /ping  /stats  /help
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("id"))
async def cmd_id(msg: Message):
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private":
        await msg.reply(f"🆔 <b>Your ID:</b> <code>{user.id}</code>", parse_mode="HTML")
    else:
        chat_type = chat.type.value if hasattr(chat.type, "value") else str(chat.type)
        uname = f"@{chat.username}" if chat.username else "Private"
        await msg.reply(
            f"🆔 <b>Chat Info</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Title:</b> {chat.title}\n"
            f"➺ <b>ID:</b> <code>{chat.id}</code>\n"
            f"➺ <b>Type:</b> {chat_type}\n"
            f"➺ <b>Username:</b> {uname}",
            parse_mode="HTML"
        )

@dp.message(Command("ping"))
async def cmd_ping(msg: Message):
    t = time.time()
    m = await msg.reply("🏓 Pinging...")
    ms = round((time.time() - t) * 1000, 2)
    await m.edit_text(
        f"🏓 <b>PONG!</b>\n━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>PING:</b> 🏓 {ms} ms\n➺ <b>UPTIME:</b> ⏳ {uptime_str()}",
        parse_mode="HTML"
    )

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT COUNT(*) FROM chats")
    total_chats = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins")
    total_admins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM join_logs WHERE date(joined_at)=date('now')")
    today_joins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM join_logs")
    total_joins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM blacklist")
    bl_count = cur.fetchone()[0]
    mode  = get_setting("accept_mode", "auto")
    st    = "🟢 ON" if mode == "auto" else "🔴 OFF"
    wtype = get_setting("welcome_type", "❌ Not set")
    log_ch = get_setting("log_channel", "❌ Not set")
    await msg.reply(
        f"📊 <b>Bot Stats</b>\n━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Chats:</b> {total_chats}\n"
        f"➺ <b>Admins:</b> {total_admins}\n"
        f"➺ <b>Total Users:</b> {total_users}\n"
        f"➺ <b>Today Joins:</b> {today_joins}\n"
        f"➺ <b>Total Joins:</b> {total_joins}\n"
        f"➺ <b>Blacklist:</b> {bl_count}\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome Type:</b> {wtype}\n"
        f"➺ <b>Log Channel:</b> <code>{log_ch}</code>\n"
        f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.reply(
        "<b>⚔️ Satoru Gojo Bot — Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📋 Setup</b>\n"
        "/addchannel — Channel add karo\n"
        "/addgroup — Group add karo\n"
        "/removechat — Chat remove karo\n"
        "/chats — Per-chat toggle\n"
        "/autoaccept on|off — Global toggle\n\n"
        "<b>👋 Welcome</b>\n"
        "/save — Reply karke welcome set karo\n"
        "/setwelcome — Same as /save\n"
        "/addbutton — Buttons add karo\n"
        "/clearbuttons — Buttons hatao\n"
        "/setlog — Log channel set karo\n"
        "/setreportlink — Report Issue button link\n"
        "/setepisodeslink — Latest Episodes button link\n\n"
        "<b>📢 Broadcast</b>\n"
        "/broadcast — Reply karke 3 options\n"
        "/fbroadcast — Forward tag ke saath\n"
        "/pinbroadcast — Broadcast + pin\n\n"
        "<b>👥 Admin</b>\n"
        "/addadmin — Admin add (reply)\n"
        "/removeadmin — Admin remove (reply)\n"
        "/admins — Admin list\n\n"
        "<b>🚫 User Management</b>\n"
        "/blacklist [user_id] — Blacklist dekho/add karo\n"
        "/unblacklist [user_id] — Remove from blacklist\n"
        "/exportusers — CSV export\n"
        "/backup — DB backup\n\n"
        "<b>🛡 Group Moderation</b>\n"
        "/ban /kick /mute /unmute\n"
        "/warn /unwarn /pin /unpin /purge\n"
        "/antilink on|off\n\n"
        "<b>ℹ️ Other</b>\n"
        "/stats — Daily & total joins\n"
        "/ping — Latency check\n"
        "/id — Chat/user ID\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GROUP MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_warns(user_id, chat_id):
    cur.execute("SELECT count FROM warnings WHERE user_id=? AND chat_id=?", (user_id, chat_id))
    row = cur.fetchone()
    return row[0] if row else 0

def add_warn(user_id, chat_id):
    cur.execute("INSERT OR IGNORE INTO warnings (user_id, chat_id, count) VALUES (?,?,0)", (user_id, chat_id))
    cur.execute("UPDATE warnings SET count=count+1 WHERE user_id=? AND chat_id=?", (user_id, chat_id))
    conn.commit()
    return get_warns(user_id, chat_id)

def reset_warns(user_id, chat_id):
    cur.execute("DELETE FROM warnings WHERE user_id=? AND chat_id=?", (user_id, chat_id))
    conn.commit()

async def is_group_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status.value in ("administrator", "creator")
    except Exception:
        return False

@dp.message(Command("ban"))
async def cmd_ban(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins ban kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /ban likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(f"🚫 <b>{target.from_user.mention_html()}</b> banned.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Ban nahi ho saka: {e}")

@dp.message(Command("kick"))
async def cmd_kick(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins kick kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /kick likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await bot.unban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(f"👢 <b>{target.from_user.mention_html()}</b> kicked.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Kick nahi ho saka: {e}")

@dp.message(Command("mute"))
async def cmd_mute(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins mute kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /mute likho.")
    args = msg.text.split()
    duration = None
    if len(args) > 1:
        try:
            duration = int(args[1])
        except ValueError:
            pass
    until = datetime.now() + timedelta(minutes=duration) if duration else None
    try:
        await bot.restrict_chat_member(
            msg.chat.id, target.from_user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        dur_text = f" {duration} minute ke liye" if duration else " permanently"
        await msg.reply(f"🔇 <b>{target.from_user.mention_html()}</b> ko{dur_text} mute.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Mute nahi ho saka: {e}")

@dp.message(Command("unmute"))
async def cmd_unmute(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unmute kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /unmute likho.")
    try:
        await bot.restrict_chat_member(
            msg.chat.id, target.from_user.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True
            )
        )
        await msg.reply(f"🔊 <b>{target.from_user.mention_html()}</b> unmuted.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Unmute nahi ho saka: {e}")

@dp.message(Command("warn"))
async def cmd_warn(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins warn kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /warn likho.")
    count = add_warn(target.from_user.id, msg.chat.id)
    if count >= 3:
        try:
            await bot.ban_chat_member(msg.chat.id, target.from_user.id)
            reset_warns(target.from_user.id, msg.chat.id)
            await msg.reply(f"🚫 <b>{target.from_user.mention_html()}</b> ko 3 warnings — auto-ban!", parse_mode="HTML")
        except Exception as e:
            await msg.reply(f"⚠️ 3 warnings but ban nahi ho saka: {e}")
    else:
        await msg.reply(f"⚠️ <b>{target.from_user.mention_html()}</b> warning #{count}/3.", parse_mode="HTML")

@dp.message(Command("unwarn"))
async def cmd_unwarn(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unwarn kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /unwarn likho.")
    reset_warns(target.from_user.id, msg.chat.id)
    await msg.reply(f"✅ <b>{target.from_user.mention_html()}</b> warnings reset.", parse_mode="HTML")

@dp.message(Command("pin"))
async def cmd_pin(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins pin kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /pin likho.")
    try:
        await bot.pin_chat_message(msg.chat.id, target.message_id, disable_notification=False)
        await msg.reply("📌 Message pin ho gaya.")
    except Exception as e:
        await msg.reply(f"❌ Pin nahi ho saka: {e}")

@dp.message(Command("unpin"))
async def cmd_unpin(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unpin kar sakte hain.")
    try:
        await bot.unpin_chat_message(msg.chat.id)
        await msg.reply("📌 Message unpin ho gaya.")
    except Exception as e:
        await msg.reply(f"❌ Unpin nahi ho saka: {e}")

@dp.message(Command("purge"))
async def cmd_purge(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins purge kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /purge likho.")
    deleted = 0
    try:
        for mid in range(target.message_id, msg.message_id + 1):
            try:
                await bot.delete_message(msg.chat.id, mid)
                deleted += 1
            except Exception:
                pass
        status = await msg.answer(f"🗑 <b>{deleted} messages delete ho gaye.</b>", parse_mode="HTML")
        await asyncio.sleep(3)
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        await msg.reply(f"❌ Purge nahi ho saka: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ANTI-LINK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(F.text & F.chat.type.in_({"group", "supergroup"}))
async def anti_link_filter(msg: Message):
    antilink = get_setting("antilink", "off")
    if antilink != "on":
        return
    if re.search(r"(https?://|t\.me/|@\w+)", msg.text or ""):
        if await is_group_admin(bot, msg.chat.id, msg.from_user.id) or is_admin(msg.from_user.id):
            return
        try:
            await msg.delete()
            warn_msg = await msg.answer(f"🚫 <b>{msg.from_user.mention_html()}</b> links allowed nahi hain!", parse_mode="HTML")
            await asyncio.sleep(5)
            await warn_msg.delete()
        except Exception:
            pass

@dp.message(Command("antilink"))
async def cmd_antilink(msg: Message):
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins use kar sakte hain.")
    args = msg.text.split()
    if len(args) < 2 or args[1] not in ("on", "off"):
        status = get_setting("antilink", "off")
        st = "🟢 ON" if status == "on" else "🔴 OFF"
        return await msg.reply(f"Anti-link abhi: <b>{st}</b>\nUsage: /antilink on|off", parse_mode="HTML")
    set_setting("antilink", args[1])
    await msg.reply(f"Anti-link <b>{'🟢 ON' if args[1]=='on' else '🔴 OFF'}</b>.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main():
    log.info("⚔️ Satoru Gojo Bot starting...")
    asyncio.create_task(auto_daily_backup())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
