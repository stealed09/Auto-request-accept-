import asyncio
import logging
import sqlite3
import time
import json

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, ChatJoinRequest, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton
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
API_ID     = 0          # my.telegram.org se lo
API_HASH   = ""         # my.telegram.org se lo

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
    chat_type TEXT,
    accept    INTEGER DEFAULT 1
);
""")
# Add accept column if upgrading old DB
try:
    cur.execute("ALTER TABLE chats ADD COLUMN accept INTEGER DEFAULT 1")
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

def uptime_str() -> str:
    secs = int(time.time() - START_TIME)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUTTON TEXT PARSER
#  Normal line  → 2 per row
#  ^Name^ line  → full width (1 per row)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def parse_button_text(text: str):
    """
    Parse button input into rows_data.
    Returns (rows_data, btn_list_str)

    Syntax:
      Name | https://link     → 2 per row (normal)
      Name || https://link    → full width (alone on its row)
    """
    lines = text.strip().splitlines()
    entries = []  # list of (name, url, full_width)

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
            # peek next
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SEND WELCOME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_welcome(chat_id: int, mention: str = None):
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
#  PYROGRAM — extract buttons from forwarded message
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def extract_pyro_buttons(pyro_msg: PyroMessage) -> list:
    """Extract inline URL buttons from a Pyrogram message."""
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
#  STATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SaveFlow(StatesGroup):
    waiting_buttons = State()   # fallback if pyro gets no buttons

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

# Global pyrogram client (None until logged in)
pyro_app: PyroClient | None = None

async def get_pyro_client() -> PyroClient | None:
    global pyro_app
    api_id   = get_setting("pyro_api_id")
    api_hash = get_setting("pyro_api_hash")
    if not api_id or not api_hash:
        return None
    if pyro_app is None or not pyro_app.is_connected:
        try:
            pyro_app = PyroClient(
                "userbot_session",
                api_id=int(api_id),
                api_hash=api_hash
            )
            await pyro_app.start()
        except Exception as e:
            log.warning(f"Pyrogram start failed: {e}")
            pyro_app = None
    return pyro_app

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(CommandStart())
async def cmd_start(msg: Message):
    welcome_type = get_setting("welcome_type")
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"

    if welcome_type:
        # Send actual welcome preview
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
#  CHAT ID RESOLVER (add channel/group manually)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def resolve_chat_id(raw: str):
    """Resolve @username, t.me link, or -100xxx → (chat_id, title, username, chat_type)"""
    raw = raw.strip()
    if raw.startswith("@"):
        username = raw.lstrip("@")
    elif "t.me/" in raw:
        import re
        m = re.search(r"t\.me/([A-Za-z0-9_]+)", raw)
        if not m: return None
        username = m.group(1)
    else:
        # Try as numeric ID
        try:
            chat = await bot.get_chat(int(raw))
            return chat.id, chat.title or "Unknown", chat.username, chat.type
        except Exception:
            return None
    try:
        chat = await bot.get_chat(f"@{username}")
        return chat.id, chat.title or "Unknown", chat.username, chat.type
    except Exception:
        return None

async def check_bot_admin(chat_id: int) -> dict:
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        is_admin = member.status in ("administrator", "creator")
        can_invite = getattr(member, "can_invite_users", False) if is_admin else False
        return {"is_admin": is_admin, "can_invite": can_invite}
    except Exception:
        return {"is_admin": False, "can_invite": False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addchannel  /addgroup  /addchat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addchannel", "addgroup", "addchat"))
async def cmd_add_chat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cmd = msg.text.split()[0].lstrip("/")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        if cmd == "addchannel":
            return await msg.reply(
                "📢 <b>Channel add karo:</b>\n\n"
                "<code>/addchannel @username</code>\n"
                "<code>/addchannel https://t.me/username</code>\n"
                "<code>/addchannel -100xxxxxxxxxx</code>\n\n"
                "<i>Bot ko pehle channel mein admin banao (Add Members permission).</i>",
                parse_mode="HTML"
            )
        elif cmd == "addgroup":
            return await msg.reply(
                "👥 <b>Group add karo:</b>\n\n"
                "<code>/addgroup @username</code>\n"
                "<code>/addgroup https://t.me/username</code>\n"
                "<code>/addgroup -100xxxxxxxxxx</code>",
                parse_mode="HTML"
            )
        else:
            return await msg.reply(
                "💬 <b>Chat add karo:</b>\n\n"
                "<code>/addchat @username</code>",
                parse_mode="HTML"
            )

    if cmd == "addchannel":
        allowed = {"channel"}
        label, emoji = "Channel", "📢"
    elif cmd == "addgroup":
        allowed = {"group", "supergroup"}
        label, emoji = "Group", "👥"
    else:
        allowed = {"channel", "group", "supergroup"}
        label, emoji = "Chat", "💬"

    status = await msg.reply(f"⏳ Checking <code>{args[1]}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(args[1])
    if not result:
        return await status.edit_text("❌ Chat resolve nahi hua. Username/ID check karo.", parse_mode="HTML")
    chat_id, title, username, chat_type = result
    if chat_type not in allowed:
        return await status.edit_text(f"❌ Ye <b>{chat_type}</b> hai, {label} nahi.", parse_mode="HTML")

    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status.edit_text(f"❌ Bot <b>{title}</b> mein admin nahi hai! Pehle admin banao.", parse_mode="HTML")
    if not admin["can_invite"]:
        return await status.edit_text(
            f"⚠️ Bot admin hai but <b>\"Add Members\"</b> permission nahi hai <b>{title}</b> mein.",
            parse_mode="HTML"
        )

    cur.execute("""INSERT OR REPLACE INTO chats
                   (chat_id, title, username, chat_type, accept)
                   VALUES (?,?,?,?,1)""",
                (chat_id, title, username, chat_type))
    conn.commit()
    uname = f"@{username}" if username else "—"
    await status.edit_text(
        f"✅ <b>{emoji} {label} added!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Title:</b> {title}\n"
        f"➺ <b>Username:</b> {uname}\n"
        f"➺ <b>ID:</b> <code>{chat_id}</code>\n"
        f"➺ <b>Type:</b> {chat_type}\n"
        f"➺ <b>Admin:</b> ✅ (Add Members ✓)\n"
        f"➺ <b>Auto-Accept:</b> 🟢 ON",
        parse_mode="HTML"
    )

@dp.message(Command("removechat"))
async def cmd_removechat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply(
            "🗑 <b>Remove chat:</b>\n"
            "<code>/removechat @username</code> ya <code>/removechat -100xxx</code>",
            parse_mode="HTML"
        )
    result = await resolve_chat_id(args[1])
    if not result:
        return await msg.reply("❌ Chat resolve nahi hua.")
    chat_id, title, _, _ = result
    cur.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    await msg.reply(f"🗑 <b>{title}</b> removed.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /setlog — owner sets log channel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("setlog"))
async def cmd_setlog(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    args = msg.text.split()
    if len(args) < 2:
        current = get_setting("log_channel", "❌ Not set")
        return await msg.reply(
            f"📌 <b>Log Channel</b>\n"
            f"Current: <code>{current}</code>\n\n"
            f"Usage: <code>/setlog -100xxxxxxxxx</code>\n\n"
            f"<i>Bot ko us channel mein admin banao, phir koi bhi message forward karo wahan — welcome auto-save ho jayega!</i>",
            parse_mode="HTML"
        )
    channel_id = args[1].strip()
    set_setting("log_channel", channel_id)
    await msg.reply(
        f"✅ Log channel set: <code>{channel_id}</code>\n\n"
        f"Ab us channel mein koi bhi message forward karo — video + caption + buttons automatically save ho jayenge!",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOG CHANNEL — auto save forwarded message
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.channel_post()
async def on_channel_post(msg: Message):
    log_channel = get_setting("log_channel")
    if not log_channel:
        return
    try:
        if str(msg.chat.id) != str(log_channel):
            return
    except Exception:
        return

    # Only process forwarded messages
    if not (msg.forward_from_chat or msg.forward_from or msg.forward_sender_name):
        return

    # Save media
    if msg.video:
        set_setting("welcome_type",    "video")
        set_setting("welcome_file_id", msg.video.file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🎬 Video"
    elif msg.animation:
        set_setting("welcome_type",    "animation")
        set_setting("welcome_file_id", msg.animation.file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🎞 GIF"
    elif msg.photo:
        set_setting("welcome_type",    "photo")
        set_setting("welcome_file_id", msg.photo[-1].file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🖼 Photo"
    elif msg.text:
        set_setting("welcome_type",    "text")
        set_setting("welcome_file_id", "")
        set_setting("welcome_caption", msg.text)
        media_type = "📝 Text"
    else:
        return

    # Try to get buttons via Pyrogram (if source chat available)
    buttons_saved = False
    session_str = get_setting("pyro_session")
    api_id      = get_setting("pyro_api_id")
    api_hash    = get_setting("pyro_api_hash")

    if session_str and api_id and api_hash and msg.forward_from_chat:
        try:
            async with PyroClient(
                "temp_session",
                api_id=int(api_id),
                api_hash=api_hash,
                session_string=session_str
            ) as app:
                src_chat   = msg.forward_from_chat.id
                src_msg_id = msg.forward_from_message_id
                if src_msg_id:
                    orig = await app.get_messages(src_chat, src_msg_id)
                    rows_data = extract_pyro_buttons(orig)
                    if rows_data:
                        set_setting("welcome_buttons", json.dumps(rows_data))
                        buttons_saved = True
        except Exception as e:
            log.warning(f"Pyrogram button fetch in log channel failed: {e}")

    btn_status = "✅ Auto-fetched!" if buttons_saved else "⚠️ Purane buttons rakhe (source restricted)"

    # Notify owner
    try:
        await bot.send_message(
            OWNER_ID,
            f"✅ <b>Welcome message auto-saved!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ Type: {media_type}\n"
            f"➺ Buttons: {btn_status}\n\n"
            f"/start karo preview ke liye.",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /login — owner sets up userbot
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("login"))
async def cmd_login(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply("⚠️ Only owner can do this.")
    await state.set_state(LoginFlow.waiting_api_id)
    await msg.reply(
        "🔐 <b>Userbot Login</b>\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "Step 1/3 — Apna <b>API ID</b> bhejo\n\n"
        "<i>my.telegram.org pe jaao → App Configuration → API ID</i>",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.waiting_api_id, F.text)
async def login_api_id(msg: Message, state: FSMContext):
    if not msg.text.strip().isdigit():
        return await msg.reply("❌ API ID sirf numbers hota hai. Dobara bhejo.")
    await state.update_data(api_id=msg.text.strip())
    await state.set_state(LoginFlow.waiting_api_hash)
    await msg.reply(
        "✅ API ID saved!\n\n"
        "Step 2/3 — Apna <b>API Hash</b> bhejo",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.waiting_api_hash, F.text)
async def login_api_hash(msg: Message, state: FSMContext):
    await state.update_data(api_hash=msg.text.strip())
    await state.set_state(LoginFlow.waiting_phone)
    await msg.reply(
        "✅ API Hash saved!\n\n"
        "Step 3/3 — Apna <b>Phone Number</b> bhejo\n"
        "Format: <code>+91XXXXXXXXXX</code>",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.waiting_phone, F.text)
async def login_phone(msg: Message, state: FSMContext):
    global pyro_app
    data     = await state.get_data()
    api_id   = int(data["api_id"])
    api_hash = data["api_hash"]
    phone    = msg.text.strip()

    await msg.reply("⏳ OTP bhej raha hoon...")

    try:
        pyro_app = PyroClient(
            "userbot_session",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True
        )
        await pyro_app.connect()
        sent = await pyro_app.send_code(phone)
        await state.update_data(
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            phone_code_hash=sent.phone_code_hash
        )
        await state.set_state(LoginFlow.waiting_code)
        await msg.reply(
            "📱 OTP aaya hoga Telegram pe!\n\n"
            "OTP bhejo (spaces ke saath):\n"
            "Example: <code>1 2 3 4 5</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await state.clear()
        pyro_app = None
        await msg.reply(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.waiting_code, F.text)
async def login_code(msg: Message, state: FSMContext):
    global pyro_app
    data  = await state.get_data()
    code  = msg.text.strip().replace(" ", "")
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]

    try:
        await pyro_app.sign_in(phone, phone_code_hash, code)
        # Save credentials
        set_setting("pyro_api_id",   str(data["api_id"]))
        set_setting("pyro_api_hash", data["api_hash"])
        # Export session string
        session_str = await pyro_app.export_session_string()
        set_setting("pyro_session", session_str)
        await state.clear()
        await msg.reply(
            "✅ <b>Userbot login successful!</b>\n\n"
            "Ab forwarded message ke buttons bhi automatically save honge! 🔥",
            parse_mode="HTML"
        )
    except Exception as e:
        err = str(e)
        if "two-steps" in err.lower() or "password" in err.lower() or "2fa" in err.lower():
            await state.set_state(LoginFlow.waiting_2fa)
            await msg.reply(
                "🔒 2FA enabled hai!\n\n"
                "Apna <b>2FA Password</b> bhejo:",
                parse_mode="HTML"
            )
        else:
            await state.clear()
            pyro_app = None
            await msg.reply(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.waiting_2fa, F.text)
async def login_2fa(msg: Message, state: FSMContext):
    global pyro_app
    data = await state.get_data()
    try:
        await pyro_app.check_password(msg.text.strip())
        set_setting("pyro_api_id",   str(data["api_id"]))
        set_setting("pyro_api_hash", data["api_hash"])
        session_str = await pyro_app.export_session_string()
        set_setting("pyro_session", session_str)
        await state.clear()
        await msg.reply(
            "✅ <b>2FA verified! Userbot login successful!</b>\n\n"
            "Ab forwarded message ke buttons bhi automatically save honge! 🔥",
            parse_mode="HTML"
        )
    except Exception as e:
        await state.clear()
        pyro_app = None
        await msg.reply(f"❌ 2FA Error: <code>{e}</code>", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /save — reply to forwarded msg, buttons via pyrogram
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("save"))
async def cmd_save(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")

    r = msg.reply_to_message
    if not r:
        return await msg.reply(
            "↩️ <b>Kisi bhi message ko reply karke /save likho.</b>",
            parse_mode="HTML"
        )

    # Save media
    if r.video:
        set_setting("welcome_type",    "video")
        set_setting("welcome_file_id", r.video.file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🎬 Video"
    elif r.animation:
        set_setting("welcome_type",    "animation")
        set_setting("welcome_file_id", r.animation.file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🎞 GIF"
    elif r.photo:
        set_setting("welcome_type",    "photo")
        set_setting("welcome_file_id", r.photo[-1].file_id)
        set_setting("welcome_caption", r.caption or "")
        media_type = "🖼 Photo"
    elif r.text:
        set_setting("welcome_type",    "text")
        set_setting("welcome_file_id", "")
        set_setting("welcome_caption", r.text)
        media_type = "📝 Text"
    else:
        return await msg.reply("❌ Ye message type support nahi hota.")

    # Try to get buttons via Pyrogram
    buttons_saved = False
    session_str = get_setting("pyro_session")
    api_id      = get_setting("pyro_api_id")
    api_hash    = get_setting("pyro_api_hash")

    if session_str and api_id and api_hash and r.forward_from_chat:
        try:
            async with PyroClient(
                "temp_session",
                api_id=int(api_id),
                api_hash=api_hash,
                session_string=session_str
            ) as app:
                # Fetch original message from source chat
                src_chat = r.forward_from_chat.id
                src_msg_id = r.forward_from_message_id
                if src_msg_id:
                    orig = await app.get_messages(src_chat, src_msg_id)
                    rows_data = extract_pyro_buttons(orig)
                    if rows_data:
                        set_setting("welcome_buttons", json.dumps(rows_data))
                        buttons_saved = True
        except Exception as e:
            log.warning(f"Pyrogram button fetch failed: {e}")

    if buttons_saved:
        await msg.reply(
            f"✅ <b>Welcome message saved!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ Type: {media_type}\n"
            f"➺ Buttons: ✅ Auto-fetched!\n\n"
            f"/start karo preview dekhne ke liye.",
            parse_mode="HTML"
        )
    else:
        # Fallback — ask manually
        existing_btns = get_setting("welcome_buttons")
        existing_note = "\n\n<i>Purane buttons hain. /skip karo unhe rakhne ke liye.</i>" if existing_btns else ""
        await state.set_state(SaveFlow.waiting_buttons)
        await state.update_data(media_type=media_type)
        await msg.reply(
            f"✅ <b>{media_type} saved!</b>\n\n"
            f"🔗 Buttons bhejo:\n"
            f"<code>Button Name | https://link</code>\n\n"
            f"Multiple buttons — ek line mein ek.\n"
            f"Buttons nahi chahiye to /skip karo.{existing_note}",
            parse_mode="HTML"
        )

@dp.message(SaveFlow.waiting_buttons, F.text)
async def save_got_buttons(msg: Message, state: FSMContext):
    data = await state.get_data()
    media_type = data.get("media_type", "")

    if msg.text.strip() == "/skip":
        await state.clear()
        return await msg.reply(
            f"✅ <b>Welcome message saved!</b>\n"
            f"➺ Type: {media_type}\n"
            f"➺ Buttons: Purane wale rakhe\n\n"
            f"/start karo preview ke liye.",
            parse_mode="HTML"
        )

    rows_data, btn_list = parse_button_text(msg.text)

    if not rows_data:
        return await msg.reply(
            "❌ Format galat hai.\n<code>Name | https://link</code>",
            parse_mode="HTML"
        )

    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()

    await msg.reply(
        f"✅ <b>Welcome message fully saved!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ Type: {media_type}\n"
        f"➺ Buttons:\n{btn_list}\n\n"
        f"/start karo preview ke liye.",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addbutton /clearbuttons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addbutton"))
async def cmd_addbutton(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    await state.set_state(AddButtonFlow.waiting)
    await msg.reply(
        "🔗 <b>Buttons bhejo:</b>\n"
        "<code>Button Name | https://link</code>\n\n"
        "Multiple — ek line mein ek. Purane replace honge.",
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /logout
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
#  AUTO-ACCEPT JOIN REQUESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    cur.execute("INSERT OR IGNORE INTO chats VALUES (?,?,1)",
                (req.chat.id, req.chat.title or ""))
    conn.commit()
    # Global toggle check
    mode = get_setting("accept_mode", "auto")
    if mode != "auto":
        return
    # Per-chat toggle check
    cur.execute("SELECT accept FROM chats WHERE chat_id=?", (req.chat.id,))
    row = cur.fetchone()
    if row and row[0] == 0:
        return
    try:
        await req.approve()
        mention = req.from_user.mention_html()
        try:
            await send_welcome(req.from_user.id, mention)
        except Exception:
            pass
        await send_welcome(req.chat.id, mention)
    except Exception as e:
        log.warning(f"Join approve failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NEW MEMBER (direct join)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.chat_member()
async def on_new_member(update: ChatMemberUpdated):
    old = update.old_chat_member.status
    new = update.new_chat_member.status
    if old in ("left", "kicked") and new == "member":
        cur.execute("INSERT OR IGNORE INTO chats VALUES (?,?)",
                    (update.chat.id, update.chat.title or ""))
        conn.commit()
        mention = update.new_chat_member.user.mention_html()
        await send_welcome(update.chat.id, mention)

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
        return await msg.reply(
            f"➺ Auto-accept is <b>{st}</b>\nUsage: /autoaccept on|off",
            parse_mode="HTML"
        )
    new_mode = "auto" if args[1] == "on" else "manual"
    set_setting("accept_mode", new_mode)
    emoji = "🟢" if new_mode == "auto" else "🔴"
    await msg.reply(f"{emoji} Auto-accept <b>{'ON' if new_mode=='auto' else 'OFF'}</b>.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /chats — per-chat auto-accept toggle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram.types import CallbackQuery

def build_chats_keyboard(page: int = 0, filter_type: str = "all"):
    if filter_type == "all":
        cur.execute("SELECT chat_id, title, COALESCE(username,''), COALESCE(chat_type,'channel'), accept FROM chats ORDER BY chat_type, title")
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
        name = (title or str(chat_id))[:24]
        status = "🟢" if accept else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status} {emoji} {name}",
                callback_data=f"chtoggle:{chat_id}:{page}:{filter_type}"
            )
        ])

    # Pagination
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"chpage:{page-1}:{filter_type}"))
    if start + PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"chpage:{page+1}:{filter_type}"))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton(text="📢 Channels", callback_data=f"chfilter:channel:0"),
        InlineKeyboardButton(text="👥 Groups", callback_data=f"chfilter:group:0"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ All ON", callback_data="chall:on"),
        InlineKeyboardButton(text="❌ All OFF", callback_data="chall:off"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons), total

@dp.message(Command("chats"))
async def cmd_chats(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    kb, total = build_chats_keyboard(0, "all")
    if total == 0:
        return await msg.reply(
            "📋 <b>Koi chat add nahi hai.</b>\n\n"
            "/addchannel ya /addgroup se add karo.",
            parse_mode="HTML"
        )
    cur.execute("SELECT chat_type, COUNT(*) FROM chats GROUP BY chat_type")
    breakdown = cur.fetchall()
    bd = " | ".join([f"{t}: {c}" for t, c in breakdown])
    await msg.reply(
        f"📋 <b>Chat List</b> — <i>{total} total ({bd})</i>\n"
        f"Toggle karo auto-accept per chat:",
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
    kb, _ = build_chats_keyboard(0)
    await cb.message.edit_reply_markup(reply_markup=kb)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /broadcast
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    await state.set_state(BroadcastFlow.waiting)
    await msg.reply("📢 Jo message broadcast karna hai wo bhejo.")

@dp.message(BroadcastFlow.waiting)
async def do_broadcast(msg: Message, state: FSMContext):
    await state.clear()
    cur.execute("SELECT chat_id FROM chats")
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
    cur.execute("SELECT COUNT(*) FROM admins")
    total_admins = cur.fetchone()[0]
    mode  = get_setting("accept_mode", "auto")
    st    = "🟢 ON" if mode == "auto" else "🔴 OFF"
    wtype = get_setting("welcome_type", "❌ Not set")
    sess     = "✅ Active" if get_setting("pyro_session") else "❌ Not logged in"
    log_ch   = get_setting("log_channel", "❌ Not set")
    await msg.reply(
        f"📊 <b>Bot Stats</b>\n━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Chats:</b> {total_chats}\n"
        f"➺ <b>Admins:</b> {total_admins}\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome Type:</b> {wtype}\n"
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
        "/start — Welcome message\n"
        "/ping — Ping + uptime\n\n"
        "🛡 <b>Admin Only</b>\n"
        "/save — Message reply karke welcome set karo\n"
        "/addbutton — Sirf buttons update karo\n"
        "/clearbuttons — Buttons hatao\n"
        "/autoaccept on|off — Global join request toggle\n"
        "/broadcast — Sab chats mein message bhejo\n"
        "/stats — Bot stats\n"
        "/admins — Admin list\n\n"
        "👑 <b>Owner Only</b>\n"
        "/setlog — Log channel set karo (auto-save)\n"
        "/login — Userbot login (buttons auto-fetch)\n"
        "/logout — Userbot logout\n"
        "/addadmin — Reply → admin banao\n"
        "/removeadmin — Reply → admin hatao\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "✨ Bot ko group/channel mein <b>Admin</b> banao\n"
        "with <b>Add Members</b> permission.",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main():
    log.info("⚔️ Satoru Gojo Bot starting...")
    # Try to restore pyrogram session on startup
    session_str = get_setting("pyro_session")
    api_id      = get_setting("pyro_api_id")
    api_hash    = get_setting("pyro_api_hash")
    if session_str and api_id and api_hash:
        try:
            global pyro_app
            pyro_app = PyroClient(
                "userbot_session",
                api_id=int(api_id),
                api_hash=api_hash,
                session_string=session_str
            )
            await pyro_app.start()
            log.info("✅ Pyrogram userbot connected!")
        except Exception as e:
            log.warning(f"Pyrogram restore failed: {e}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
