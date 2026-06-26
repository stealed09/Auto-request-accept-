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

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneCodeInvalidError, PhoneCodeExpiredError,
    SessionPasswordNeededError, PasswordHashInvalidError
)

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
CREATE TABLE IF NOT EXISTS tg_sessions (
    user_id  INTEGER PRIMARY KEY,
    api_id   TEXT,
    api_hash TEXT,
    phone    TEXT,
    session  TEXT
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
            await asyncio.sleep(900)
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
    waiting     = State()
    choose_type = State()

class AddButtonFlow(StatesGroup):
    waiting = State()

class SetLinkFlow(StatesGroup):
    report   = State()
    episodes = State()

class LoginFlow(StatesGroup):
    api_id   = State()
    api_hash = State()
    phone    = State()
    otp      = State()
    password = State()

class AddChatFlow(StatesGroup):
    addchannel = State()
    addgroup   = State()
    removechat = State()
    setlog     = State()

class AdminFlow(StatesGroup):
    addadmin    = State()
    removeadmin = State()

class BlacklistFlow(StatesGroup):
    add    = State()
    remove = State()

class SetLinkFlowBtn(StatesGroup):
    report   = State()
    episodes = State()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BOT + DISPATCHER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# Active Telethon clients (login flow ke dauran memory mein)
_login_clients: dict[int, TelegramClient] = {}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACK_KB = lambda page="setup": InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔙 Back", callback_data=f"menu:{page}")]
])

def main_menu_text():
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"
    return (
        f"『 ⚔️ 』<b>SATORU GOJO BOT</b>\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"\n"
        f"┌ 🤖 <b>Status</b>  ➜  <code>Online</code>\n"
        f"├ ⚡ <b>Auto-Accept</b>  ➜  {st}\n"
        f"├ ⏱️ <b>Uptime</b>  ➜  <code>{uptime_str()}</code>\n"
        f"└ 👁️ <b>Mode</b>  ➜  <code>Infinity</code>\n"
        f"\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"<i>「 The Strongest Bot is Here 」</i>\n"
        f"\n"
        f"𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗽𝗮𝗻𝗲𝗹 𝗯𝗲𝗹𝗼𝘄 👇"
    )

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚙️ • S E T U P •", callback_data="menu:setup"),
            InlineKeyboardButton(text="🌸 • W E L C O M E •", callback_data="menu:welcome"),
        ],
        [
            InlineKeyboardButton(text="📡 • B R O A D C A S T •", callback_data="menu:broadcast"),
            InlineKeyboardButton(text="🔑 • S E S S I O N •", callback_data="menu:session"),
        ],
        [
            InlineKeyboardButton(text="👑 • A D M I N •", callback_data="menu:admin"),
            InlineKeyboardButton(text="🧹 • U S E R S •", callback_data="menu:usermgmt"),
        ],
        [
            InlineKeyboardButton(text="🛡️ • M O D •", callback_data="menu:moderation"),
            InlineKeyboardButton(text="💠 • O T H E R •", callback_data="menu:other"),
        ],
    ])

async def show_menu_page(target, page: str):
    """Edit a message OR send a new one with the menu page."""
    if page == "main":
        text = main_menu_text()
        kb   = main_menu_keyboard()
    elif page in MENU_PAGES:
        data = MENU_PAGES[page]
        # Dynamic pages rebuild buttons (e.g. chats list)
        if callable(data.get("build")):
            buttons = data["build"]()
        else:
            buttons = data["buttons"]
        # Session page ka title login status ke saath
        if page == "session":
            cur.execute("SELECT phone FROM tg_sessions LIMIT 1")
            row = cur.fetchone()
            if row:
                text = (
                    "『 🔑 』<b>SESSION PANEL</b>\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"🟢 <b>Connected:</b> <code>{row[0]}</code>\n"
                    "<i>Account connected hai — channels/groups fetch karo</i>"
                )
            else:
                text = (
                    "『 🔑 』<b>SESSION PANEL</b>\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    "🔴 <b>Not Connected</b>\n"
                    "<i>Login karo Telethon session activate karne ke liye</i>"
                )
        else:
            text = data["title"]
        kb   = InlineKeyboardMarkup(inline_keyboard=buttons)
    else:
        return

    if hasattr(target, "edit_text"):
        try:
            await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    # Fallback: send new
    chat_id = target.chat.id if hasattr(target, "chat") else target
    await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")

