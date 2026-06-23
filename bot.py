import asyncio
import logging
import sqlite3
import time
import json
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, ChatJoinRequest, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from pyrogram import Client as PyroClient
from pyrogram.types import Message as PyroMessage

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN  = "YOUR_BOT_TOKEN_HERE"
OWNER_ID   = 123456789
API_ID     = 0
API_HASH   = ""

START_TIME = time.time()
logging.basicConfig(level=logging.INFO)
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
    chat_type TEXT,           -- 'channel' | 'group' | 'supergroup'
    accept    INTEGER DEFAULT 1,
    added_at  INTEGER
);
""")
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

def uptime_str() -> str:
    secs = int(time.time() - START_TIME)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHAT ID PARSER
#  Accepts: @username, https://t.me/username, -100xxxxxxxxx
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def resolve_chat(bot: Bot, raw: str):
    """
    Resolve a chat identifier into (chat_id, title, username, chat_type).
    Returns None on failure.
    """
    raw = raw.strip()
    # Already numeric ID like -1001234567890
    if re.fullmatch(r"-?\d{8,}", raw):
        try:
            chat = await bot.get_chat(int(raw))
            return chat.id, chat.title or "Unknown", chat.username, chat.type
        except Exception as e:
            log.warning(f"Numeric ID resolve failed: {e}")
            return None

    # @username
    if raw.startswith("@"):
        username = raw.lstrip("@")
    # t.me link
    elif "t.me/" in raw:
        m = re.search(r"t\.me/([A-Za-z0-9_]+)", raw)
        if not m:
            return None
        username = m.group(1)
    else:
        return None

    try:
        chat = await bot.get_chat(f"@{username}")
        return chat.id, chat.title or "Unknown", chat.username, chat.type
    except Exception as e:
        log.warning(f"Username resolve failed: {e}")
        return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHECK ADMIN STATUS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_bot_admin(bot: Bot, chat_id: int) -> dict:
    """
    Returns dict with: is_admin (bool), can_invite (bool), raw_member
    """
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        is_admin = member.status in ("administrator", "creator")
        can_invite = False
        if is_admin:
            # 'can_invite_users' exists in ChatMemberAdministrator
            can_invite = getattr(member, "can_invite_users", False)
        return {
            "is_admin": is_admin,
            "can_invite": can_invite,
            "status": member.status
        }
    except Exception as e:
        log.warning(f"get_chat_member failed for {chat_id}: {e}")
        return {"is_admin": False, "can_invite": False, "status": "unknown"}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUTTON TEXT PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def parse_button_text(text: str):
    """
    Returns (rows_data, btn_list_str)
    Name | https://link      → 2 per row
    Name || https://link     → full width
    """
    lines = text.strip().splitlines()
    entries = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if " || " in line:
            parts = line.split(" || ", 1)
            name, link = parts[0].strip(), parts[1].strip()
            if name and link.startswith("http"):
                entries.append((name, link, True))
        elif "|" in line:
            parts = line.split("|", 1)
            name, link = parts[0].strip(), parts[1].strip()
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WELCOME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_welcome(bot: Bot, chat_id: int, mention: str = None):
    msg_type = get_setting("welcome_type")
    file_id  = get_setting("welcome_file_id")
    caption  = get_setting("welcome_caption", "")
    keyboard = saved_keyboard()
    text = f"👋 {mention}\n{caption}" if mention else caption
    try:
        if msg_type == "video" and file_id:
            await bot.send_video(chat_id=chat_id, video=file_id,
                                 caption=text or None,
                                 reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "photo" and file_id:
            await bot.send_photo(chat_id=chat_id, photo=file_id,
                                 caption=text or None,
                                 reply_markup=keyboard, parse_mode="HTML")
        elif msg_type == "animation" and file_id:
            await bot.send_animation(chat_id=chat_id, animation=file_id,
                                     caption=text or None,
                                     reply_markup=keyboard, parse_mode="HTML")
        elif text:
            await bot.send_message(chat_id=chat_id, text=text,
                                   reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        log.warning(f"send_welcome failed {chat_id}: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AddChatFlow(StatesGroup):
    waiting = State()

class SaveFlow(StatesGroup):
    waiting_buttons = State()

class BroadcastFlow(StatesGroup):
    waiting = State()

class AddButtonFlow(StatesGroup):
    waiting = State()

class LoginFlow(StatesGroup):
    waiting_api_id   = State()
    waiting_api_hash = State()
    waiting_phone    = State()
    waiting_code     = State()
    waiting_2fa      = State()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BOT + DISPATCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

pyro_app: PyroClient | None = None

def extract_pyro_buttons(pyro_msg: PyroMessage) -> list:
    rows_data = []
    if not pyro_msg.reply_markup:
        return rows_data
    try:
        for row in pyro_msg.reply_markup.inline_keyboard:
            row_data = []
            for btn in row:
                if btn.url:
                    row_data.append({"text": btn.text, "url": btn.url})
            if row_data:
                rows_data.append(row_data)
    except Exception:
        pass
    return rows_data

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(CommandStart())
async def cmd_start(msg: Message):
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"
    cur.execute("SELECT COUNT(*) FROM chats")
    total = cur.fetchone()[0]
    wtype = get_setting("welcome_type", "❌ Not set")
    await msg.reply(
        f"⚔️ <b>Satoru Gojo Bot</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome:</b> {wtype}\n"
        f"➺ <b>Chats:</b> {total} added\n"
        f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"<b>Quick Start:</b>\n"
        f"1️⃣ /addchannel ya /addgroup se chats add karo\n"
        f"2️⃣ Bot ko admin banao (Add Members)\n"
        f"3️⃣ /chats se sab manage karo\n"
        f"/help — sab commands",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addchannel  &  /addgroup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addchannel", "addgroup", "addchat"))
async def cmd_add_chat(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")

    cmd = msg.text.split()[0].lstrip("/")
    if cmd == "addchannel":
        kind_label = "Channel"
        kind_emoji = "📢"
        allowed_types = {"channel"}
    elif cmd == "addgroup":
        kind_label = "Group"
        kind_emoji = "👥"
        allowed_types = {"group", "supergroup"}
    else:
        kind_label = "Chat"
        kind_emoji = "💬"
        allowed_types = {"channel", "group", "supergroup"}

    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await state.set_state(AddChatFlow.waiting)
        await state.update_data(kind_label=kind_label,
                                kind_emoji=kind_emoji,
                                allowed_types=",".join(allowed_types))
        return await msg.reply(
            f"{kind_emoji} <b>{kind_label} add karo</b>\n\n"
            f"Bhejo:\n"
            f"• Channel/Group <b>ID</b>: <code>-100xxxxxxxxxx</code>\n"
            f"• <b>@username</b>\n"
            f"• Ya <b>t.me link</b>\n\n"
            f"Bot ko pehle wahan admin banao (Add Members + Invite via Link).",
            parse_mode="HTML"
        )

    await process_add_chat(msg, args[1].strip(), kind_label, kind_emoji, allowed_types)

async def process_add_chat(msg: Message, raw: str, kind_label: str,
                           kind_emoji: str, allowed_types: set):
    status_msg = await msg.reply(f"⏳ Checking <code>{raw}</code>...", parse_mode="HTML")

    result = await resolve_chat(bot, raw)
    if not result:
        return await status_msg.edit_text(
            f"❌ Chat resolve nahi hua.\n\n"
            f"Check karo:\n"
            f"• Username sahi hai\n"
            f"• Bot ko wahan add kiya hai\n"
            f"• Channel/Group exist karta hai",
            parse_mode="HTML"
        )

    chat_id, title, username, chat_type = result

    if chat_type not in allowed_types:
        return await status_msg.edit_text(
            f"❌ Ye <b>{chat_type}</b> hai, {kind_label} nahi.\n\n"
            f"Titile: {title}\n"
            f"Sahi command use karo.",
            parse_mode="HTML"
        )

    # Check bot admin status
    admin_info = await check_bot_admin(bot, chat_id)
    if not admin_info["is_admin"]:
        return await status_msg.edit_text(
            f"❌ Bot <b>{title}</b> mein admin nahi hai!\n\n"
            f"Pehle bot ko admin banao, phir dobara add karo.\n\n"
            f"Chat ID: <code>{chat_id}</code>",
            parse_mode="HTML"
        )

    if not admin_info["can_invite"]:
        return await status_msg.edit_text(
            f"⚠️ Bot admin hai <b>{title}</b> mein, but\n"
            f"<b>\"Add Members\" / \"Invite Users\"</b> permission nahi hai.\n\n"
            f"Admin settings mein ye permission ON karo, phir retry.",
            parse_mode="HTML"
        )

    # Save to DB
    cur.execute("""INSERT OR REPLACE INTO chats
                   (chat_id, title, username, chat_type, accept, added_at)
                   VALUES (?,?,?,?,?,?)""",
                (chat_id, title, username, chat_type, 1, int(time.time())))
    conn.commit()

    uname_disp = f"@{username}" if username else "—"
    await status_msg.edit_text(
        f"✅ <b>{kind_emoji} {kind_label} added!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Title:</b> {title}\n"
        f"➺ <b>Username:</b> {uname_disp}\n"
        f"➺ <b>ID:</b> <code>{chat_id}</code>\n"
        f"➺ <b>Type:</b> {chat_type}\n"
        f"➺ <b>Admin:</b> ✅ (Add Members ✓)\n"
        f"➺ <b>Auto-Accept:</b> 🟢 ON\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n\n"
        f"Ab yahan join requests auto-approve hongi!",
        parse_mode="HTML"
    )

@dp.message(AddChatFlow.waiting, F.text)
async def addchat_got(msg: Message, state: FSMContext):
    if msg.text.startswith("/"):
        await state.clear()
        return
    data = await state.get_data()
    kind_label = data.get("kind_label", "Chat")
    kind_emoji = data.get("kind_emoji", "💬")
    allowed = set(data.get("allowed_types", "").split(","))
    await state.clear()
    await process_add_chat(msg, msg.text.strip(), kind_label, kind_emoji, allowed)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /removechat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("removechat", "remove"))
async def cmd_removechat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply(
            "🗑 <b>Remove chat:</b>\n"
            "<code>/removechat -100xxxxxxxxxx</code>\n"
            "ya <code>/removechat @username</code>",
            parse_mode="HTML"
        )
    result = await resolve_chat(bot, args[1].strip())
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
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    args = msg.text.split()
    if len(args) < 2:
        current = get_setting("log_channel", "❌ Not set")
        return await msg.reply(
            f"📌 <b>Log Channel</b>\nCurrent: <code>{current}</code>\n\n"
            f"Usage: <code>/setlog -100xxxxxxxxx</code>",
            parse_mode="HTML"
        )
    set_setting("log_channel", args[1].strip())
    await msg.reply(f"✅ Log channel set: <code>{args[1]}</code>", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOG CHANNEL AUTO-SAVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.channel_post()
async def on_channel_post(msg: Message):
    log_channel = get_setting("log_channel")
    if not log_channel or str(msg.chat.id) != str(log_channel):
        return
    if not (msg.forward_from_chat or msg.forward_from or msg.forward_sender_name):
        return
    if msg.video:
        set_setting("welcome_type", "video")
        set_setting("welcome_file_id", msg.video.file_id)
        set_setting("welcome_caption", msg.caption or "")
    elif msg.animation:
        set_setting("welcome_type", "animation")
        set_setting("welcome_file_id", msg.animation.file_id)
        set_setting("welcome_caption", msg.caption or "")
    elif msg.photo:
        set_setting("welcome_type", "photo")
        set_setting("welcome_file_id", msg.photo[-1].file_id)
        set_setting("welcome_caption", msg.caption or "")
    elif msg.text:
        set_setting("welcome_type", "text")
        set_setting("welcome_file_id", "")
        set_setting("welcome_caption", msg.text)
    else:
        return
    try:
        await bot.send_message(OWNER_ID, "✅ Welcome auto-saved from log channel!", parse_mode="HTML")
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  USERBOT LOGIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("login"))
async def cmd_login(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    await state.set_state(LoginFlow.waiting_api_id)
    await msg.reply("🔐 Step 1/3 — Apna <b>API ID</b> bhejo\n<i>my.telegram.org se</i>", parse_mode="HTML")

@dp.message(LoginFlow.waiting_api_id, F.text)
async def login_api_id(msg: Message, state: FSMContext):
    if not msg.text.strip().isdigit():
        return await msg.reply("❌ API ID sirf numbers hota hai.")
    await state.update_data(api_id=msg.text.strip())
    await state.set_state(LoginFlow.waiting_api_hash)
    await msg.reply("✅ Step 2/3 — <b>API Hash</b> bhejo", parse_mode="HTML")

@dp.message(LoginFlow.waiting_api_hash, F.text)
async def login_api_hash(msg: Message, state: FSMContext):
    await state.update_data(api_hash=msg.text.strip())
    await state.set_state(LoginFlow.waiting_phone)
    await msg.reply("✅ Step 3/3 — Phone number bhejo (<code>+91XXXXXXXXXX</code>)", parse_mode="HTML")

@dp.message(LoginFlow.waiting_phone, F.text)
async def login_phone(msg: Message, state: FSMContext):
    global pyro_app
    data = await state.get_data()
    api_id = int(data["api_id"])
    api_hash = data["api_hash"]
    phone = msg.text.strip()
    await msg.reply("⏳ OTP bhej raha hoon...")
    try:
        pyro_app = PyroClient("userbot_session", api_id=api_id,
                              api_hash=api_hash, in_memory=True)
        await pyro_app.connect()
        sent = await pyro_app.send_code(phone)
        await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash)
        await state.set_state(LoginFlow.waiting_code)
        await msg.reply("📱 OTP bhejo (<code>1 2 3 4 5</code>):", parse_mode="HTML")
    except Exception as e:
        await state.clear()
        await msg.reply(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.waiting_code, F.text)
async def login_code(msg: Message, state: FSMContext):
    global pyro_app
    data = await state.get_data()
    code = msg.text.strip().replace(" ", "")
    try:
        await pyro_app.sign_in(data["phone"], data["phone_code_hash"], code)
        set_setting("pyro_api_id", str(data["api_id"]))
        set_setting("pyro_api_hash", data["api_hash"])
        set_setting("pyro_session", await pyro_app.export_session_string())
        await state.clear()
        await msg.reply("✅ Userbot login successful! 🔥", parse_mode="HTML")
    except Exception as e:
        if "password" in str(e).lower() or "2fa" in str(e).lower():
            await state.set_state(LoginFlow.waiting_2fa)
            await msg.reply("🔒 2FA password bhejo:", parse_mode="HTML")
        else:
            await state.clear()
            await msg.reply(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.waiting_2fa, F.text)
async def login_2fa(msg: Message, state: FSMContext):
    global pyro_app
    data = await state.get_data()
    try:
        await pyro_app.check_password(msg.text.strip())
        set_setting("pyro_api_id", str(data["api_id"]))
        set_setting("pyro_api_hash", data["api_hash"])
        set_setting("pyro_session", await pyro_app.export_session_string())
        await state.clear()
        await msg.reply("✅ 2FA verified! Login successful!", parse_mode="HTML")
    except Exception as e:
        await state.clear()
        await msg.reply(f"❌ 2FA Error: <code>{e}</code>", parse_mode="HTML")

@dp.message(Command("logout"))
async def cmd_logout(msg: Message):
    global pyro_app
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    set_setting("pyro_session", "")
    set_setting("pyro_api_id", "")
    set_setting("pyro_api_hash", "")
    if pyro_app and pyro_app.is_connected:
        await pyro_app.stop()
    pyro_app = None
    await msg.reply("✅ Userbot logged out.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /save
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("save"))
async def cmd_save(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    r = msg.reply_to_message
    if not r:
        return await msg.reply("↩️ Message pe reply karke /save karo.", parse_mode="HTML")
    if r.video:
        set_setting("welcome_type", "video"); set_setting("welcome_file_id", r.video.file_id); set_setting("welcome_caption", r.caption or ""); media_type = "🎬 Video"
    elif r.animation:
        set_setting("welcome_type", "animation"); set_setting("welcome_file_id", r.animation.file_id); set_setting("welcome_caption", r.caption or ""); media_type = "🎞 GIF"
    elif r.photo:
        set_setting("welcome_type", "photo"); set_setting("welcome_file_id", r.photo[-1].file_id); set_setting("welcome_caption", r.caption or ""); media_type = "🖼 Photo"
    elif r.text:
        set_setting("welcome_type", "text"); set_setting("welcome_file_id", ""); set_setting("welcome_caption", r.text); media_type = "📝 Text"
    else:
        return await msg.reply("❌ Ye type support nahi hota.")
    await state.set_state(SaveFlow.waiting_buttons)
    await state.update_data(media_type=media_type)
    await msg.reply(
        f"✅ <b>{media_type} saved!</b>\n\n"
        f"🔗 Buttons bhejo:\n<code>Name | https://link</code>\n"
        f"Ya /skip karo.",
        parse_mode="HTML"
    )

@dp.message(SaveFlow.waiting_buttons, F.text)
async def save_got_buttons(msg: Message, state: FSMContext):
    data = await state.get_data()
    media_type = data.get("media_type", "")
    if msg.text.strip() == "/skip":
        await state.clear()
        return await msg.reply(f"✅ Saved! Type: {media_type}, buttons: purane rakhe.", parse_mode="HTML")
    rows_data, btn_list = parse_button_text(msg.text)
    if not rows_data:
        return await msg.reply("❌ Format galat.\n<code>Name | https://link</code>", parse_mode="HTML")
    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()
    await msg.reply(f"✅ <b>Welcome fully saved!</b>\n{btn_list}", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addbutton  /clearbuttons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addbutton"))
async def cmd_addbutton(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    await state.set_state(AddButtonFlow.waiting)
    await msg.reply("🔗 <b>Buttons bhejo:</b>\n<code>Name | https://link</code>", parse_mode="HTML")

@dp.message(AddButtonFlow.waiting, F.text)
async def addbutton_done(msg: Message, state: FSMContext):
    rows_data, btn_list = parse_button_text(msg.text)
    if not rows_data:
        return await msg.reply("❌ Format galat.", parse_mode="HTML")
    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()
    await msg.reply(f"✅ <b>Buttons updated!</b>\n{btn_list}", parse_mode="HTML")

@dp.message(Command("clearbuttons"))
async def cmd_clearbuttons(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    set_setting("welcome_buttons", "")
    await msg.reply("🗑 Sab buttons remove.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AUTO-ACCEPT (only for chats in DB)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    cur.execute("SELECT accept FROM chats WHERE chat_id=?", (req.chat.id,))
    row = cur.fetchone()
    if not row:
        return  # not in our list — ignore
    if row[0] == 0:
        return
    mode = get_setting("accept_mode", "auto")
    if mode != "auto":
        return
    try:
        await req.approve()
        mention = req.from_user.mention_html()
        try:
            await send_welcome(bot, req.from_user.id, mention)
        except Exception:
            pass
        await send_welcome(bot, req.chat.id, mention)
    except Exception as e:
        log.warning(f"Join approve failed: {e}")

@dp.chat_member()
async def on_new_member(update: ChatMemberUpdated):
    old = update.old_chat_member.status
    new = update.new_chat_member.status
    if old in ("left", "kicked") and new == "member":
        cur.execute("SELECT 1 FROM chats WHERE chat_id=?", (update.chat.id,))
        if not cur.fetchone():
            return
        mention = update.new_chat_member.user.mention_html()
        await send_welcome(bot, update.chat.id, mention)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /autoaccept (global)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("autoaccept"))
async def cmd_autoaccept(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split()
    if len(args) < 2 or args[1] not in ("on", "off"):
        mode = get_setting("accept_mode", "auto")
        st = "🟢 ON" if mode == "auto" else "🔴 OFF"
        return await msg.reply(f"➺ Auto-accept: <b>{st}</b>\nUsage: /autoaccept on|off", parse_mode="HTML")
    new_mode = "auto" if args[1] == "on" else "manual"
    set_setting("accept_mode", new_mode)
    emoji = "🟢" if new_mode == "auto" else "🔴"
    await msg.reply(f"{emoji} Auto-accept <b>{'ON' if new_mode=='auto' else 'OFF'}</b>.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /chats — list with toggle buttons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_chats_keyboard(filter_type: str = "all", page: int = 0):
    if filter_type == "all":
        cur.execute("SELECT chat_id, title, username, chat_type, accept FROM chats ORDER BY chat_type, title")
    else:
        cur.execute("SELECT chat_id, title, username, chat_type, accept FROM chats WHERE chat_type=? ORDER BY title", (filter_type,))
    rows = cur.fetchall()
    PER_PAGE = 5
    total = len(rows)
    start = page * PER_PAGE
    chunk = rows[start:start + PER_PAGE]

    buttons = []
    type_emoji = {"channel": "📢", "group": "👥", "supergroup": "👥"}
    for chat_id, title, username, chat_type, accept in chunk:
        emoji = type_emoji.get(chat_type, "💬")
        name = (title or str(chat_id))[:26]
        status = "🟢" if accept else "🔴"
        buttons.append([
            InlineKeyboardButton(text=f"{status} {emoji} {name}", callback_data=f"chtoggle:{chat_id}:{page}:{filter_type}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"chpage:{page-1}:{filter_type}"))
    if start + PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"chpage:{page+1}:{filter_type}"))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="📢 Channels", callback_data="chfilter:channel:0"),
        InlineKeyboardButton(text="👥 Groups", callback_data="chfilter:group:0"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ All ON", callback_data="chall:on:all"),
        InlineKeyboardButton(text="❌ All OFF", callback_data="chall:off:all"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔄 Refresh Admin Check", callback_data="chrefresh:0:all"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons), total

@dp.message(Command("chats"))
async def cmd_chats(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    kb, total = build_chats_keyboard("all", 0)
    if total == 0:
        return await msg.reply(
            "📋 <b>Koi chat add nahi hai abhi.</b>\n\n"
            "/addchannel ya /addgroup se add karo.",
            parse_mode="HTML"
        )
    cur.execute("SELECT chat_type, COUNT(*) FROM chats GROUP BY chat_type")
    breakdown = cur.fetchall()
    breakdown_str = " | ".join([f"{t}: {c}" for t, c in breakdown])
    await msg.reply(
        f"📋 <b>Chat List</b> — <i>{total} total ({breakdown_str})</i>\n"
        f"Toggle auto-accept per chat:",
        reply_markup=kb,
        parse_mode="HTML"
    )

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
    kb, _ = build_chats_keyboard(filter_type, page)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chpage:"))
async def cb_chpage(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, page, filter_type = cb.data.split(":")
    kb, _ = build_chats_keyboard(filter_type, int(page))
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chfilter:"))
async def cb_chfilter(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, filter_type, page = cb.data.split(":")
    kb, total = build_chats_keyboard(filter_type, int(page))
    await cb.answer(f"Filter: {filter_type}", show_alert=False)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chall:"))
async def cb_chall(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    _, val, _ = cb.data.split(":")
    v = 1 if val == "on" else 0
    cur.execute("UPDATE chats SET accept=?", (v,))
    conn.commit()
    label = "🟢 ON" if v else "🔴 OFF"
    await cb.answer(f"Sab chats: {label}", show_alert=True)
    kb, _ = build_chats_keyboard("all", 0)
    await cb.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("chrefresh:"))
async def cb_chrefresh(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    await cb.answer("🔄 Admin status check ho raha hai...", show_alert=False)
    cur.execute("SELECT chat_id, title FROM chats")
    rows = cur.fetchall()
    issues = []
    for chat_id, title in rows:
        info = await check_bot_admin(bot, chat_id)
        if not info["is_admin"]:
            issues.append(f"❌ <b>{title}</b> — admin nahi")
        elif not info["can_invite"]:
            issues.append(f"⚠️ <b>{title}</b> — Add Members permission missing")
    if not issues:
        await cb.message.answer("✅ Sab chats mein bot admin hai with proper permissions!", parse_mode="HTML")
    else:
        await cb.message.answer(
            "🔄 <b>Admin Check Results:</b>\n\n" + "\n".join(issues),
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /broadcast
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    await state.set_state(BroadcastFlow.waiting)
    await msg.reply("📢 Broadcast karne ka message bhejo.")

@dp.message(BroadcastFlow.waiting)
async def do_broadcast(msg: Message, state: FSMContext):
    await state.clear()
    cur.execute("SELECT chat_id FROM chats WHERE accept=1")
    chats = cur.fetchall()
    ok, fail = 0, 0
    for (chat_id,) in chats:
        try:
            await msg.copy_to(chat_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.reply(f"📢 <b>Broadcast done!</b>\n✅ Sent: {ok}\n❌ Failed: {fail}", parse_mode="HTML")

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
    await msg.reply(f"✅ <b>{uname}</b> admin banaya.", parse_mode="HTML")

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
    await msg.reply(f"🗑 <b>{uname}</b> admin hataya.", parse_mode="HTML")

@dp.message(Command("admins"))
async def cmd_admins(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT user_id FROM admins")
    rows = cur.fetchall()
    lines = [f"👑 Owner: <code>{OWNER_ID}</code>"]
    lines += [f"🛡 <code>{r[0]}</code>" for r in rows]
    await msg.reply("\n".join(lines), parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /ping  /stats  /help
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    cur.execute("SELECT COUNT(*) FROM chats WHERE chat_type='channel'")
    channels = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM chats WHERE chat_type IN ('group','supergroup')")
    groups = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins")
    total_admins = cur.fetchone()[0]
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"
    wtype = get_setting("welcome_type", "❌ Not set")
    sess = "✅ Active" if get_setting("pyro_session") else "❌ Not logged in"
    log_ch = get_setting("log_channel", "❌ Not set")
    await msg.reply(
        f"📊 <b>Bot Stats</b>\n━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Total Chats:</b> {total_chats}\n"
        f"➺ <b>Channels:</b> {channels} | <b>Groups:</b> {groups}\n"
        f"➺ <b>Admins:</b> {total_admins}\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome:</b> {wtype}\n"
        f"➺ <b>Userbot:</b> {sess}\n"
        f"➺ <b>Log Channel:</b> <code>{log_ch}</code>\n"
        f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.reply(
        "⚔️ <b>Bot Commands</b>\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "📌 <b>Chat Management (Admin)</b>\n"
        "/addchannel — Channel add karo (ID/username/link)\n"
        "/addgroup — Group add karo\n"
        "/addchat — Auto-detect channel/group\n"
        "/removechat — Chat remove karo\n"
        "/chats — Sab chats ki list with toggles\n\n"
        "🌀 <b>General</b>\n"
        "/start — Status\n"
        "/ping — Ping + uptime\n\n"
        "🛡 <b>Admin Only</b>\n"
        "/save — Reply se welcome set karo\n"
        "/addbutton — Buttons update karo\n"
        "/clearbuttons — Buttons hatao\n"
        "/autoaccept on|off — Global toggle\n"
        "/broadcast — Sab chats mein message\n"
        "/stats — Bot stats\n"
        "/admins — Admin list\n\n"
        "👑 <b>Owner Only</b>\n"
        "/setlog — Log channel set\n"
        "/login — Userbot login\n"
        "/logout — Userbot logout\n"
        "/addadmin — Reply → admin banao\n"
        "/removeadmin — Reply → admin hatao\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "💡 <b>Tip:</b> Pehle bot ko apne channels/groups mein admin banao (Add Members permission), phir /addchannel ya /addgroup se add karo.",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main():
    log.info("⚔️ Satoru Gojo Bot starting...")
    session_str = get_setting("pyro_session")
    api_id      = get_setting("pyro_api_id")
    api_hash    = get_setting("pyro_api_hash")
    if session_str and api_id and api_hash:
        try:
            global pyro_app
            pyro_app = PyroClient("userbot_session", api_id=int(api_id),
                                  api_hash=api_hash, session_string=session_str)
            await pyro_app.start()
            log.info("✅ Pyrogram userbot connected!")
        except Exception as e:
            log.warning(f"Pyrogram restore failed: {e}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