def build_setup_buttons():
    cur.execute("SELECT chat_id, title, chat_type, accept FROM chats ORDER BY chat_type")
    rows = cur.fetchall()
    buttons = []

    channels = [(cid, title, acc) for cid, title, ctype, acc in rows if ctype == "channel"]
    groups   = [(cid, title, acc) for cid, title, ctype, acc in rows if ctype != "channel"]

    # ── CHANNELS SECTION ──
    if channels:
        buttons.append([InlineKeyboardButton(text="━━━━ 📢 CHANNELS ━━━━", callback_data="noop")])
        for chat_id, title, accept in channels:
            st    = "🟢" if accept else "🔴"
            name  = (title or str(chat_id))[:22]
            buttons.append([
                InlineKeyboardButton(text=f"{st} {name}", callback_data=f"chat_toggle:{chat_id}"),
                InlineKeyboardButton(text="⏮️ Old", callback_data=f"chat_acceptold:{chat_id}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"chat_remove:{chat_id}"),
            ])

    # ── GROUPS SECTION ──
    if groups:
        buttons.append([InlineKeyboardButton(text="━━━━ 👥 GROUPS ━━━━", callback_data="noop")])
        for chat_id, title, accept in groups:
            st    = "🟢" if accept else "🔴"
            name  = (title or str(chat_id))[:22]
            buttons.append([
                InlineKeyboardButton(text=f"{st} {name}", callback_data=f"chat_toggle:{chat_id}"),
                InlineKeyboardButton(text="⏮️ Old", callback_data=f"chat_acceptold:{chat_id}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"chat_remove:{chat_id}"),
            ])

    # ── ACTION BUTTONS ──
    buttons.append([InlineKeyboardButton(text="━━━━━━━━━━━━━━━━━━━", callback_data="noop")])
    buttons += [
        [InlineKeyboardButton(text="📺 Add Channel", callback_data="cmd:addchannel"),
         InlineKeyboardButton(text="👥 Add Group", callback_data="cmd:addgroup")],
        [InlineKeyboardButton(text="✅ Auto Accept ON",  callback_data="cmd:autoaccept_on"),
         InlineKeyboardButton(text="❌ Auto Accept OFF", callback_data="cmd:autoaccept_off")],
        [InlineKeyboardButton(text="📝 Log Channel Set", callback_data="cmd:setlog")],
        [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
    ]
    return buttons

def build_session_buttons():
    """Session panel ke buttons — logged in ho to account info + channel/group list."""
    cur.execute("SELECT phone, api_id FROM tg_sessions LIMIT 1")
    row = cur.fetchone()
    if not row:
        # Not logged in
        return [
            [InlineKeyboardButton(text="🔐 Login karein", callback_data="cmd:login")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    phone, api_id = row
    # Logged in state buttons
    buttons = [
        [InlineKeyboardButton(text=f"✅ Connected: {phone}", callback_data="session:info")],
        [InlineKeyboardButton(text="🔄 Channels/Groups Fetch", callback_data="session:fetch")],
        [InlineKeyboardButton(text="⏮️ Accept All (All Chats)", callback_data="session:acceptall"),
         InlineKeyboardButton(text="🚫 Reject All", callback_data="session:rejectall")],
        [InlineKeyboardButton(text="🚪 Logout", callback_data="cmd:logout")],
        [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
    ]
    return buttons

MENU_PAGES = {
    "setup": {
        "title": (
            "『 ⚙️ 』<b>SETUP PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>🟢=accept ON  🔴=OFF  ⏮️=old requests  🗑=remove</i>"
        ),
        "build": build_setup_buttons,
        "buttons": [],
    },
    "welcome": {
        "title": (
            "『 🌸 』<b>WELCOME PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Join karne walon ka swagat karo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="💾 Welcome Save", callback_data="cmd:save"),
             InlineKeyboardButton(text="🔗 Button Add", callback_data="cmd:addbutton")],
            [InlineKeyboardButton(text="🧹 Buttons Clear", callback_data="cmd:clearbuttons"),
             InlineKeyboardButton(text="🚨 Report Link", callback_data="cmd:setreportlink")],
            [InlineKeyboardButton(text="🎬 Episodes Link", callback_data="cmd:setepisodeslink")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
    "broadcast": {
        "title": (
            "『 📡 』<b>BROADCAST PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Sabke DM mein message bhejo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="📢 Normal Broadcast", callback_data="cmd:broadcast"),
             InlineKeyboardButton(text="↩️ Forward Broadcast", callback_data="cmd:fbroadcast")],
            [InlineKeyboardButton(text="📌 Pin Broadcast", callback_data="cmd:pinbroadcast")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
    "session": {
        "title": "『 🔑 』<b>SESSION PANEL</b>\n━━━━━━━━━━━━━━━━━\n<i>Telethon session manage karo</i>",
        "build": build_session_buttons,
        "buttons": [],
    },
    "admin": {
        "title": (
            "『 👑 』<b>ADMIN PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Admins ko manage karo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="➕ Admin Add", callback_data="cmd:addadmin"),
             InlineKeyboardButton(text="➖ Admin Remove", callback_data="cmd:removeadmin")],
            [InlineKeyboardButton(text="📋 Admin List", callback_data="cmd:admins")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
    "usermgmt": {
        "title": (
            "『 🧹 』<b>USER PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Users ko manage karo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="🚫 Blacklist", callback_data="cmd:blacklist"),
             InlineKeyboardButton(text="✅ Unblacklist", callback_data="cmd:unblacklist")],
            [InlineKeyboardButton(text="📤 Export Users", callback_data="cmd:exportusers"),
             InlineKeyboardButton(text="💾 DB Backup", callback_data="cmd:backup")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
    "moderation": {
        "title": (
            "『 🛡️ 』<b>MODERATION PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Group ko control mein rakho</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="🚫 Ban", callback_data="cmd:ban"),
             InlineKeyboardButton(text="👢 Kick", callback_data="cmd:kick")],
            [InlineKeyboardButton(text="🔇 Mute", callback_data="cmd:mute"),
             InlineKeyboardButton(text="🔊 Unmute", callback_data="cmd:unmute")],
            [InlineKeyboardButton(text="⚠️ Warn", callback_data="cmd:warn"),
             InlineKeyboardButton(text="🔓 Unwarn", callback_data="cmd:unwarn")],
            [InlineKeyboardButton(text="📌 Pin", callback_data="cmd:pin"),
             InlineKeyboardButton(text="📍 Unpin", callback_data="cmd:unpin")],
            [InlineKeyboardButton(text="🗑️ Purge", callback_data="cmd:purge"),
             InlineKeyboardButton(text="🔗 Antilink", callback_data="cmd:antilink")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
    "other": {
        "title": (
            "『 💠 』<b>OTHER PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Aur bhi kaam ke tools</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="📊 Stats", callback_data="cmd:stats"),
             InlineKeyboardButton(text="🏓 Ping", callback_data="cmd:ping")],
            [InlineKeyboardButton(text="🆔 Get ID", callback_data="cmd:id")],
            [InlineKeyboardButton(text="╔══ 🔙 BACK ══╗", callback_data="menu:main")],
        ]
    },
}

CMD_HINTS = {
    "addchannel":     "📢 <b>Channel add karo:</b>\n\n<code>/addchannel @username</code>\n<code>/addchannel -100xxxxxxxxxx</code>\n\n<i>Bot ko pehle channel mein admin banao.</i>",
    "addgroup":       "👥 <b>Group add karo:</b>\n\n<code>/addgroup @username</code>\n<code>/addgroup -100xxxxxxxxxx</code>\n\n<i>Bot ko pehle group mein admin banao.</i>",
    "removechat":     "🗑 <b>Chat remove karo:</b>\n\n<code>/removechat @username</code>\n<code>/removechat -100xxxxxxxxxx</code>",
    "chats":          "📋 <b>Chat list:</b>\n\n<code>/chats</code>",
    "setlog":         "📋 <b>Log channel set karo:</b>\n\n<code>/setlog -100xxxxxxxxxx</code>",
    "save":           "💾 <b>Welcome set karo:</b>\n\nKisi <b>video/photo/gif/text</b> message ko reply karke:\n<code>/save</code>",
    "addbutton":      "🔗 <b>Buttons add karo:</b>\n\n<code>/addbutton</code> — phir format bhejo:\n<code>Button Name | https://link</code>\n<code>Full Width || https://link</code>",
    "clearbuttons":   "🗑 Buttons clear karne ke liye:\n\n<code>/clearbuttons</code>",
    "setreportlink":  "🚨 <b>Report link set karo:</b>\n\n<code>/setreportlink https://t.me/yourlink</code>",
    "setepisodeslink":"🎬 <b>Episodes link set karo:</b>\n\n<code>/setepisodeslink https://t.me/yourlink</code>",
    "broadcast":      "📢 <b>Broadcast karo:</b>\n\nKisi message ko reply karke:\n<code>/broadcast</code>\n\nPhir 3 options aayenge.",
    "fbroadcast":     "↩️ <b>Forward broadcast:</b>\n\nKisi message ko reply karke:\n<code>/fbroadcast</code>",
    "pinbroadcast":   "📌 <b>Pin broadcast:</b>\n\nKisi message ko reply karke:\n<code>/pinbroadcast</code>",
    "login":          "🔐 <b>Telethon login:</b>\n\n<code>/login</code>\n\nmy.telegram.org se API ID aur API Hash chahiye.",
    "logout":         "🗑 Session delete karne ke liye:\n\n<code>/logout</code>",
    "session":        "📋 Session info dekhne ke liye:\n\n<code>/session</code>",
    "acceptold":      "✅ <b>Purane requests accept karo:</b>\n\n<code>/acceptold</code> — sab chats\n<code>/acceptold @username</code> — ek chat",
    "addadmin":       "➕ <b>Admin add karo:</b>\n\nUser ke message ko reply karke:\n<code>/addadmin</code>",
    "removeadmin":    "➖ <b>Admin remove karo:</b>\n\nUser ke message ko reply karke:\n<code>/removeadmin</code>",
    "admins":         "📋 Admin list dekhne ke liye:\n\n<code>/admins</code>",
    "blacklist":      "🚫 <b>Blacklist:</b>\n\n<code>/blacklist</code> — list dekho\n<code>/blacklist USER_ID</code> — add karo",
    "unblacklist":    "✅ <b>Blacklist se hatao:</b>\n\n<code>/unblacklist USER_ID</code>",
    "exportusers":    "📊 Users CSV export karne ke liye:\n\n<code>/exportusers</code>",
    "backup":         "💾 Database backup lene ke liye:\n\n<code>/backup</code>",
    "ban":            "🚫 <b>Ban karo:</b>\n\nUser ke message ko reply karke:\n<code>/ban</code>",
    "kick":           "👢 <b>Kick karo:</b>\n\nUser ke message ko reply karke:\n<code>/kick</code>",
    "mute":           "🔇 <b>Mute karo:</b>\n\nUser ke message ko reply karke:\n<code>/mute</code> ya <code>/mute 10</code> (minutes)",
    "unmute":         "🔊 <b>Unmute karo:</b>\n\nUser ke message ko reply karke:\n<code>/unmute</code>",
    "warn":           "⚠️ <b>Warn karo:</b>\n\nUser ke message ko reply karke:\n<code>/warn</code>\n\n3 warnings = auto-ban",
    "unwarn":         "✅ <b>Warnings reset karo:</b>\n\nUser ke message ko reply karke:\n<code>/unwarn</code>",
    "pin":            "📌 <b>Message pin karo:</b>\n\nMessage ko reply karke:\n<code>/pin</code>",
    "unpin":          "📌 <b>Message unpin karo:</b>\n\n<code>/unpin</code>",
    "purge":          "🗑 <b>Messages purge karo:</b>\n\nJis message se purge karna ho usse reply karke:\n<code>/purge</code>",
    "antilink":       "🔗 <b>Antilink toggle:</b>\n\n<code>/antilink on</code>\n<code>/antilink off</code>",
    "id":             "🆔 Chat/User ID dekhne ke liye:\n\n<code>/id</code>",
}

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    if msg.chat.type != "private":
        return await msg.reply("⚔️ <b>Satoru Gojo Bot</b> active hai!", parse_mode="HTML")
    # Save user to DB so broadcast reaches them
    user = msg.from_user
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, first_name, username) VALUES (?,?,?)",
        (user.id, user.first_name, user.username)
    )
    conn.commit()
    if is_admin(msg.from_user.id):
        await msg.reply(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="HTML")
    else:
        welcome_type = get_setting("welcome_type")
        if welcome_type:
            await send_welcome(msg.chat.id)
        else:
            await msg.reply("👋 Hello! Bot active hai.")

@dp.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()

@dp.callback_query(F.data.startswith("menu:"))
async def cb_menu(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins ke liye.", show_alert=True)
    page = cb.data.split(":")[1]
    await show_menu_page(cb.message, page)
    await cb.answer()

@dp.callback_query(F.data.startswith("chat_toggle:"))
async def cb_chat_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    chat_id = int(cb.data.split(":")[1])
    cur.execute("SELECT accept, title FROM chats WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        return await cb.answer("❌ Chat nahi mila.", show_alert=True)
    new_val = 0 if row[0] else 1
    cur.execute("UPDATE chats SET accept=? WHERE chat_id=?", (new_val, chat_id))
    conn.commit()
    status = "🟢 ON" if new_val else "🔴 OFF"
    await cb.answer(f"{row[1] or chat_id}: Accept {status}", show_alert=False)
    await show_menu_page(cb.message, "setup")

@dp.callback_query(F.data.startswith("chat_remove:"))
async def cb_chat_remove(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    chat_id = int(cb.data.split(":")[1])
    cur.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    cur.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    title = row[0] if row else str(chat_id)
    await cb.answer(f"🗑 {title} removed.", show_alert=False)
    await show_menu_page(cb.message, "setup")

@dp.callback_query(F.data.startswith("chat_acceptold:"))
async def cb_chat_acceptold(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    chat_id = int(cb.data.split(":")[1])
    await cb.answer("⏳ Accepting old requests...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Purane requests accept ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_run_acceptold(cb.from_user.id, status, specific_chat_id=chat_id))

async def _run_acceptold(admin_id: int, status_msg, specific_chat_id=None):
    cur.execute("SELECT api_id, api_hash, session FROM tg_sessions WHERE user_id=?", (admin_id,))
    row = cur.fetchone()
    if not row:
        await status_msg.edit_text("❌ Session nahi hai. Pehle /login karo.")
        return
    api_id, api_hash, session_str = row
    client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await status_msg.edit_text("❌ Session expired. /logout karke /login karo.")
            await client.disconnect()
            return
        from telethon.tl.functions.messages import GetChatInviteRequestsRequest, HideChatJoinRequestRequest
        if specific_chat_id:
            chats_to_process = [specific_chat_id]
        else:
            cur.execute("SELECT chat_id FROM chats WHERE accept=1")
            chats_to_process = [r[0] for r in cur.fetchall()]
        accepted = 0
        for cid in chats_to_process:
            try:
                entity = await client.get_entity(cid)
                reqs = await client(GetChatInviteRequestsRequest(peer=entity, limit=100, offset_date=None, offset_user=None))
                for req in reqs.requests:
                    try:
                        await client(HideChatJoinRequestRequest(peer=entity, user_id=req.user_id, approved=True))
                        accepted += 1
                    except Exception:
                        pass
            except Exception as e:
                log.warning(f"acceptold error {cid}: {e}")
        await status_msg.edit_text(
            f"✅ <b>{accepted}</b> purane requests accept ho gaye!",
            parse_mode="HTML",
            reply_markup=BACK_KB("setup")
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")
    finally:
        await client.disconnect()


CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Cancel", callback_data="flow:cancel")]
])

@dp.callback_query(F.data == "flow:cancel")
async def cb_flow_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_menu_page(cb.message, "main")
    await cb.answer("❌ Cancelled.")

@dp.callback_query(F.data.startswith("cmd:"))
async def cb_cmd(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins ke liye.", show_alert=True)
    cmd = cb.data.split(":")[1]
    await cb.answer()

    # ── Direct toggle actions ──────────────────────────────────
    if cmd == "autoaccept_on":
        set_setting("accept_mode", "auto")
        await cb.answer("🟢 Auto-Accept ON!", show_alert=False)
        await show_menu_page(cb.message, "setup")
        return

    if cmd == "autoaccept_off":
        set_setting("accept_mode", "manual")
        await cb.answer("🔴 Auto-Accept OFF!", show_alert=False)
        await show_menu_page(cb.message, "setup")
        return

    if cmd == "clearbuttons":
        set_setting("welcome_buttons", "")
        await cb.answer("🗑 Welcome buttons clear ho gaye!", show_alert=True)
        return

    if cmd == "stats":
        cur.execute("SELECT COUNT(*) FROM chats");        total_chats  = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users");        total_users  = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM join_logs WHERE date(joined_at)=date('now')"); today_joins = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM join_logs");    total_joins  = cur.fetchone()[0]
        mode = get_setting("accept_mode", "auto")
        st = "🟢 ON" if mode == "auto" else "🔴 OFF"
        await cb.message.answer(
            f"📊 <b>Bot Stats</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💬 <b>Chats:</b> <code>{total_chats}</code>\n"
            f"👤 <b>Users:</b> <code>{total_users}</code>\n"
            f"📅 <b>Today joins:</b> <code>{today_joins}</code>\n"
            f"🔢 <b>Total joins:</b> <code>{total_joins}</code>\n"
            f"⚡ <b>Auto-Accept:</b> {st}",
            parse_mode="HTML"
        )
        return

    if cmd == "ping":
        await cb.message.answer(f"🏓 <b>PONG!</b>\n⏱ Uptime: <code>{uptime_str()}</code>", parse_mode="HTML")
        return

    if cmd == "id":
        chat = cb.message.chat
        uname = f"@{chat.username}" if chat.username else "Private"
        await cb.message.answer(
            f"🆔 <b>Chat Info</b>\n"
            f"➺ <b>ID:</b> <code>{chat.id}</code>\n"
            f"➺ <b>Username:</b> {uname}\n"
            f"➺ <b>Your ID:</b> <code>{cb.from_user.id}</code>",
            parse_mode="HTML"
        )
        return

    # ── Export / Backup (direct) ───────────────────────────────
    if cmd == "exportusers":
        cur.execute("SELECT user_id, first_name, username, joined_at FROM users ORDER BY joined_at DESC")
        rows = cur.fetchall()
        if not rows:
            await cb.message.answer("📂 Koi user nahi hai abhi.")
            return
        lines = ["user_id,first_name,username,joined_at"] + [
            f"{r[0]},{r[1]},{r[2] or ''},{r[3]}" for r in rows
        ]
        with open("/tmp/users_export.csv", "w") as f:
            f.write("\n".join(lines))
        await bot.send_document(cb.message.chat.id, FSInputFile("/tmp/users_export.csv"),
                                caption=f"📊 Total Users: {len(rows)}")
        return

    if cmd == "backup":
        backup_path = f"/tmp/satoru_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2("satoru.db", backup_path)
        await bot.send_document(cb.message.chat.id, FSInputFile(backup_path),
                                caption=f"💾 Database Backup\n📅 {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
        return

    if cmd == "acceptold":
        status = await cb.message.answer("⏳ <b>Purane requests accept ho rahe hain...</b>", parse_mode="HTML")
        asyncio.create_task(_run_acceptold(cb.from_user.id, status))
        return

    if cmd == "admins":
        cur.execute("SELECT user_id FROM admins")
        rows = cur.fetchall()
        if not rows:
            await cb.message.answer(f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\nKoi extra admin nahi.", parse_mode="HTML")
        else:
            lines = [f"• <code>{r[0]}</code>" for r in rows]
            await cb.message.answer(
                f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n\n🛡 <b>Admins:</b>\n" + "\n".join(lines),
                parse_mode="HTML"
            )
        return

    if cmd == "chats":
        cur.execute("SELECT chat_id, title, chat_type, accept FROM chats ORDER BY chat_type")
        rows = cur.fetchall()
        if not rows:
            await cb.message.answer("📋 Koi chat registered nahi hai.\nPehle /addchannel ya /addgroup use karo.")
            return
        lines = []
        for r in rows:
            emoji = "📢" if r[2] == "channel" else "👥"
            st = "🟢" if r[3] else "🔴"
            lines.append(f"{emoji} {st} <b>{r[1]}</b> — <code>{r[0]}</code>")
        await cb.message.answer("📋 <b>Registered Chats:</b>\n\n" + "\n".join(lines), parse_mode="HTML")
        return

    if cmd == "session":
        session_file = get_setting("session_file", "")
        if session_file and os.path.exists(session_file):
            size = os.path.getsize(session_file)
            await cb.message.answer(
                f"📋 <b>Session Info</b>\n"
                f"➺ <b>File:</b> <code>{session_file}</code>\n"
                f"➺ <b>Size:</b> <code>{size} bytes</code>\n"
                f"➺ <b>Status:</b> 🟢 Active",
                parse_mode="HTML"
            )
        else:
            await cb.message.answer(
                "📋 <b>Session:</b> ❌ Not logged in\n\n"
                "Login karne ke liye <b>Login</b> button use karo.",
                parse_mode="HTML"
            )
        return

    # ── Broadcast flow ─────────────────────────────────────────
    if cmd in ("broadcast", "fbroadcast", "pinbroadcast"):
        bc_type = {"broadcast": "normal", "fbroadcast": "forward", "pinbroadcast": "pin"}[cmd]
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_type=bc_type)
        await cb.message.answer(
            f"📢 <b>Broadcast — {bc_type.upper()}</b>\n\n"
            "Wo message bhejo jo broadcast karna hai.\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    # ── Welcome Save / AddButton ────────────────────────────────
    if cmd == "save":
        await state.set_state(SaveFlow.waiting_buttons)
        await cb.message.answer(
            "💾 <b>Welcome message set karo:</b>\n\n"
            "Koi bhi <b>photo / video / gif / text</b> message bhejo.\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "addbutton":
        await state.set_state(AddButtonFlow.waiting)
        await cb.message.answer(
            "🔗 <b>Button add karo:</b>\n\n"
            "Format:\n"
            "<code>Button Name | https://link</code>\n"
            "<code>Full Width || https://link</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "setreportlink":
        await state.set_state(SetLinkFlow.report)
        await cb.message.answer(
            "🚨 <b>Report link bhejo:</b>\n\n"
            "<code>https://t.me/yourlink</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "setepisodeslink":
        await state.set_state(SetLinkFlow.episodes)
        await cb.message.answer(
            "🎬 <b>Episodes link bhejo:</b>\n\n"
            "<code>https://t.me/yourlink</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    # ── FSM flows — input lena ─────────────────────────────────
    if cmd == "addchannel":
        await state.set_state(AddChatFlow.addchannel)
        await cb.message.answer(
            "📢 <b>Channel ka @username ya ID bhejo:</b>\n\n"
            "<code>@mychannel</code>\n"
            "<code>-100xxxxxxxxxx</code>\n\n"
            "⚠️ Bot ko pehle channel mein admin banao (Add Members permission).\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "addgroup":
        await state.set_state(AddChatFlow.addgroup)
        await cb.message.answer(
            "👥 <b>Group ka @username ya ID bhejo:</b>\n\n"
            "<code>@mygroup</code>\n"
            "<code>-100xxxxxxxxxx</code>\n\n"
            "⚠️ Bot ko pehle group mein admin banao (Add Members permission).\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "removechat":
        await state.set_state(AddChatFlow.removechat)
        await cb.message.answer(
            "🗑 <b>Chat ka @username ya ID bhejo jo remove karni hai:</b>\n\n"
            "<code>@mychannel</code>\n"
            "<code>-100xxxxxxxxxx</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "setlog":
        await state.set_state(AddChatFlow.setlog)
        cur_log = get_setting("log_channel", "❌ Not set")
        await cb.message.answer(
            f"📋 <b>Log channel set karo:</b>\n"
            f"Current: <code>{cur_log}</code>\n\n"
            f"Log channel ka ID bhejo:\n"
            f"<code>-100xxxxxxxxxx</code>\n\n"
            f"<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "addadmin":
        if cb.from_user.id != OWNER_ID:
            await cb.message.answer("⚠️ <b>Sirf owner admin add kar sakta hai.</b>", parse_mode="HTML")
            return
        await state.set_state(AdminFlow.addadmin)
        await cb.message.answer(
            "➕ <b>Admin add karo:</b>\n\n"
            "Jis user ko admin banana hai uska <b>User ID</b> bhejo:\n"
            "<code>123456789</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "removeadmin":
        if cb.from_user.id != OWNER_ID:
            await cb.message.answer("⚠️ <b>Sirf owner admin remove kar sakta hai.</b>", parse_mode="HTML")
            return
        cur.execute("SELECT user_id FROM admins")
        rows = cur.fetchall()
        if not rows:
            await cb.message.answer("📋 Koi extra admin nahi hai.")
            return
        await state.set_state(AdminFlow.removeadmin)
        lines = [f"• <code>{r[0]}</code>" for r in rows]
        await cb.message.answer(
            "➖ <b>Admin remove karo:</b>\n\n"
            "Current admins:\n" + "\n".join(lines) + "\n\n"
            "Jis admin ko hatana hai uska <b>User ID</b> bhejo:\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "blacklist":
        cur.execute("SELECT user_id FROM blacklist")
        rows = cur.fetchall()
        bl_text = ""
        if rows:
            bl_text = "\n\n🚫 <b>Current blacklist:</b>\n" + "\n".join(f"• <code>{r[0]}</code>" for r in rows)
        await state.set_state(BlacklistFlow.add)
        await cb.message.answer(
            "🚫 <b>Blacklist mein add karo:</b>\n\n"
            "User ID bhejo:\n"
            "<code>123456789</code>" + bl_text + "\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "unblacklist":
        cur.execute("SELECT user_id FROM blacklist")
        rows = cur.fetchall()
        if not rows:
            await cb.message.answer("✅ Blacklist already empty hai.")
            return
        await state.set_state(BlacklistFlow.remove)
        lines = [f"• <code>{r[0]}</code>" for r in rows]
        await cb.message.answer(
            "✅ <b>Blacklist se hatao:</b>\n\n"
            "Current blacklist:\n" + "\n".join(lines) + "\n\n"
            "User ID bhejo jo hatana hai:\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "login":
        await state.set_state(LoginFlow.api_id)
        await cb.message.answer(
            "🔐 <b>Telegram Login</b>\n"
            "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            "➺ <b>Step 1/4</b> — API ID bhejo\n\n"
            "<i>my.telegram.org se milega</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "logout":
        cur.execute("SELECT phone FROM tg_sessions LIMIT 1")
        row = cur.fetchone()
        if not row:
            await cb.answer("⚠️ Koi session nahi hai.", show_alert=True)
            return
        cur.execute("DELETE FROM tg_sessions")
        conn.commit()
        await cb.answer("🗑 Session delete ho gaya!", show_alert=True)
        await show_menu_page(cb.message, "session")
        return

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SESSION PANEL CALLBACKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _get_session_client(user_id: int):
    """DB se session fetch karke connected Telethon client return karo."""
    cur.execute("SELECT api_id, api_hash, session FROM tg_sessions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT api_id, api_hash, session FROM tg_sessions LIMIT 1")
        row = cur.fetchone()
    if not row:
        return None, "❌ Session nahi hai. Pehle /login karo."
    api_id, api_hash, session_str = row
    client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None, "❌ Session expired. /logout karke /login karo."
    return client, None

@dp.callback_query(F.data == "session:info")
async def cb_session_info(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    cur.execute("SELECT phone, api_id FROM tg_sessions LIMIT 1")
    row = cur.fetchone()
    if not row:
        return await cb.answer("❌ Session nahi hai.", show_alert=True)
    await cb.answer()
    await cb.message.answer(
        f"📋 <b>Session Info</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Phone:</b> <code>{row[0]}</code>\n"
        f"➺ <b>API ID:</b> <code>{row[1]}</code>\n"
        f"➺ <b>Status:</b> 🟢 Active",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "session:fetch")
async def cb_session_fetch(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    await cb.answer("⏳ Fetch ho raha hai...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Account se channels/groups fetch ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_fetch_and_show_chats(cb.from_user.id, cb.message.chat.id, status.message_id))

async def _fetch_and_show_chats(user_id: int, chat_id: int, status_msg_id: int):
    client, err = await _get_session_client(user_id)
    if err:
        await bot.edit_message_text(err, chat_id=chat_id, message_id=status_msg_id)
        return
    try:
        from telethon.tl.types import Channel, Chat
        dialogs = await client.get_dialogs(limit=200)
        channels = []
        groups   = []
        for d in dialogs:
            if not d.entity:
                continue
            ent = d.entity
            if isinstance(ent, Channel):
                if ent.broadcast:
                    channels.append(ent)
                else:
                    groups.append(ent)
            elif isinstance(ent, Chat):
                groups.append(ent)
        await client.disconnect()

        # Build reply text
        lines = [
            f"『 📡 』<b>Account ke Chats</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"📢 <b>Channels:</b> {len(channels)}\n"
            f"👥 <b>Groups:</b> {len(groups)}\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━"
        ]

        # Channels list (max 20)
        if channels:
            lines.append("\n<b>📺 Channels:</b>")
            for i, c in enumerate(channels[:20], 1):
                uname = f"@{c.username}" if getattr(c, 'username', None) else "—"
                cid = int(f"-100{c.id}")
                lines.append(f"  {i}. <b>{c.title}</b> {uname}\n     <code>{cid}</code>")
        if len(channels) > 20:
            lines.append(f"  <i>... aur {len(channels)-20} channels</i>")

        # Groups list (max 20)
        if groups:
            lines.append("\n<b>👥 Groups:</b>")
            for i, g in enumerate(groups[:20], 1):
                uname = f"@{g.username}" if getattr(g, 'username', None) else "—"
                lines.append(f"  {i}. <b>{g.title}</b> {uname}")
        if len(groups) > 20:
            lines.append(f"  <i>... aur {len(groups)-20} groups</i>")

        text = "\n".join(lines)
        # Build action keyboard
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏮️ Accept All Pending", callback_data="session:acceptall"),
             InlineKeyboardButton(text="🚫 Reject All Pending", callback_data="session:rejectall")],
            [InlineKeyboardButton(text="📢 Sirf Channels Accept", callback_data="session:accept_channels"),
             InlineKeyboardButton(text="👥 Sirf Groups Accept", callback_data="session:accept_groups")],
            [InlineKeyboardButton(text="🔙 Session Panel", callback_data="menu:session")],
        ])

        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=status_msg_id,
            parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Fetch failed:\n<code>{e}</code>",
            chat_id=chat_id, message_id=status_msg_id, parse_mode="HTML"
        )

async def _mass_accept_reject(user_id: int, chat_id: int, status_msg_id: int, approve: bool, filter_type: str = "all"):
    """
    filter_type: "all" | "channels" | "groups"
    approve: True=accept, False=reject
    """
    client, err = await _get_session_client(user_id)
    if err:
        await bot.edit_message_text(err, chat_id=chat_id, message_id=status_msg_id)
        return
    try:
        from telethon.tl.types import Channel, Chat
        from telethon.tl.functions.messages import GetChatInviteRequestsRequest, HideChatJoinRequestRequest
        dialogs = await client.get_dialogs(limit=200)
        targets = []
        for d in dialogs:
            if not d.entity: continue
            ent = d.entity
            if filter_type == "channels" and isinstance(ent, Channel) and ent.broadcast:
                targets.append(ent)
            elif filter_type == "groups" and isinstance(ent, Channel) and not ent.broadcast:
                targets.append(ent)
            elif filter_type == "groups" and isinstance(ent, Chat):
                targets.append(ent)
            elif filter_type == "all" and isinstance(ent, (Channel, Chat)):
                targets.append(ent)

        action_word = "Accept" if approve else "Reject"
        total_ok = 0
        total_fail = 0
        details = []

        for ent in targets:
            ok = fail = 0
            try:
                reqs = await client(GetChatInviteRequestsRequest(peer=ent, limit=100, offset_date=None, offset_user=None))
                if not reqs.requests:
                    continue
                for req in reqs.requests:
                    try:
                        await client(HideChatJoinRequestRequest(peer=ent, user_id=req.user_id, approved=approve))
                        ok += 1
                        await asyncio.sleep(0.3)
                    except Exception:
                        fail += 1
                if ok or fail:
                    details.append(f"{'✅' if approve else '🚫'} <b>{ent.title[:22]}</b>: {ok} {action_word.lower()}, {fail} fail")
                    total_ok += ok
                    total_fail += fail
            except Exception as e:
                details.append(f"⚠️ <b>{ent.title[:22]}</b>: {str(e)[:40]}")

        await client.disconnect()

        detail_text = "\n".join(details[:20]) if details else "<i>Koi pending requests nahi mile.</i>"
        if len(details) > 20:
            detail_text += f"\n<i>... aur {len(details)-20} chats</i>"

        emoji = "✅" if approve else "🚫"
        await bot.edit_message_text(
            f"{emoji} <b>{action_word} Old — Done!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Total {action_word}ed:</b> {total_ok}\n"
            f"➺ <b>Failed:</b> {total_fail}\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"{detail_text}",
            chat_id=chat_id, message_id=status_msg_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Session Panel", callback_data="menu:session")]
            ])
        )
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Error:\n<code>{e}</code>",
            chat_id=chat_id, message_id=status_msg_id, parse_mode="HTML"
        )

@dp.callback_query(F.data == "session:acceptall")
async def cb_session_acceptall(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    await cb.answer("⏳ Accept all shuru...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Sab chats ke pending requests accept ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_mass_accept_reject(cb.from_user.id, cb.message.chat.id, status.message_id, approve=True, filter_type="all"))

@dp.callback_query(F.data == "session:rejectall")
async def cb_session_rejectall(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    await cb.answer("⏳ Reject all shuru...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Sab chats ke pending requests reject ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_mass_accept_reject(cb.from_user.id, cb.message.chat.id, status.message_id, approve=False, filter_type="all"))

@dp.callback_query(F.data == "session:accept_channels")
async def cb_session_accept_channels(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    await cb.answer("⏳ Channels accept shuru...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Sirf channels ke pending requests accept ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_mass_accept_reject(cb.from_user.id, cb.message.chat.id, status.message_id, approve=True, filter_type="channels"))

@dp.callback_query(F.data == "session:accept_groups")
async def cb_session_accept_groups(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    await cb.answer("⏳ Groups accept shuru...", show_alert=False)
    status = await cb.message.answer("⏳ <b>Sirf groups ke pending requests accept ho rahe hain...</b>", parse_mode="HTML")
    asyncio.create_task(_mass_accept_reject(cb.from_user.id, cb.message.chat.id, status.message_id, approve=True, filter_type="groups"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUTTON FSM HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _do_add_chat(msg: Message, state: FSMContext, chat_type_filter: str):
    """Shared logic for addchannel/addgroup from button FSM."""
    await state.clear()
    raw = msg.text.strip()
    status = await msg.reply(f"⏳ Checking <code>{raw}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(raw)
    if not result:
        return await status.edit_text("❌ Chat resolve nahi hua. @username ya -100ID check karo.", parse_mode="HTML")
    chat_id, title, username, ctype = result
    if ctype == "unknown":
        ctype = chat_type_filter
        title = title or f"Private Chat"
    label_map = {"channel": "Channel", "supergroup": "Group", "group": "Group"}
    emoji_map = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    if chat_type_filter != "any" and ctype not in (
        {"channel": {"channel"}, "supergroup": {"supergroup", "group"}}.get(chat_type_filter, {ctype})
    ):
        return await status.edit_text(f"❌ Ye <b>{ctype}</b> hai, expected {chat_type_filter}.", parse_mode="HTML")
    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status.edit_text(f"❌ Bot <b>{title}</b> mein admin nahi hai!", parse_mode="HTML")
    if not admin["can_invite"]:
        return await status.edit_text("⚠️ Bot admin hai but <b>Add Members</b> permission nahi.", parse_mode="HTML")
    cur.execute(
        "INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
        (chat_id, title, username, ctype)
    )
    conn.commit()
    uname = f"@{username}" if username else "—"
    await status.edit_text(
        f"✅ <b>{emoji_map.get(ctype,'💬')} {label_map.get(ctype,'Chat')} added!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"➺ <b>Title:</b> {title}\n"
        f"➺ <b>Username:</b> {uname}\n"
        f"➺ <b>ID:</b> <code>{chat_id}</code>\n"
        f"➺ <b>Auto-Accept:</b> 🟢 ON",
        parse_mode="HTML"
    )

async def _do_acceptold(chat_id: int, specific_chat):
    """Accept old pending requests — reusable."""
    from telethon import TelegramClient as TC
    session_file = get_setting("session_file", "")
    if not session_file or not os.path.exists(session_file):
        await bot.send_message(chat_id, "❌ Session nahi hai. Pehle /login karo.")
        return
    api_id   = int(get_setting("api_id", "0"))
    api_hash = get_setting("api_hash", "")
    client = TC(session_file, api_id, api_hash)
    try:
        await client.connect()
        from telethon.tl.functions.messages import GetDialogsRequest
        from telethon.tl.types import InputPeerEmpty
        accepted = 0
        if specific_chat:
            result = await resolve_chat_id(specific_chat)
            if not result:
                await bot.send_message(chat_id, "❌ Chat resolve nahi hua.")
                return
            chats_to_process = [result[0]]
        else:
            cur.execute("SELECT chat_id FROM chats WHERE accept=1")
            chats_to_process = [r[0] for r in cur.fetchall()]
        for cid in chats_to_process:
            try:
                from telethon.tl.functions.messages import GetChatInviteRequestsRequest, HideChatJoinRequestRequest
                from telethon.tl.types import InputChannel
                entity = await client.get_entity(cid)
                reqs = await client(GetChatInviteRequestsRequest(peer=entity, limit=100, offset_date=None, offset_user=None))
                for req in reqs.requests:
                    try:
                        await client(HideChatJoinRequestRequest(peer=entity, user_id=req.user_id, approved=True))
                        accepted += 1
                    except Exception:
                        pass
            except Exception as e:
                log.warning(f"acceptold error for {cid}: {e}")
        await bot.send_message(chat_id, f"✅ <b>{accepted}</b> requests accepted!", parse_mode="HTML")
    finally:
        await client.disconnect()

@dp.message(AddChatFlow.addchannel)
async def flow_addchannel(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.startswith("/skip"):
        await state.clear()
        return await msg.reply("❌ Cancelled.")
    data = await state.get_data()
    await state.clear()
    raw = msg.text.strip()
    # Check if custom name given: "ID Name" format
    parts = raw.split(maxsplit=1)
    chat_raw   = parts[0]
    custom_name = parts[1] if len(parts) > 1 else None
    status = await msg.reply(f"⏳ Checking <code>{chat_raw}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(chat_raw)
    if not result:
        return await status.edit_text(
            "❌ Chat resolve nahi hua.\n@username ya -100ID do.",
            parse_mode="HTML", reply_markup=BACK_KB("setup")
        )
    chat_id, title, username, ctype = result
    if custom_name: title = custom_name
    if ctype == "unknown": ctype = "channel"
    if not title: title = f"Channel {chat_id}"
    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status.edit_text(f"❌ Bot <b>{title}</b> mein admin nahi hai!", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    if not admin["can_invite"]:
        return await status.edit_text("⚠️ Bot admin hai but <b>Add Members</b> permission nahi.", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    cur.execute("INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
                (chat_id, title, username, "channel"))
    conn.commit()
    uname = f"@{username}" if username else "—"
    await status.edit_text(
        f"✅ <b>📢 Channel added!</b>\n━━━━━━━━━━━━━━━\n"
        f"➺ <b>Name:</b> {title}\n➺ <b>Username:</b> {uname}\n➺ <b>ID:</b> <code>{chat_id}</code>\n➺ Auto-Accept: 🟢 ON",
        parse_mode="HTML", reply_markup=BACK_KB("setup")
    )

@dp.message(AddChatFlow.addgroup)
async def flow_addgroup(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.startswith("/skip"):
        await state.clear()
        return await msg.reply("❌ Cancelled.")
    await state.clear()
    raw   = msg.text.strip()
    parts = raw.split(maxsplit=1)
    chat_raw    = parts[0]
    custom_name = parts[1] if len(parts) > 1 else None
    status = await msg.reply(f"⏳ Checking <code>{chat_raw}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(chat_raw)
    if not result:
        return await status.edit_text("❌ Chat resolve nahi hua.", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    chat_id, title, username, ctype = result
    if custom_name: title = custom_name
    if ctype == "unknown": ctype = "supergroup"
    if not title: title = f"Group {chat_id}"
    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status.edit_text(f"❌ Bot <b>{title}</b> mein admin nahi hai!", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    if not admin["can_invite"]:
        return await status.edit_text("⚠️ Bot admin hai but <b>Add Members</b> permission nahi.", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    cur.execute("INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
                (chat_id, title, username, ctype))
    conn.commit()
    uname = f"@{username}" if username else "—"
    await status.edit_text(
        f"✅ <b>👥 Group added!</b>\n━━━━━━━━━━━━━━━\n"
        f"➺ <b>Name:</b> {title}\n➺ <b>Username:</b> {uname}\n➺ <b>ID:</b> <code>{chat_id}</code>\n➺ Auto-Accept: 🟢 ON",
        parse_mode="HTML", reply_markup=BACK_KB("setup")
    )

@dp.message(AddChatFlow.removechat)
async def flow_removechat(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("setup"))
    result = await resolve_chat_id(msg.text.strip())
    if not result:
        return await msg.reply("❌ Chat resolve nahi hua.", reply_markup=BACK_KB("setup"))
    chat_id, title, _, _ = result
    cur.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    if cur.rowcount:
        await msg.reply(f"🗑 <b>{title or chat_id}</b> removed.", parse_mode="HTML", reply_markup=BACK_KB("setup"))
    else:
        await msg.reply("⚠️ Ye chat registered nahi tha.", reply_markup=BACK_KB("setup"))

@dp.message(AddChatFlow.setlog)
async def flow_setlog(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("setup"))
    try:
        log_id = int(msg.text.strip())
    except ValueError:
        return await msg.reply("❌ Valid chat ID do (e.g. -100xxxxxxxxxx)", reply_markup=BACK_KB("setup"))
    set_setting("log_channel", str(log_id))
    await msg.reply(f"✅ Log channel set: <code>{log_id}</code>", parse_mode="HTML", reply_markup=BACK_KB("setup"))

@dp.message(AdminFlow.addadmin)
async def flow_addadmin(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID: return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("admin"))
    try:
        uid = int(msg.text.strip())
    except ValueError:
        return await msg.reply("❌ Valid User ID do.", reply_markup=BACK_KB("admin"))
    cur.execute("INSERT OR IGNORE INTO admins VALUES (?)", (uid,))
    conn.commit()
    await msg.reply(f"✅ <code>{uid}</code> admin ban gaya.", parse_mode="HTML", reply_markup=BACK_KB("admin"))

@dp.message(AdminFlow.removeadmin)
async def flow_removeadmin(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID: return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("admin"))
    try:
        uid = int(msg.text.strip())
    except ValueError:
        return await msg.reply("❌ Valid User ID do.", reply_markup=BACK_KB("admin"))
    cur.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    conn.commit()
    if cur.rowcount:
        await msg.reply(f"🗑 <code>{uid}</code> admin list se remove.", parse_mode="HTML", reply_markup=BACK_KB("admin"))
    else:
        await msg.reply(f"⚠️ <code>{uid}</code> admin nahi tha.", parse_mode="HTML", reply_markup=BACK_KB("admin"))

@dp.message(BlacklistFlow.add)
async def flow_blacklist_add(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("usermgmt"))
    try:
        uid = int(msg.text.strip())
    except ValueError:
        return await msg.reply("❌ Valid User ID do.", reply_markup=BACK_KB("usermgmt"))
    cur.execute("INSERT OR IGNORE INTO blacklist VALUES (?)", (uid,))
    conn.commit()
    await msg.reply(f"🚫 <code>{uid}</code> blacklisted.", parse_mode="HTML", reply_markup=BACK_KB("usermgmt"))

@dp.message(BlacklistFlow.remove)
async def flow_blacklist_remove(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    if msg.text and msg.text.startswith("/skip"):
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("usermgmt"))
    try:
        uid = int(msg.text.strip())
    except ValueError:
        return await msg.reply("❌ Valid User ID do.", reply_markup=BACK_KB("usermgmt"))
    cur.execute("DELETE FROM blacklist WHERE user_id=?", (uid,))
    conn.commit()
    if cur.rowcount:
        await msg.reply(f"✅ <code>{uid}</code> blacklist se remove.", parse_mode="HTML", reply_markup=BACK_KB("usermgmt"))
    else:
        await msg.reply(f"⚠️ <code>{uid}</code> blacklist mein tha hi nahi.", parse_mode="HTML", reply_markup=BACK_KB("usermgmt"))

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
#  /setreportlink /setepisodeslink
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
#  /login — Telethon String Session
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("login"))
async def cmd_login(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    # Pehle se saved session check karo
    cur.execute("SELECT phone FROM tg_sessions WHERE user_id=?", (msg.from_user.id,))
    existing = cur.fetchone()
    if existing:
        await msg.reply(
            f"⚠️ <b>Ek session pehle se save hai</b> (Phone: <code>{existing[0]}</code>)\n\n"
            f"Replace karna hai? Naya login karo ya /logout karo pehle.",
            parse_mode="HTML"
        )
    await state.set_state(LoginFlow.api_id)
    await msg.reply(
        "🔐 <b>Telegram Login</b>\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "➺ <b>Step 1/4</b> — API ID bhejo\n\n"
        "<i>my.telegram.org se milega</i>",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.api_id, F.text)
async def login_got_api_id(msg: Message, state: FSMContext):
    api_id = msg.text.strip()
    if not api_id.isdigit():
        return await msg.reply("❌ API ID sirf numbers hona chahiye. Dobara bhejo:")
    await state.update_data(api_id=api_id)
    await state.set_state(LoginFlow.api_hash)
    await msg.reply(
        "✅ API ID mila!\n\n"
        "➺ <b>Step 2/4</b> — API Hash bhejo:",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.api_hash, F.text)
async def login_got_api_hash(msg: Message, state: FSMContext):
    api_hash = msg.text.strip()
    if len(api_hash) != 32:
        return await msg.reply("❌ API Hash 32 characters ka hona chahiye. Dobara bhejo:")
    await state.update_data(api_hash=api_hash)
    await state.set_state(LoginFlow.phone)
    await msg.reply(
        "✅ API Hash mila!\n\n"
        "➺ <b>Step 3/4</b> — Phone number bhejo\n"
        "<i>Format: +919876543210</i>",
        parse_mode="HTML"
    )

@dp.message(LoginFlow.phone, F.text)
async def login_got_phone(msg: Message, state: FSMContext):
    phone = msg.text.strip()
    data     = await state.get_data()
    api_id   = int(data["api_id"])
    api_hash = data["api_hash"]

    status = await msg.reply("⏳ OTP bhej raha hoon...")
    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        _login_clients[msg.from_user.id] = client
        await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash)
        await state.set_state(LoginFlow.otp)
        await status.edit_text(
            "📲 OTP bheja gaya!\n\n"
            "➺ <b>Step 4/4</b> — OTP bhejo <b>space se</b>\n"
            "<i>Example: 1 2 3 4 5 6</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        await state.clear()
        await status.edit_text(f"❌ OTP send nahi ho saka:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.otp, F.text)
async def login_got_otp(msg: Message, state: FSMContext):
    otp = msg.text.strip().replace(" ", "")
    if not otp.isdigit():
        return await msg.reply(
            "❌ Sirf numbers bhejo (space se).\nExample: <code>1 2 3 4 5 6</code>",
            parse_mode="HTML"
        )
    data            = await state.get_data()
    phone           = data["phone"]
    phone_code_hash = data["phone_code_hash"]
    client: TelegramClient = _login_clients.get(msg.from_user.id)

    if not client:
        await state.clear()
        return await msg.reply("❌ Session expire ho gaya. /login se dobara shuru karo.")

    status = await msg.reply("⏳ Verify kar raha hoon...")
    try:
        await client.sign_in(phone=phone, code=otp, phone_code_hash=phone_code_hash)
        await _save_session(msg, state, client, data)
        await status.delete()
    except SessionPasswordNeededError:
        await state.set_state(LoginFlow.password)
        await status.edit_text(
            "🔒 2FA enabled hai!\n\n"
            "➺ Password bhejo:",
            parse_mode="HTML"
        )
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await state.clear()
        _login_clients.pop(msg.from_user.id, None)
        await status.edit_text("❌ OTP galat/expired. /login se dobara karo.")
    except Exception as e:
        await state.clear()
        _login_clients.pop(msg.from_user.id, None)
        await status.edit_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(LoginFlow.password, F.text)
async def login_got_password(msg: Message, state: FSMContext):
    password = msg.text.strip()
    data   = await state.get_data()
    client: TelegramClient = _login_clients.get(msg.from_user.id)

    if not client:
        await state.clear()
        return await msg.reply("❌ Session expire. /login se dobara karo.")

    status = await msg.reply("⏳ 2FA verify kar raha hoon...")
    try:
        await client.sign_in(password=password)
        await _save_session(msg, state, client, data)
        await status.delete()
    except PasswordHashInvalidError:
        await status.edit_text("❌ Password galat hai. Dobara bhejo:")
    except Exception as e:
        await state.clear()
        _login_clients.pop(msg.from_user.id, None)
        await status.edit_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

async def _save_session(msg: Message, state: FSMContext, client: TelegramClient, data: dict):
    session_str = client.session.save()
    api_id   = data["api_id"]
    api_hash = data["api_hash"]
    phone    = data["phone"]
    cur.execute(
        "INSERT OR REPLACE INTO tg_sessions (user_id, api_id, api_hash, phone, session) VALUES (?,?,?,?,?)",
        (msg.from_user.id, api_id, api_hash, phone, session_str)
    )
    conn.commit()
    await client.disconnect()
    _login_clients.pop(msg.from_user.id, None)
    await state.clear()
    await msg.reply(
        "✅ <b>Login Successful!</b>\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Phone:</b> <code>{phone}</code>\n"
        f"➺ <b>Session:</b> ✅ DB mein save ho gaya\n\n"
        "Ab /acceptold se purane join requests accept kar sakte ho!",
        parse_mode="HTML"
    )

@dp.message(Command("logout"))
async def cmd_logout(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("DELETE FROM tg_sessions WHERE user_id=?", (msg.from_user.id,))
    conn.commit()
    await msg.reply("🗑 Session delete ho gaya.")

@dp.message(Command("session"))
async def cmd_session(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT phone, api_id FROM tg_sessions WHERE user_id=?", (msg.from_user.id,))
    row = cur.fetchone()
    if not row:
        return await msg.reply("❌ Koi session saved nahi. /login karo pehle.")
    await msg.reply(
        f"✅ <b>Active Session</b>\n"
        f"➺ Phone: <code>{row[0]}</code>\n"
        f"➺ API ID: <code>{row[1]}</code>",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /acceptold — Purane pending join requests accept karo
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("acceptold"))
async def cmd_acceptold(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")

    # Session check karo
    cur.execute("SELECT api_id, api_hash, session FROM tg_sessions WHERE user_id=?", (msg.from_user.id,))
    row = cur.fetchone()
    if not row:
        return await msg.reply(
            "❌ Koi session nahi mila.\n"
            "/login karo pehle, phir /acceptold chalao.",
            parse_mode="HTML"
        )

    api_id, api_hash, session_str = row

    # Kaunse chats process karne hain
    args = msg.text.split(maxsplit=1)
    target_chat = args[1].strip() if len(args) > 1 else None

    if target_chat:
        result = await resolve_chat_id(target_chat)
        if not result:
            return await msg.reply("❌ Chat resolve nahi hua.")
        chat_ids = [result[0]]
        chat_titles = {result[0]: result[1] or str(result[0])}
    else:
        cur.execute("SELECT chat_id, title FROM chats WHERE accept=1")
        rows = cur.fetchall()
        if not rows:
            return await msg.reply("❌ Koi chat DB mein nahi. /addchannel ya /addgroup se add karo.")
        chat_ids = [r[0] for r in rows]
        chat_titles = {r[0]: r[1] for r in rows}

    status_msg = await msg.reply(
        f"⏳ <b>Processing {len(chat_ids)} chat(s)...</b>\n"
        f"Telethon se purane requests accept ho rahe hain...",
        parse_mode="HTML"
    )

    asyncio.create_task(_acceptold_task(
        msg.chat.id, status_msg.message_id,
        api_id, api_hash, session_str,
        chat_ids, chat_titles
    ))

async def _acceptold_task(
    notify_chat: int, notify_msg_id: int,
    api_id: str, api_hash: str, session_str: str,
    chat_ids: list, chat_titles: dict
):
    total_ok = 0
    total_fail = 0
    total_none = 0
    details = []

    try:
        client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "❌ Session invalid/expired. /logout karke /login karo.",
                chat_id=notify_chat, message_id=notify_msg_id,
                parse_mode="HTML"
            )
            await client.disconnect()
            return

        for chat_id in chat_ids:
            title = chat_titles.get(chat_id, str(chat_id))
            ok = 0
            fail = 0
            try:
                entity = await client.get_entity(chat_id)
                # Sabhi pending join requests fetch karo
                async for req in client.iter_participants(entity, filter="requests"):
                    try:
                        await client(
                            __import__("telethon.tl.functions.messages", fromlist=["HideChatJoinRequestRequest"])
                            .HideChatJoinRequestRequest(peer=entity, user_id=req, approved=True)
                        )
                        ok += 1
                        await asyncio.sleep(0.3)
                    except Exception:
                        fail += 1
            except Exception as e:
                details.append(f"❌ {title[:20]}: {str(e)[:40]}")
                total_fail += 1
                continue

            if ok == 0 and fail == 0:
                total_none += 1
                details.append(f"ℹ️ {title[:20]}: Koi pending request nahi")
            else:
                total_ok += ok
                total_fail += fail
                details.append(f"✅ {title[:20]}: {ok} accepted, {fail} fail")

        await client.disconnect()

        detail_text = "\n".join(details[:15])
        if len(details) > 15:
            detail_text += f"\n... aur {len(details)-15} chats"

        await bot.edit_message_text(
            f"✅ <b>Accept Old Done!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Total Accepted:</b> {total_ok}\n"
            f"➺ <b>Failed:</b> {total_fail}\n"
            f"➺ <b>No Pending:</b> {total_none}\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"{detail_text}",
            chat_id=notify_chat, message_id=notify_msg_id,
            parse_mode="HTML"
        )

    except Exception as e:
        await bot.edit_message_text(
            f"❌ Error:\n<code>{e}</code>",
            chat_id=notify_chat, message_id=notify_msg_id,
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AUTO-ACCEPT JOIN REQUESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_join_log(user_id: int, first_name: str, username: str, chat_id: int, chat_title: str):
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
    user_id    = req.from_user.id
    first_name = req.from_user.first_name or "User"
    username   = req.from_user.username or ""

    cur.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, accept) VALUES (?,?,?,1)",
        (req.chat.id, title, chat_type)
    )
    cur.execute("UPDATE chats SET title=?, chat_type=? WHERE chat_id=?", (title, chat_type, req.chat.id))
    conn.commit()

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

        asyncio.create_task(send_join_log(user_id, first_name, username, req.chat.id, title))

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
#  /broadcast
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
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
    data = await state.get_data()
    bc_type = data.get("bc_type") or data.get("bc_force_mode")
    await state.update_data(broadcast_msg_id=msg.message_id, broadcast_chat_id=msg.chat.id)
    # If bc_type already set (from button flow), start immediately
    if bc_type and bc_type in ("normal", "forward", "pin"):
        await state.clear()
        status = await msg.reply("⏳ Broadcasting...", parse_mode="HTML")
        asyncio.create_task(_do_broadcast(msg.chat.id, msg.message_id, bc_type, status))
        return
    # Otherwise show type selection
    await state.set_state(BroadcastFlow.choose_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Normal Broadcast", callback_data="bc:normal")],
        [InlineKeyboardButton(text="📌 Pin Broadcast", callback_data="bc:pin")],
        [InlineKeyboardButton(text="↩️ Forward Tag Broadcast", callback_data="bc:forward")],
    ])
    await msg.reply("📡 <b>Broadcast type choose karo:</b>", reply_markup=kb, parse_mode="HTML")

async def _do_broadcast(source_chat_id: int, source_msg_id: int, mode: str, status_msg: Message):
    # Broadcast sirf users ke DM mein jaayega (channels/groups mein nahi)
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    ok, fail, blocked = 0, 0, 0
    for (user_id,) in users:
        try:
            if mode == "forward":
                await bot.forward_message(user_id, source_chat_id, source_msg_id)
            else:
                await bot.copy_message(user_id, source_chat_id, source_msg_id)
            ok += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "bot was blocked" in err or "user is deactivated" in err or "chat not found" in err:
                blocked += 1
            else:
                fail += 1
        await asyncio.sleep(0.05)

    mode_label = {"normal": "Normal", "pin": "DM (Pin N/A)", "forward": "Forward Tag"}.get(mode, mode)
    await status_msg.edit_text(
        f"📢 <b>Broadcast Done! [{mode_label}]</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"✅ Sent: {ok}\n"
        f"🚫 Blocked/Inactive: {blocked}\n"
        f"❌ Failed: {fail}\n"
        f"📊 Total: {ok+blocked+fail}",
        parse_mode="HTML",
        reply_markup=BACK_KB("broadcast")
    )

@dp.callback_query(F.data.startswith("bc:"))
async def cb_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Not allowed.", show_alert=True)
    mode = cb.data.split(":")[1]
    data = await state.get_data()
    source_msg_id  = data.get("broadcast_msg_id")
    source_chat_id = data.get("broadcast_chat_id")
    await state.clear()
    if not source_msg_id:
        return await cb.answer("❌ Message nahi mila.", show_alert=True)
    await cb.answer(f"Broadcasting [{mode}]...", show_alert=False)
    status = await cb.message.edit_text("⏳ Broadcasting...", parse_mode="HTML")
    asyncio.create_task(_do_broadcast(source_chat_id, source_msg_id, mode, status))

@dp.message(Command("fbroadcast"))
async def cmd_fbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="forward")
        return await msg.reply("↩️ Jo message forward karna hai usse reply karke /fbroadcast karo.")
    status = await msg.reply("⏳ Forward broadcasting to users DM...")
    asyncio.create_task(_do_broadcast(msg.chat.id, msg.reply_to_message.message_id, "forward", status))

@dp.message(Command("pinbroadcast"))
async def cmd_pinbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="pin")
        return await msg.reply("↩️ Jo message pin karke broadcast karna hai usse reply karke /pinbroadcast karo.")
    status = await msg.reply("⏳ Broadcasting to users DM...")
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
    uid   = msg.reply_to_message.from_user.id
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
    uid   = msg.reply_to_message.from_user.id
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
#  GROUP ADMIN MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message(Command("addadmingrp", "aag"))
async def cmd_addadmingrp(msg: Message):
    """Reply to a user → promote them as group admin."""
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf group admins ya bot admins ye kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Jis user ko admin banana hai uske message pe reply karke <code>/aag</code> likho.", parse_mode="HTML")
    uid   = target.from_user.id
    uname = target.from_user.mention_html()
    try:
        await bot.promote_chat_member(
            msg.chat.id, uid,
            can_manage_chat=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_video_chats=True,
        )
        await msg.reply(
            f"👑 <b>{uname}</b> ko group admin bana diya!\n"
            f"➺ <b>Chat:</b> {msg.chat.title}\n"
            f"➺ <b>By:</b> {msg.from_user.mention_html()}",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Admin nahi bana saka:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(Command("removeadmingrp", "rag"))
async def cmd_removeadmingrp(msg: Message):
    """Reply to a user → demote them from group admin."""
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf group admins ya bot admins ye kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Jis user ko demote karna hai uske message pe reply karke <code>/rag</code> likho.", parse_mode="HTML")
    uid   = target.from_user.id
    uname = target.from_user.mention_html()
    try:
        await bot.promote_chat_member(
            msg.chat.id, uid,
            can_manage_chat=False,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_video_chats=False,
        )
        await msg.reply(
            f"🗑 <b>{uname}</b> ko group admin se remove kar diya.\n"
            f"➺ <b>Chat:</b> {msg.chat.title}\n"
            f"➺ <b>By:</b> {msg.from_user.mention_html()}",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Demote nahi ho saka:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(Command("listadmingrp", "lag"))
async def cmd_listadmingrp(msg: Message):
    """List all current admins of the group."""
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    try:
        admins = await bot.get_chat_administrators(msg.chat.id)
        lines = []
        for a in admins:
            role = "👑 Creator" if a.status.value == "creator" else "🛡 Admin"
            uname = a.user.mention_html()
            custom = f" — <i>{a.custom_title}</i>" if getattr(a, "custom_title", None) else ""
            lines.append(f"{role} {uname}{custom}")
        await msg.reply(
            f"📋 <b>{msg.chat.title} — Admins ({len(admins)})</b>\n"
            f"━━━━━━━━━━━━━━━\n" + "\n".join(lines),
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Admin list nahi mili:\n<code>{e}</code>", parse_mode="HTML")

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
#  EXPORT / BACKUP
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
    await bot.send_document(msg.chat.id, FSInputFile("/tmp/users_export.csv"), caption=f"📊 Total Users: {len(rows)}")

@dp.message(Command("backup"))
async def cmd_backup(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    backup_path = f"/tmp/satoru_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2("satoru.db", backup_path)
    await bot.send_document(
        msg.chat.id, FSInputFile(backup_path),
        caption=f"💾 Database Backup\n📅 {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    )

async def auto_daily_backup():
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_midnight - now).total_seconds())
        try:
            backup_path = f"/tmp/satoru_daily_{datetime.now().strftime('%Y%m%d')}.db"
            shutil.copy2("satoru.db", backup_path)
            await bot.send_document(OWNER_ID, FSInputFile(backup_path),
                caption=f"💾 Auto Daily Backup\n📅 {datetime.now().strftime('%d %b %Y')}")
        except Exception as e:
            log.error(f"Daily backup failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /id /ping /stats /help
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
    cur.execute("SELECT COUNT(*) FROM chats");        total_chats  = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins");       total_admins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users");        total_users  = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM join_logs WHERE date(joined_at)=date('now')"); today_joins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM join_logs");    total_joins  = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM blacklist");    bl_count     = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM tg_sessions");  sess_count   = cur.fetchone()[0]
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
        f"➺ <b>Saved Sessions:</b> {sess_count}\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome Type:</b> {wtype}\n"
        f"➺ <b>Log Channel:</b> <code>{log_ch}</code>\n"
        f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"

    is_private = msg.chat.type == "private"

    status_text = (
        f"『 ⚔️ 』<b>SATORU GOJO BOT</b>\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"\n"
        f"┌ 🤖 <b>Status</b>  ➜  <code>Online</code>\n"
        f"├ ⚡ <b>Auto-Accept</b>  ➜  {st}\n"
        f"├ ⏱️ <b>Uptime</b>  ➜  <code>{uptime_str()}</code>\n"
        f"└ 👁️ <b>Mode</b>  ➜  <code>Infinity</code>\n"
        f"\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
    )

    commands_text = (
        f"\n"
        f"<b>⚙️ SETUP</b>\n"
        f"├ /addchannel — Channel add karo\n"
        f"├ /addgroup — Group add karo\n"
        f"├ /removechat — Chat remove karo\n"
        f"├ /chats — Registered chats list\n"
        f"├ /autoaccept on|off — Auto-accept toggle\n"
        f"└ /setlog — Log channel set karo\n"
        f"\n"
        f"<b>👑 BOT ADMINS</b>\n"
        f"├ /addadmin — Reply karke bot admin banao\n"
        f"├ /removeadmin — Reply karke bot admin hatao\n"
        f"└ /admins — Bot admin list\n"
        f"\n"
        f"<b>👥 GROUP ADMIN</b> <i>(group mein use karo)</i>\n"
        f"├ /aag — Reply → group admin banao\n"
        f"├ /rag — Reply → group admin hatao\n"
        f"└ /lag — Group admin list dekho\n"
        f"<i>(Full: /addadmingrp /removeadmingrp /listadmingrp)</i>\n"
        f"\n"
        f"<b>🛡 GROUP MANAGEMENT</b> <i>(group mein use karo)</i>\n"
        f"├ /ban — Reply → ban karo\n"
        f"├ /kick — Reply → kick karo\n"
        f"├ /mute [time] — Reply → mute karo\n"
        f"├ /unmute — Reply → unmute karo\n"
        f"├ /warn — Reply → warn karo\n"
        f"├ /unwarn — Reply → warn hatao\n"
        f"├ /pin — Reply → pin karo (loud)\n"
        f"├ /pin silent — Reply → silently pin karo\n"
        f"├ /pin <id> — Message ID se pin karo\n"
        f"├ /pin <id> s — Message ID silently pin\n"
        f"├ /unpin — Last pinned unpin karo\n"
        f"├ /unpin all — Sab unpin karo\n"
        f"├ /unpin <id> — Specific ID unpin karo\n"
        f"└ /removepin — /unpin ka alias\n"
        f"├ /purge — Reply tak ke messages delete karo\n"
        f"└ /antilink on|off — Link filter toggle\n"
        f"\n"
        f"<b>📢 BROADCAST</b>\n"
        f"├ /broadcast — Normal broadcast\n"
        f"├ /fbroadcast — Forward broadcast\n"
        f"└ /pinbroadcast — Pin + broadcast\n"
        f"\n"
        f"<b>🚫 BLACKLIST</b>\n"
        f"├ /blacklist [ID] — User blacklist karo\n"
        f"└ /unblacklist [ID] — Blacklist se hatao\n"
        f"\n"
        f"<b>🔐 SESSION</b>\n"
        f"├ /login — Userbot login\n"
        f"├ /logout — Logout\n"
        f"├ /session — Session status\n"
        f"└ /acceptold — Purane pending requests accept karo\n"
        f"\n"
        f"<b>📊 INFO</b>\n"
        f"├ /stats — Bot stats\n"
        f"├ /id — Chat/User ID\n"
        f"├ /ping — Bot ping\n"
        f"├ /exportusers — Users CSV export\n"
        f"└ /backup — Database backup\n"
        f"\n"
        f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"<i>「 The Strongest Bot is Here 」</i>\n"
        f"<i>Powered by @TALK_WITH_STEALED</i>"
    )

    if is_private:
        await msg.reply(
            status_text + f"<i>「 The Strongest Bot is Here 」</i>\n\n𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗽𝗮𝗻𝗲𝗹 𝗯𝗲𝗹𝗼𝘄 👇",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        await msg.reply(status_text + commands_text, parse_mode="HTML")

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
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins ban kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /ban likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(f"🚫 <b>{target.from_user.mention_html()}</b> banned.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Ban nahi ho saka: {e}")

@dp.message(Command("kick"))
async def cmd_kick(msg: Message):
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins kick kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /kick likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await bot.unban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(f"👢 <b>{target.from_user.mention_html()}</b> kicked.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Kick nahi ho saka: {e}")

@dp.message(Command("mute"))
async def cmd_mute(msg: Message):
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins mute kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /mute likho.")
    args = msg.text.split()
    duration = None
    if len(args) > 1:
        try: duration = int(args[1])
        except ValueError: pass
    until = datetime.now() + timedelta(minutes=duration) if duration else None
    try:
        await bot.restrict_chat_member(msg.chat.id, target.from_user.id,
            permissions=ChatPermissions(can_send_messages=False), until_date=until)
        dur_text = f" {duration} minute ke liye" if duration else " permanently"
        await msg.reply(f"🔇 <b>{target.from_user.mention_html()}</b> ko{dur_text} mute.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Mute nahi ho saka: {e}")

@dp.message(Command("unmute"))
async def cmd_unmute(msg: Message):
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unmute kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /unmute likho.")
    try:
        await bot.restrict_chat_member(msg.chat.id, target.from_user.id,
            permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True))
        await msg.reply(f"🔊 <b>{target.from_user.mention_html()}</b> unmuted.", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Unmute nahi ho saka: {e}")

@dp.message(Command("warn"))
async def cmd_warn(msg: Message):
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins warn kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /warn likho.")
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
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unwarn kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /unwarn likho.")
    reset_warns(target.from_user.id, msg.chat.id)
    await msg.reply(f"✅ <b>{target.from_user.mention_html()}</b> warnings reset.", parse_mode="HTML")

@dp.message(Command("pin"))
async def cmd_pin(msg: Message):
    """
    /pin           → reply karke pin karo (loud)
    /pin silent    → reply karke silently pin karo
    /pin <msg_id>  → specific message ID pin karo
    /pin <id> s    → specific ID silently pin karo
    /removepin     → alias for /unpin
    """
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins pin kar sakte hain.")

    args = msg.text.split()[1:]  # everything after /pin
    silent = False
    msg_id = None

    # Check flags
    clean_args = []
    for a in args:
        if a.lower() in ("s", "silent", "-s", "--silent"):
            silent = True
        else:
            clean_args.append(a)

    if clean_args:
        try:
            msg_id = int(clean_args[0])
        except ValueError:
            return await msg.reply(
                "❌ Invalid format.\n\n"
                "Usage:\n"
                "<code>/pin</code> — reply karke pin karo\n"
                "<code>/pin silent</code> — reply karke silently pin karo\n"
                "<code>/pin 1234</code> — message ID se pin karo\n"
                "<code>/pin 1234 s</code> — silently pin karo",
                parse_mode="HTML"
            )
    elif msg.reply_to_message:
        msg_id = msg.reply_to_message.message_id
    else:
        return await msg.reply(
            "↩️ Reply karke /pin likho, ya message ID do:\n"
            "<code>/pin 1234</code>",
            parse_mode="HTML"
        )

    try:
        await bot.pin_chat_message(msg.chat.id, msg_id, disable_notification=silent)
        mode = "🔕 Silently" if silent else "🔔 Loudly"
        await msg.reply(f"📌 Message <code>{msg_id}</code> pin ho gaya! {mode}", parse_mode="HTML")
    except Exception as e:
        await msg.reply(f"❌ Pin nahi ho saka:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(Command("unpin", "removepin"))
async def cmd_unpin(msg: Message):
    """
    /unpin         → last pinned message unpin karo
    /removepin     → same
    /unpin all     → sab pinned messages unpin karo
    /unpin <id>    → specific message ID unpin karo
    """
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unpin kar sakte hain.")

    args = msg.text.split()[1:]

    if args and args[0].lower() == "all":
        try:
            await bot.unpin_all_chat_messages(msg.chat.id)
            await msg.reply("📌 Saare pinned messages unpin ho gaye!")
        except Exception as e:
            await msg.reply(f"❌ Unpin all nahi ho saka:\n<code>{e}</code>", parse_mode="HTML")
        return

    if args:
        try:
            msg_id = int(args[0])
        except ValueError:
            return await msg.reply(
                "❌ Invalid format.\n\n"
                "Usage:\n"
                "<code>/unpin</code> — last pinned unpin karo\n"
                "<code>/unpin all</code> — sab unpin karo\n"
                "<code>/unpin 1234</code> — specific ID unpin karo",
                parse_mode="HTML"
            )
        try:
            await bot.unpin_chat_message(msg.chat.id, message_id=msg_id)
            await msg.reply(f"📌 Message <code>{msg_id}</code> unpin ho gaya!", parse_mode="HTML")
        except Exception as e:
            await msg.reply(f"❌ Unpin nahi ho saca:\n<code>{e}</code>", parse_mode="HTML")
        return

    # No args — unpin last pinned
    try:
        await bot.unpin_chat_message(msg.chat.id)
        await msg.reply("📌 Last pinned message unpin ho gaya!")
    except Exception as e:
        await msg.reply(f"❌ Unpin nahi ho saka:\n<code>{e}</code>", parse_mode="HTML")



@dp.message(Command("purge"))
async def cmd_purge(msg: Message):
    if msg.chat.type == "private": return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins purge kar sakte hain.")
    target = msg.reply_to_message
    if not target: return await msg.reply("↩️ Reply karke /purge likho.")
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
        try: await status.delete()
        except Exception: pass
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
