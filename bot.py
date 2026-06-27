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

from pyrogram import Client as PyroClient
from pyrogram.errors import (
    PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, BadRequest, FloodWait
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
CREATE TABLE IF NOT EXISTS broadcast_log (
    broadcast_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at       TEXT,
    total_sent    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS broadcast_msgs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id INTEGER,
    user_id      INTEGER,
    msg_id       INTEGER
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
    return build_welcome_keyboard()

def build_welcome_keyboard() -> InlineKeyboardMarkup | None:
    rows = []
    raw = get_setting("welcome_buttons")
    if raw:
        try:
            data = json.loads(raw)
            for row in data:
                rows.append([InlineKeyboardButton(**btn) for btn in row])
        except Exception:
            pass
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

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
    if mention and "{name}" in caption:
        text = caption.replace("{name}", mention)
    elif mention:
        text = f"{mention}\n{caption}" if caption else mention
    else:
        text = caption
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
    if mention and "{name}" in caption:
        text = caption.replace("{name}", mention)
    elif mention:
        text = f"{mention}\n{caption}" if caption else mention
    else:
        text = caption
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
    report          = State()
    episodes        = State()
    approval_image  = State()
    approval_link   = State()

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
_login_clients: dict[int, PyroClient] = {}

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

def main_menu_keyboard(uid: int = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⚙️ • S E T U P •", callback_data="menu:setup"),
            InlineKeyboardButton(text="🌸 • W E L C O M E •", callback_data="menu:welcome"),
        ],
        [
            InlineKeyboardButton(text="📡 • B R O A D C A S T •", callback_data="menu:broadcast"),
        ],
        [
            InlineKeyboardButton(text="👑 • A D M I N •", callback_data="menu:admin"),
            InlineKeyboardButton(text="🧹 • U S E R S •", callback_data="menu:usermgmt"),
        ],
        [
            InlineKeyboardButton(text="🛡️ • M O D •", callback_data="menu:moderation"),
            InlineKeyboardButton(text="💠 • O T H E R •", callback_data="menu:other"),
        ],
    ]
    # Session panel — sirf owner ko dikhao
    if uid == OWNER_ID:
        rows[1].append(InlineKeyboardButton(text="🔑 • S E S S I O N •", callback_data="menu:session"))
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_menu_page(target, page: str, uid: int = None):
    """Edit a message OR send a new one with the menu page."""
    if page == "main":
        text = main_menu_text()
        kb   = main_menu_keyboard(uid)
    elif page in MENU_PAGES:
        data = MENU_PAGES[page]
        # Dynamic pages rebuild buttons (e.g. chats list)
        if callable(data.get("build")):
            import inspect
            fn = data["build"]
            if inspect.iscoroutinefunction(fn):
                buttons = await fn()
            else:
                buttons = fn()
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

async def refresh_chat_titles():
    """NULL ya unknown title wale chats ko Bot API se live fetch karke update karo."""
    cur.execute("SELECT chat_id FROM chats WHERE title IS NULL OR title='' OR title LIKE 'Channel %' OR title LIKE 'Group %' OR title LIKE 'Private %'")
    stale = cur.fetchall()
    for (chat_id,) in stale:
        try:
            chat = await bot.get_chat(chat_id)
            real_title = chat.title or str(chat_id)
            # chat_type bhi detect karo
            ctype = chat.type.value if hasattr(chat.type, "value") else str(chat.type)
            if ctype == "channel":
                db_type = "channel"
            elif ctype in ("group", "supergroup"):
                db_type = ctype
            else:
                db_type = "channel"
            cur.execute("UPDATE chats SET title=?, chat_type=? WHERE chat_id=?", (real_title, db_type, chat_id))
        except Exception:
            pass
    conn.commit()

async def build_setup_buttons():
    # Pehle stale titles refresh karo
    await refresh_chat_titles()

    cur.execute("SELECT chat_id, title, chat_type, accept FROM chats ORDER BY chat_type")
    rows = cur.fetchall()
    buttons = []

    channels = [(cid, title, acc) for cid, title, ctype, acc in rows if ctype == "channel"]
    groups   = [(cid, title, acc) for cid, title, ctype, acc in rows if ctype != "channel"]

    # ── CHANNELS SECTION ──
    if channels:
        buttons.append([InlineKeyboardButton(text="━━━━ CHANNELS ━━━━", callback_data="noop")])
        for chat_id, title, accept in channels:
            st   = "🟢" if accept else "🔴"
            name = (title or str(chat_id))[:25]
            buttons.append([
                InlineKeyboardButton(text=f"{st} {name}", callback_data=f"chat_toggle:{chat_id}"),
                InlineKeyboardButton(text="OLD", callback_data=f"chat_acceptold:{chat_id}"),
                InlineKeyboardButton(text="DEL", callback_data=f"chat_remove:{chat_id}"),
            ])

    # ── GROUPS SECTION ──
    if groups:
        buttons.append([InlineKeyboardButton(text="━━━━ GROUPS ━━━━", callback_data="noop")])
        for chat_id, title, accept in groups:
            st   = "🟢" if accept else "🔴"
            name = (title or str(chat_id))[:25]
            buttons.append([
                InlineKeyboardButton(text=f"{st} {name}", callback_data=f"chat_toggle:{chat_id}"),
                InlineKeyboardButton(text="OLD", callback_data=f"chat_acceptold:{chat_id}"),
                InlineKeyboardButton(text="DEL", callback_data=f"chat_remove:{chat_id}"),
            ])

    if not channels and not groups:
        buttons.append([InlineKeyboardButton(text="Koi chat nahi — niche se add karo", callback_data="noop")])

    # ── ACTION BUTTONS ──
    buttons.append([InlineKeyboardButton(text="━━━━━━━━━━━━━━━━━━━", callback_data="noop")])
    buttons += [
        [InlineKeyboardButton(text="Add Channel / Group", callback_data="cmd:addchat")],
        [InlineKeyboardButton(text="🟢 Auto Accept ON",  callback_data="cmd:autoaccept_on"),
         InlineKeyboardButton(text="🔴 Auto Accept OFF", callback_data="cmd:autoaccept_off")],
        [InlineKeyboardButton(text="Log Group Set", callback_data="cmd:setlog")],
        [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
    ]
    return buttons

def build_session_buttons():
    """Session panel ke buttons — logged in ho to account info + channel/group list."""
    cur.execute("SELECT phone, api_id FROM tg_sessions LIMIT 1")
    row = cur.fetchone()
    if not row:
        # Not logged in
        return [
            [InlineKeyboardButton(text="Login", callback_data="cmd:login")],
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
        ]
    phone, api_id = row
    # Logged in state buttons
    buttons = [
        [InlineKeyboardButton(text=f"✅ Connected: {phone}", callback_data="session:info")],
        [InlineKeyboardButton(text="🔄 Channels/Groups Fetch", callback_data="session:fetch")],
        [InlineKeyboardButton(text="⏮️ Accept All (All Chats)", callback_data="session:acceptall"),
         InlineKeyboardButton(text="🚫 Reject All", callback_data="session:rejectall")],
        [InlineKeyboardButton(text="🚪 Logout", callback_data="cmd:logout")],
        [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
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
            "『 WELCOME PANEL 』\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Join karne walon ka swagat karo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="Welcome Save", callback_data="cmd:save"),
             InlineKeyboardButton(text="Button Add", callback_data="cmd:addbutton")],
            [InlineKeyboardButton(text="Buttons Clear", callback_data="cmd:clearbuttons")],
            [InlineKeyboardButton(text="Approval Image Set", callback_data="cmd:setapprovalimage"),
             InlineKeyboardButton(text="Approval Link Set", callback_data="cmd:setapprovallink")],
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
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
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
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
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
        ]
    },
    "usermgmt": {
        "title": (
            "『 🧹 』<b>USER PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<i>Users ko manage karo</i>"
        ),
        "buttons": [
            [InlineKeyboardButton(text="Blacklist", callback_data="cmd:blacklist"),
             InlineKeyboardButton(text="✅ Unblacklist", callback_data="cmd:unblacklist")],
            [InlineKeyboardButton(text="📤 Export Users", callback_data="cmd:exportusers"),
             InlineKeyboardButton(text="💾 DB Backup", callback_data="cmd:backup")],
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
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
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
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
            [InlineKeyboardButton(text="BACK", callback_data="menu:main")],
        ]
    },
}

CMD_HINTS = {
    "addchannel":     "📢 <b>Channel add karo:</b>\n\n<code>/addchannel @username</code>\n<code>/addchannel -100xxxxxxxxxx</code>\n\n<i>Bot ko pehle channel mein admin banao.</i>",
    "addgroup":       "👥 <b>Group add karo:</b>\n\n<code>/addgroup @username</code>\n<code>/addgroup -100xxxxxxxxxx</code>\n\n<i>Bot ko pehle group mein admin banao.</i>",
    "removechat":     "🗑 <b>Chat remove karo:</b>\n\n<code>/removechat @username</code>\n<code>/removechat -100xxxxxxxxxx</code>",
    "chats":          "📋 <b>Chat list:</b>\n\n<code>/chats</code>",
    "setlog":         "📋 <b>Log group set karo:</b>\n\n<code>/setlog -100xxxxxxxxxx</code>",
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
        await msg.reply(main_menu_text(), reply_markup=main_menu_keyboard(msg.from_user.id), parse_mode="HTML")
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
    # Session panel — sirf owner access kar sakta hai
    if page == "session" and cb.from_user.id != OWNER_ID:
        return await cb.answer("⚠️ Session panel sirf owner ke liye hai.", show_alert=True)
    await show_menu_page(cb.message, page, uid=cb.from_user.id)
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
    client, err = await _get_session_client(admin_id)
    if err:
        await status_msg.edit_text(f"❌ {err}")
        return
    try:
        if specific_chat_id:
            chats_to_process = [specific_chat_id]
        else:
            cur.execute("SELECT chat_id FROM chats WHERE accept=1")
            chats_to_process = [r[0] for r in cur.fetchall()]
        accepted = 0
        failed   = 0
        err_detail = ""
        from pyrogram.raw import functions, types as raw_types

        for cid in chats_to_process:
            try:
                # Step 1: Get chat entity to warm cache AND get valid peer
                chat_entity = None
                try:
                    chat_entity = await client.get_chat(cid)
                except Exception as warm_err:
                    log.warning(f"Cache warm failed {cid}: {warm_err}")

                # Step 2: Resolve peer — MUST have valid access_hash
                peer = None
                try:
                    peer = await client.resolve_peer(cid)
                except Exception as pe:
                    log.warning(f"resolve_peer failed {cid}: {pe}")
                    # Try building InputChannel from chat entity if available
                    if chat_entity is not None:
                        try:
                            access_hash = getattr(chat_entity, "access_hash", None)
                            raw_id = abs(cid)
                            id_str = str(raw_id)
                            channel_id = int(id_str[3:]) if id_str.startswith("100") else raw_id
                            if access_hash:
                                peer = raw_types.InputChannel(channel_id=channel_id, access_hash=access_hash)
                        except Exception as pe2:
                            log.warning(f"InputChannel build failed {cid}: {pe2}")

                if peer is None:
                    raise Exception(f"Could not resolve peer for chat {cid}. Make sure the userbot is a member/admin of this chat.")

                # Step 3: Fetch pending requests
                result = await client.invoke(
                    functions.messages.GetChatInviteImporters(
                        peer=peer,
                        requested=True,
                        offset_date=0,
                        offset_user=raw_types.InputUserEmpty(),
                        limit=100,
                    )
                )
                importers = getattr(result, "importers", [])

                # Step 4: Approve each
                for imp in importers:
                    try:
                        user_peer = await client.resolve_peer(imp.user_id)
                        await client.invoke(
                            functions.messages.HideChatJoinRequest(
                                peer=peer,
                                user_id=user_peer,
                                approved=True,
                            )
                        )
                        accepted += 1
                        await asyncio.sleep(0.5)
                    except Exception as e2:
                        log.warning(f"approve failed: {e2}")
                        err_detail = str(e2)
                        failed += 1

            except Exception as e:
                log.warning(f"acceptold error {cid}: {e}")
                err_detail = str(e)
                failed += 1
        result_text = (
            f"<b>Old Requests Done!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"Accepted: <b>{accepted}</b>\n"
            f"Failed: <b>{failed}</b>"
        )
        if failed and err_detail:
            result_text += f"\n\n<b>Error:</b>\n<code>{err_detail[:300]}</code>"
        await status_msg.edit_text(result_text, parse_mode="HTML", reply_markup=BACK_KB("setup"))
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")
    finally:
        await client.stop()


CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Cancel", callback_data="flow:cancel")]
])

@dp.callback_query(F.data == "flow:cancel")
async def cb_flow_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_menu_page(cb.message, "main", uid=cb.from_user.id)
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
        cur.execute("SELECT phone, api_id FROM tg_sessions LIMIT 1")
        row = cur.fetchone()
        if row:
            phone, api_id = row
            await cb.message.answer(
                f"<b>Session Info</b>\n"
                f"➺ <b>Phone:</b> <code>{phone}</code>\n"
                f"➺ <b>API ID:</b> <code>{api_id}</code>\n"
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
            "<b>Episodes link bhejo:</b>\n\n"
            "<code>https://t.me/yourlink</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "setapprovalimage":
        await state.set_state(SetLinkFlow.approval_image)
        cur_img = get_setting("approval_image", "")
        await cb.message.answer(
            "<b>Approval Image set karo:</b>\n\n"
            "Ek photo bhejo — woh image approval DM mein jayegi.\n"
            f"Current: <code>{cur_img[:40] if cur_img else 'Not set'}</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return

    if cmd == "setapprovallink":
        await state.set_state(SetLinkFlow.approval_link)
        cur_link = get_setting("approval_link", "")
        await cb.message.answer(
            "<b>Approval Link set karo:</b>\n\n"
            "Channel/Group ka invite link bhejo.\n"
            f"Current: <code>{cur_link or 'Not set'}</code>\n\n"
            "<code>https://t.me/+xxxxxxx</code>\n\n"
            "<i>/skip se cancel karo.</i>",
            parse_mode="HTML",
            reply_markup=CANCEL_KB
        )
        return


    if cmd in ("addchannel", "addgroup", "addchat"):
        await state.set_state(AddChatFlow.addchannel)
        await cb.message.answer(
            "➕ <b>Channel / Group Add Karo</b>\n"
            "━━━━━━━━━━▧▣▧━━━━━━━━━━\n\n"
            "<b>Method 1 — ID/Username:</b>\n"
            "<code>@username</code>\n"
            "<code>-100xxxxxxxxxx</code>\n\n"
            "<b>Method 2 — Forward Message:</b>\n"
            "Us channel/group se koi bhi message yahan forward karo\n"
            "<i>(Private chats ke liye best method)</i>\n\n"
            "🤖 Bot khud detect karega — channel hai ya group\n"
            "⚠️ Bot ko pehle admin banao (Add Members permission)\n"
            "<i>/skip se cancel karo</i>",
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
        cur_log = get_setting("log_group", "❌ Not set")
        await cb.message.answer(
            f"📋 <b>Log group set karo:</b>\n"
            f"Current: <code>{cur_log}</code>\n\n"
            f"Log group ka ID bhejo:\n"
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
    """DB se session fetch karke connected Pyrogram client return karo."""
    cur.execute("SELECT api_id, api_hash, session FROM tg_sessions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT api_id, api_hash, session FROM tg_sessions LIMIT 1")
        row = cur.fetchone()
    if not row:
        return None, "❌ Session nahi hai. Pehle /login karo."
    api_id, api_hash, session_str = row
    try:
        client = PyroClient(
            name="userbot",
            api_id=int(api_id),
            api_hash=api_hash,
            session_string=session_str,
            in_memory=True,
            no_updates=True
        )
        await client.start()
        return client, None
    except Exception as e:
        return None, f"❌ Session connect failed: {e}"

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

async def _fetch_and_show_chats(user_id: int, chat_id: int, status_msg_id: int, page: int = 0):
    client, err = await _get_session_client(user_id)
    if err:
        await bot.edit_message_text(err, chat_id=chat_id, message_id=status_msg_id)
        return
    try:
        all_chats = []
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if not chat:
                continue
            # Sirf admin/owner wale
            from pyrogram.enums import ChatType
            if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP):
                continue
            member = None
            try:
                member = await client.get_chat_member(chat.id, "me")
            except Exception:
                continue
            from pyrogram.enums import ChatMemberStatus
            if member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                continue
            if chat.type == ChatType.CHANNEL:
                ctype = "channel"
            else:
                ctype = "supergroup"
            cid   = chat.id
            uname = chat.username
            all_chats.append((cid, chat.title or str(cid), uname, ctype))

        await client.stop()

        cur.execute("SELECT chat_id FROM chats")
        added_ids = {r[0] for r in cur.fetchall()}

        total      = len(all_chats)
        PAGE_SIZE  = 8
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page        = max(0, min(page, total_pages - 1))
        slice_      = all_chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

        ch_count  = sum(1 for _, _, _, t in all_chats if t == "channel")
        grp_count = total - ch_count

        lines = [
            f"『 📡 』<b>Account ke Chats</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"📢 Channels: <b>{ch_count}</b>  |  👥 Groups: <b>{grp_count}</b>\n"
            f"📄 Page {page+1}/{total_pages}\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━"
        ]

        buttons = []
        for cid, title, uname, ctype in slice_:
            emoji = "📢" if ctype == "channel" else "👥"
            short = title[:28]
            lines.append(f"\n{emoji} <b>{title}</b>")
            if uname:
                lines.append(f"   @{uname} | <code>{cid}</code>")
            else:
                lines.append(f"   <code>{cid}</code>")
            if cid in added_ids:
                buttons.append([InlineKeyboardButton(text=f"✅ {short[:22]} (added)", callback_data="noop")])
            else:
                buttons.append([InlineKeyboardButton(text=f"➕ {emoji} {short[:22]}", callback_data=f"fetchadd:{cid}:{ctype}:{title[:30]}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"fetchpage:{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"fetchpage:{page+1}"))
        if nav:
            buttons.append(nav)

        buttons += [
            [InlineKeyboardButton(text="⏮️ Accept All Pending", callback_data="session:acceptall"),
             InlineKeyboardButton(text="🚫 Reject All",         callback_data="session:rejectall")],
            [InlineKeyboardButton(text="📢 Channels Accept",    callback_data="session:accept_channels"),
             InlineKeyboardButton(text="👥 Groups Accept",      callback_data="session:accept_groups")],
            [InlineKeyboardButton(text="🔙 Session Panel",      callback_data="menu:session")],
        ]

        await bot.edit_message_text(
            "\n".join(lines),
            chat_id=chat_id, message_id=status_msg_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Fetch failed:\n<code>{e}</code>",
            chat_id=chat_id, message_id=status_msg_id, parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("fetchpage:"))
async def cb_fetchpage(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    page = int(cb.data.split(":")[1])
    await cb.answer()
    status = await cb.message.edit_text("⏳ Loading...", parse_mode="HTML")
    asyncio.create_task(_fetch_and_show_chats(cb.from_user.id, cb.message.chat.id, status.message_id, page=page))

@dp.callback_query(F.data.startswith("fetchadd:"))
async def cb_fetchadd(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ Sirf admins.", show_alert=True)
    parts   = cb.data.split(":", 3)
    cid     = int(parts[1])
    ctype   = parts[2]
    title   = parts[3] if len(parts) > 3 else str(cid)

    # Already added check
    cur.execute("SELECT chat_id FROM chats WHERE chat_id=?", (cid,))
    if cur.fetchone():
        return await cb.answer(f"✅ {title[:20]} already added hai!", show_alert=True)

    # Admin check
    admin = await check_bot_admin(cid)
    if not admin["is_admin"]:
        return await cb.answer(f"❌ Bot {title[:20]} mein admin nahi hai!", show_alert=True)
    if not admin["can_invite"]:
        return await cb.answer(f"⚠️ Add Members permission nahi — {title[:20]}", show_alert=True)

    cur.execute(
        "INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
        (cid, title, None, ctype)
    )
    conn.commit()
    emoji = "📢" if ctype == "channel" else "👥"
    await cb.answer(f"{emoji} {title[:25]} added! ✅", show_alert=True)
    # Refresh current page
    status = await cb.message.edit_text("⏳ Refreshing...", parse_mode="HTML")
    asyncio.create_task(_fetch_and_show_chats(cb.from_user.id, cb.message.chat.id, status.message_id))

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
        from pyrogram.enums import ChatType
        # DB se chats lo filter ke saath
        cur.execute("SELECT chat_id, title, chat_type FROM chats WHERE accept=1")
        db_chats = cur.fetchall()

        targets = []
        for cid, title, ctype in db_chats:
            if filter_type == "channels" and ctype != "channel":
                continue
            if filter_type == "groups" and ctype == "channel":
                continue
            targets.append((cid, title or str(cid)))

        action_word = "Accept" if approve else "Reject"
        total_ok   = 0
        total_fail = 0
        details    = []

        for cid, title in targets:
            ok = fail = 0
            try:
                from pyrogram.raw import functions, types as raw_types
                chat_entity = None
                try:
                    chat_entity = await client.get_chat(cid)
                except Exception: pass
                peer = None
                try:
                    peer = await client.resolve_peer(cid)
                except Exception as pe:
                    log.warning(f"resolve_peer failed {cid}: {pe}")
                    if chat_entity is not None:
                        try:
                            access_hash = getattr(chat_entity, "access_hash", None)
                            raw_id = abs(cid)
                            id_str = str(raw_id)
                            ch_id = int(id_str[3:]) if id_str.startswith("100") else raw_id
                            if access_hash:
                                peer = raw_types.InputChannel(channel_id=ch_id, access_hash=access_hash)
                        except Exception:
                            pass
                if peer is None:
                    raise Exception(f"Could not resolve peer for chat {cid}.")
                result = await client.invoke(
                    functions.messages.GetChatInviteImporters(
                        peer=peer, requested=True,
                        offset_date=0, offset_user=raw_types.InputUserEmpty(), limit=100,
                    )
                )
                for imp in getattr(result, "importers", []):
                    try:
                        user_peer = await client.resolve_peer(imp.user_id)
                        await client.invoke(
                            functions.messages.HideChatJoinRequest(
                                peer=peer,
                                user_id=user_peer, approved=approve,
                            )
                        )
                        ok += 1
                        await asyncio.sleep(0.5)
                    except Exception:
                        fail += 1
                if ok or fail:
                    details.append(f"{'✅' if approve else '🚫'} <b>{title[:22]}</b>: {ok} done, {fail} fail")
                    total_ok   += ok
                    total_fail += fail
            except Exception as e:
                details.append(f"⚠️ <b>{title[:22]}</b>: {str(e)[:40]}")

        await client.stop()

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

async def _do_acceptold(chat_id: int, specific_chat, notify_user_id: int = None):
    """Accept old pending requests — reusable."""
    uid = notify_user_id or OWNER_ID
    client, err = await _get_session_client(uid)
    if err:
        await bot.send_message(chat_id, f"❌ {err}")
        return
    try:
        accepted = 0
        failed   = 0
        err_detail = ""
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
                from pyrogram.raw import functions, types as raw_types
                chat_entity = None
                try:
                    chat_entity = await client.get_chat(cid)
                except Exception as warm_err:
                    log.warning(f"Cache warm failed {cid}: {warm_err}")
                peer = None
                try:
                    peer = await client.resolve_peer(cid)
                except Exception as pe:
                    log.warning(f"resolve_peer failed {cid}: {pe}")
                    if chat_entity is not None:
                        try:
                            access_hash = getattr(chat_entity, "access_hash", None)
                            raw_id = abs(cid)
                            id_str = str(raw_id)
                            channel_id = int(id_str[3:]) if id_str.startswith("100") else raw_id
                            if access_hash:
                                peer = raw_types.InputChannel(channel_id=channel_id, access_hash=access_hash)
                        except Exception:
                            pass
                if peer is None:
                    raise Exception(f"Could not resolve peer for chat {cid}. Make sure the userbot is a member/admin of this chat.")
                result = await client.invoke(
                    functions.messages.GetChatInviteImporters(
                        peer=peer, requested=True,
                        offset_date=0, offset_user=raw_types.InputUserEmpty(), limit=100,
                    )
                )
                for imp in getattr(result, "importers", []):
                    try:
                        user_peer = await client.resolve_peer(imp.user_id)
                        await client.invoke(
                            functions.messages.HideChatJoinRequest(
                                peer=peer,
                                user_id=user_peer, approved=True,
                            )
                        )
                        accepted += 1
                        await asyncio.sleep(0.5)
                    except Exception as e2:
                        log.warning(f"approve failed: {e2}")
                        failed += 1
            except Exception as e:
                log.warning(f"acceptold error for {cid}: {e}")
                err_detail = str(e)
                failed += 1

        await bot.send_message(
            chat_id,
            f"<b>Old Requests Done!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"Accepted: <b>{accepted}</b>\n"
            f"Failed: <b>{failed}</b>"
            + (f"\n\n<b>Error:</b>\n<code>{err_detail[:200]}</code>" if failed and err_detail else ""),
            parse_mode="HTML"
        )
    finally:
        await client.stop()

async def _flow_add_chat_common(msg: Message, state: FSMContext, forced_type: str = None):
    """
    Channel/Group add karo — 2 tarike:
    1. @username ya -100ID type karo
    2. Us chat se koi bhi message forward karo (private chats ke liye best)
    Bot khud title aur type detect karega.
    """
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.strip().startswith("/skip"):
        await state.clear()
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("setup"))

    await state.clear()

    chat_id   = None
    title     = None
    username  = None
    actual_type = None

    # ── METHOD 1: Forwarded message se detect ──────────────────
    fwd = msg.forward_origin if hasattr(msg, "forward_origin") else None
    if fwd:
        try:
            # aiogram v3: forward_origin.chat
            src_chat = getattr(fwd, "chat", None) or getattr(fwd, "sender_chat", None)
            if src_chat:
                chat_id  = src_chat.id
                title    = src_chat.title or None
                username = getattr(src_chat, "username", None)
                ctype_val = src_chat.type.value if hasattr(src_chat.type, "value") else str(src_chat.type)
                if ctype_val == "channel":
                    actual_type = "channel"
                elif ctype_val in ("group", "supergroup"):
                    actual_type = ctype_val
                else:
                    actual_type = "channel"
        except Exception:
            pass

    # ── METHOD 2: @username ya -100ID text input ───────────────
    if not chat_id and msg.text:
        raw   = msg.text.strip()
        parts = raw.split(maxsplit=1)
        chat_raw = parts[0]
        status = await msg.reply(f"⏳ Resolving <code>{chat_raw}</code>...", parse_mode="HTML")
        result = await resolve_chat_id(chat_raw)
        if not result:
            return await status.edit_text(
                "❌ Chat resolve nahi hua.\n\n"
                "<b>2 tarike hain:</b>\n"
                "• <code>@username</code> ya <code>-100ID</code> type karo\n"
                "• Ya us chat se koi bhi message yahan <b>forward</b> karo",
                parse_mode="HTML", reply_markup=BACK_KB("setup")
            )
        chat_id, title, username, ctype = result
        if ctype == "channel":
            actual_type = "channel"
        elif ctype in ("group", "supergroup"):
            actual_type = ctype
        else:
            actual_type = forced_type or "channel"
    elif chat_id:
        # Forward method — status message send karo
        status = await msg.reply(f"⏳ Verifying...", parse_mode="HTML")
    else:
        return await msg.reply(
            "❌ Kuch samajh nahi aaya.\n\n"
            "• <code>@username</code> ya <code>-100ID</code> bhejo\n"
            "• Ya us chat se message forward karo",
            parse_mode="HTML", reply_markup=BACK_KB("setup")
        )

    # ── Title fallback ─────────────────────────────────────────
    # get_chat try karo agar title missing hai
    if not title:
        try:
            chat_obj = await bot.get_chat(chat_id)
            title    = chat_obj.title or None
            username = getattr(chat_obj, "username", None) or username
            if not actual_type or actual_type == "channel":
                ctype_val = chat_obj.type.value if hasattr(chat_obj.type, "value") else str(chat_obj.type)
                if ctype_val in ("group", "supergroup"):
                    actual_type = ctype_val
        except Exception:
            pass

    if not title:
        title = f"Chat {chat_id}"

    # ── Admin check ────────────────────────────────────────────
    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status.edit_text(
            f"❌ Bot <b>{title}</b> mein admin nahi hai!\n"
            f"<i>Bot ko admin banao (Add Members permission) phir dobara try karo.</i>",
            parse_mode="HTML", reply_markup=BACK_KB("setup")
        )
    if not admin["can_invite"]:
        return await status.edit_text(
            f"⚠️ Bot admin hai lekin <b>Add Members</b> permission nahi hai.",
            parse_mode="HTML", reply_markup=BACK_KB("setup")
        )

    # ── Save to DB ─────────────────────────────────────────────
    cur.execute(
        "INSERT OR REPLACE INTO chats (chat_id, title, username, chat_type, accept) VALUES (?,?,?,?,1)",
        (chat_id, title, username, actual_type)
    )
    conn.commit()

    uname      = f"@{username}" if username else "—"
    type_emoji = "📢" if actual_type == "channel" else "👥"
    type_label = "Channel" if actual_type == "channel" else "Group"
    await status.edit_text(
        f"✅ <b>{type_emoji} {type_label} Added!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Name:</b> {title}\n"
        f"➺ <b>Type:</b> {type_emoji} {type_label}\n"
        f"➺ <b>Username:</b> {uname}\n"
        f"➺ <b>ID:</b> <code>{chat_id}</code>\n"
        f"➺ <b>Auto-Accept:</b> 🟢 ON",
        parse_mode="HTML", reply_markup=BACK_KB("setup")
    )

@dp.message(AddChatFlow.addchannel)
async def flow_addchannel(msg: Message, state: FSMContext):
    await _flow_add_chat_common(msg, state, forced_type=None)

@dp.message(AddChatFlow.addgroup)
async def flow_addgroup(msg: Message, state: FSMContext):
    await _flow_add_chat_common(msg, state, forced_type=None)

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
    set_setting("log_group", str(log_id))
    await msg.reply(f"✅ Log group set: <code>{log_id}</code>", parse_mode="HTML", reply_markup=BACK_KB("setup"))

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
        cur_log = get_setting("log_group", "❌ Not set")
        return await msg.reply(
            f"📋 <b>Log Group:</b> <code>{cur_log}</code>\n\n"
            f"Usage: <code>/setlog -100xxxxxxxxxx</code>",
            parse_mode="HTML"
        )
    try:
        log_id = int(args[1].strip())
    except ValueError:
        return await msg.reply("❌ Valid chat ID do (e.g. -100xxxxxxxxxx)")
    set_setting("log_group", str(log_id))
    await msg.reply(f"✅ Log group set: <code>{log_id}</code>", parse_mode="HTML")

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
        return await msg.reply(
            f"✅ <b>Welcome saved!</b> Type: {media_type}",
            parse_mode="HTML", reply_markup=BACK_KB("welcome")
        )
    rows_data, btn_list = parse_button_text(msg.text)
    if not rows_data:
        return await msg.reply("❌ Format galat hai.\n<code>Name | https://link</code>", parse_mode="HTML")
    set_setting("welcome_buttons", json.dumps(rows_data))
    await state.clear()
    await msg.reply(
        f"✅ <b>Welcome fully saved!</b>\n"
        f"➺ Type: {media_type}\n"
        f"➺ Buttons:\n{btn_list}",
        parse_mode="HTML", reply_markup=BACK_KB("welcome")
    )

@dp.message(SaveFlow.waiting_buttons, F.photo | F.video | F.animation)
async def save_got_media(msg: Message, state: FSMContext):
    """Button flow mein directly media bheja — save karo."""
    if not is_admin(msg.from_user.id): return
    if msg.video:
        set_setting("welcome_type", "video")
        set_setting("welcome_file_id", msg.video.file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🎥 Video"
    elif msg.animation:
        set_setting("welcome_type", "animation")
        set_setting("welcome_file_id", msg.animation.file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🎞 GIF"
    elif msg.photo:
        set_setting("welcome_type", "photo")
        set_setting("welcome_file_id", msg.photo[-1].file_id)
        set_setting("welcome_caption", msg.caption or "")
        media_type = "🖼 Photo"
    else:
        return await msg.reply("❌ Ye media type support nahi.")
    await state.update_data(media_type=media_type)
    await msg.reply(
        f"✅ <b>{media_type} saved!</b>\n\n"
        f"Ab buttons bhejo (optional):\n"
        f"<code>Button Name | https://link</code>\n\n"
        f"Buttons nahi chahiye → /skip karo.",
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

@dp.message(SetLinkFlow.approval_image, F.photo)
async def flow_set_approval_image(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    file_id = msg.photo[-1].file_id
    await state.clear()
    set_setting("approval_image", file_id)
    await msg.reply(
        "✅ <b>Approval image saved!</b>\nAb join request approve hone pe yeh image DM mein jayegi.",
        parse_mode="HTML", reply_markup=BACK_KB("welcome")
    )

@dp.message(SetLinkFlow.approval_image, F.text)
async def flow_set_approval_image_skip(msg: Message, state: FSMContext):
    if msg.text.strip().startswith("/skip"):
        await state.clear()
        return await msg.reply("Cancelled.", reply_markup=BACK_KB("welcome"))
    await msg.reply("Photo bhejo ya /skip karo.")

@dp.message(SetLinkFlow.approval_link)
async def flow_set_approval_link(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.strip().startswith("/skip"):
        await state.clear()
        return await msg.reply("Cancelled.", reply_markup=BACK_KB("welcome"))
    link = msg.text.strip() if msg.text else ""
    if not link.startswith("http"):
        return await msg.reply("Valid URL do (https:// se start karo)\n/skip se cancel karo.")
    await state.clear()
    set_setting("approval_link", link)
    await msg.reply(
        f"✅ <b>Approval link saved!</b>\n<code>{link}</code>",
        parse_mode="HTML", reply_markup=BACK_KB("welcome")
    )

@dp.message(SetLinkFlow.report)
async def flow_set_report_link(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.strip().startswith("/skip"):
        await state.clear()
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("welcome"))
    link = msg.text.strip() if msg.text else ""
    if not link.startswith("http"):
        return await msg.reply(
            "❌ Valid URL do (https:// se start hona chahiye)\n<i>/skip se cancel karo</i>",
            parse_mode="HTML"
        )
    await state.clear()
    set_setting("report_link", link)
    await msg.reply(
        f"✅ <b>Report Link saved!</b>\n<code>{link}</code>",
        parse_mode="HTML", reply_markup=BACK_KB("welcome")
    )

@dp.message(SetLinkFlow.episodes)
async def flow_set_episodes_link(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    if msg.text and msg.text.strip().startswith("/skip"):
        await state.clear()
        return await msg.reply("❌ Cancelled.", reply_markup=BACK_KB("welcome"))
    link = msg.text.strip() if msg.text else ""
    if not link.startswith("http"):
        return await msg.reply(
            "❌ Valid URL do (https:// se start hona chahiye)\n<i>/skip se cancel karo</i>",
            parse_mode="HTML"
        )
    await state.clear()
    set_setting("episodes_link", link)
    await msg.reply(
        f"✅ <b>Episodes Link saved!</b>\n<code>{link}</code>",
        parse_mode="HTML", reply_markup=BACK_KB("welcome")
    )

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
        client = PyroClient(
            name="login_temp",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
            no_updates=True
        )
        await client.connect()
        sent = await client.send_code(phone)
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
    client: PyroClient = _login_clients.get(msg.from_user.id)

    if not client:
        await state.clear()
        return await msg.reply("❌ Session expire ho gaya. /login se dobara shuru karo.")

    status = await msg.reply("⏳ Verify kar raha hoon...")
    try:
        await client.sign_in(phone, phone_code_hash, otp)
        await _save_session(msg, state, client, data)
        await status.delete()
    except SessionPasswordNeeded:
        await state.set_state(LoginFlow.password)
        await status.edit_text(
            "🔒 2FA enabled hai!\n\n"
            "➺ Password bhejo:",
            parse_mode="HTML"
        )
    except (PhoneCodeInvalid, PhoneCodeExpired):
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
    client: PyroClient = _login_clients.get(msg.from_user.id)

    if not client:
        await state.clear()
        return await msg.reply("❌ Session expire. /login se dobara karo.")

    status = await msg.reply("⏳ 2FA verify kar raha hoon...")
    try:
        await client.check_password(password)
        await _save_session(msg, state, client, data)
        await status.delete()
    except BadRequest:
        await status.edit_text("❌ Password galat hai. Dobara bhejo:")
    except Exception as e:
        await state.clear()
        _login_clients.pop(msg.from_user.id, None)
        await status.edit_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

async def _save_session(msg: Message, state: FSMContext, client: PyroClient, data: dict):
    session_str = await client.export_session_string()
    api_id   = data["api_id"]
    api_hash = data["api_hash"]
    phone    = data["phone"]
    cur.execute(
        "INSERT OR REPLACE INTO tg_sessions (user_id, api_id, api_hash, phone, session) VALUES (?,?,?,?,?)",
        (msg.from_user.id, api_id, api_hash, phone, session_str)
    )
    conn.commit()
    await client.stop()
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
    total_ok   = 0
    total_fail = 0
    total_none = 0
    details    = []

    try:
        client = PyroClient(
            name="acceptold",
            api_id=int(api_id),
            api_hash=api_hash,
            session_string=session_str,
            in_memory=True,
            no_updates=True
        )
        await client.start()

        for chat_id in chat_ids:
            title = chat_titles.get(chat_id, str(chat_id))
            ok = fail = 0
            try:
                from pyrogram.raw import functions, types as raw_types
                chat_entity = None
                try:
                    chat_entity = await client.get_chat(chat_id)
                except Exception: pass
                peer = None
                try:
                    peer = await client.resolve_peer(chat_id)
                except Exception as pe:
                    log.warning(f"resolve_peer failed {chat_id}: {pe}")
                    if chat_entity is not None:
                        try:
                            access_hash = getattr(chat_entity, "access_hash", None)
                            raw_id = abs(chat_id)
                            id_str = str(raw_id)
                            ch_id = int(id_str[3:]) if id_str.startswith("100") else raw_id
                            if access_hash:
                                peer = raw_types.InputChannel(channel_id=ch_id, access_hash=access_hash)
                        except Exception:
                            pass
                if peer is None:
                    raise Exception(f"Could not resolve peer for chat {chat_id}. Make sure the userbot is a member/admin of this chat.")
                result = await client.invoke(
                    functions.messages.GetChatInviteImporters(
                        peer=peer, requested=True,
                        offset_date=0, offset_user=raw_types.InputUserEmpty(), limit=100,
                    )
                )
                for imp in getattr(result, "importers", []):
                    try:
                        user_peer = await client.resolve_peer(imp.user_id)
                        await client.invoke(
                            functions.messages.HideChatJoinRequest(
                                peer=peer,
                                user_id=user_peer, approved=True,
                            )
                        )
                        ok += 1
                        await asyncio.sleep(0.5)
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
                total_ok   += ok
                total_fail += fail
                details.append(f"✅ {title[:20]}: {ok} accepted, {fail} fail")

        await client.stop()

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
async def send_join_log(user_id: int, first_name: str, username: str, chat_id: int, chat_title: str, chat_type: str = ""):
    log_ch = get_setting("log_group")
    if not log_ch:
        return
    try:
        uname_str  = f"@{username}" if username else "❌ No username"
        type_emoji = "📢" if chat_type == "channel" else "👥"
        profile    = f"tg://user?id={user_id}"
        cur.execute("SELECT COUNT(*) FROM join_logs WHERE user_id=?", (user_id,))
        total_joins = cur.fetchone()[0]
        await bot.send_message(
            int(log_ch),
            f"👤 <b>New Join Approved</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Name:</b> <a href='{profile}'>{first_name}</a>\n"
            f"➺ <b>Username:</b> {uname_str}\n"
            f"➺ <b>User ID:</b> <code>{user_id}</code>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Chat:</b> {type_emoji} {chat_title}\n"
            f"➺ <b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"➺ <b>Total Joins (user):</b> {total_joins}\n"
            f"➺ <b>Time:</b> {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            parse_mode="HTML"
        )
    except Exception as e:
        log.warning(f"Join log failed: {e}")
        # Try sending simplified message to debug
        try:
            await bot.send_message(int(log_ch), f"⚠️ Log error: <code>{e}</code>", parse_mode="HTML")
        except Exception as e2:
            log.warning(f"Even debug log failed: {e2}")

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

        # ── Approval DM ─────────────────────────────────────────
        approval_img  = get_setting("approval_image", "")
        approval_link = get_setting("approval_link", "")

        fancy_title = title
        dm_caption = (
            f"🎉 ʀᴇǫᴜᴇsᴛ ᴀᴄᴄᴇᴘᴛᴇᴅ!\n\n"
            f"ʏᴏᴜʀ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴀᴄᴄᴇᴘᴛᴇᴅ ꜰᴏʀ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ {fancy_title}.\n\n"
            f"✅ ʏᴏᴜ ᴄᴀɴ ɴᴏᴡ ᴀᴄᴄᴇss ᴀʟʟ ᴏꜰ ɪᴛs ᴄᴏɴᴛᴇɴᴛ.\n\n"
            f"💖 ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴊᴏɪɴɪɴɢ!\n\n"
            f"⚡ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : @Animes_Nakama"
        )
        btns = []
        if approval_link:
            btns.append([InlineKeyboardButton(text=f"ᴊᴏɪɴ {title}", url=approval_link)])
        approval_kb = InlineKeyboardMarkup(inline_keyboard=btns) if btns else None
        try:
            if approval_img:
                await bot.send_photo(
                    user_id,
                    photo=approval_img,
                    caption=dm_caption,
                    reply_markup=approval_kb
                )
            else:
                await bot.send_message(
                    user_id,
                    dm_caption,
                    reply_markup=approval_kb
                )
        except Exception as e:
            log.warning(f"DM send failed for {user_id}: {e}")

        asyncio.create_task(send_join_log(user_id, first_name, username, req.chat.id, title, chat_type))

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
        try:
            await _do_broadcast(msg.chat.id, msg.message_id, bc_type, status)
        except Exception as e:
            await status.edit_text(f"❌ Broadcast error:\n<code>{e}</code>", parse_mode="HTML")
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
    cur.execute(
        "INSERT INTO broadcast_log (sent_at, total_sent) VALUES (?,?)",
        (datetime.now().strftime("%d %b %Y %I:%M %p"), 0)
    )
    conn.commit()
    broadcast_id = cur.lastrowid

    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    if not users:
        try:
            await status_msg.edit_text(
                "❌ <b>Koi user nahi mila!</b>\n"
                "Users tabhi save hote hain jab woh /start karte hain.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    ok, fail, blocked = 0, 0, 0
    for (user_id,) in users:
        try:
            if mode == "forward":
                sent = await bot.forward_message(user_id, source_chat_id, source_msg_id)
            else:
                sent = await bot.copy_message(user_id, source_chat_id, source_msg_id)
            # Pin mode — pin the sent message in user's DM
            if mode == "pin":
                try:
                    await bot.pin_chat_message(user_id, sent.message_id, disable_notification=True)
                except Exception:
                    pass
            cur.execute(
                "INSERT INTO broadcast_msgs (broadcast_id, user_id, msg_id) VALUES (?,?,?)",
                (broadcast_id, user_id, sent.message_id)
            )
            ok += 1
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ["blocked", "bot was blocked", "deactivated", "chat not found", "forbidden"]):
                blocked += 1
            else:
                fail += 1
        await asyncio.sleep(0.05)

    cur.execute("UPDATE broadcast_log SET total_sent=? WHERE broadcast_id=?", (ok, broadcast_id))
    conn.commit()

    mode_label = {"normal": "Normal", "pin": "Pin DM", "forward": "Forward"}.get(mode, mode)
    try:
        await status_msg.edit_text(
            f"<b>Broadcast Done!</b> [{mode_label}]\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"Sent: <b>{ok}</b>\n"
            f"Blocked/Inactive: <b>{blocked}</b>\n"
            f"Failed: <b>{fail}</b>\n"
            f"Total: <b>{ok+blocked+fail}</b>\n\n"
            f"Broadcast ID: <code>{broadcast_id}</code>\n"
            f"Delete karne ke liye: <code>/dBroadcast {broadcast_id}</code>",
            parse_mode="HTML",
            reply_markup=BACK_KB("broadcast")
        )
    except Exception as e:
        log.warning(f"Broadcast status update failed: {e}")

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
    try:
        await _do_broadcast(source_chat_id, source_msg_id, mode, status)
    except Exception as e:
        await status.edit_text(f"❌ Broadcast error:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(Command("dBroadcast", "dbroadcast", "deletebroadcast"))
async def cmd_delete_broadcast(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("Only admins can use this.")
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply(
            "<b>Usage:</b> <code>/dBroadcast {broadcast_id}</code>\n\n"
            "Broadcast ID broadcast karne ke baad milta hai.",
            parse_mode="HTML"
        )
    try:
        broadcast_id = int(args[1].strip())
    except ValueError:
        return await msg.reply("Valid broadcast ID do (number).")

    cur.execute("SELECT user_id, msg_id FROM broadcast_msgs WHERE broadcast_id=?", (broadcast_id,))
    rows = cur.fetchall()
    if not rows:
        return await msg.reply(f"Broadcast ID <code>{broadcast_id}</code> nahi mila ya already delete ho gaya.", parse_mode="HTML")

    status = await msg.reply(f"Deleting broadcast <code>{broadcast_id}</code> from {len(rows)} users...", parse_mode="HTML")

    deleted, failed = 0, 0
    for user_id, msg_id in rows:
        try:
            await bot.delete_message(user_id, msg_id)
            deleted += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    # Remove from DB
    cur.execute("DELETE FROM broadcast_msgs WHERE broadcast_id=?", (broadcast_id,))
    cur.execute("DELETE FROM broadcast_log WHERE broadcast_id=?", (broadcast_id,))
    conn.commit()

    await status.edit_text(
        f"<b>Broadcast Delete Done!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"Broadcast ID: <code>{broadcast_id}</code>\n"
        f"Deleted: {deleted}\n"
        f"Failed: {failed}\n"
        f"Total: {deleted + failed}",
        parse_mode="HTML"
    )

@dp.message(Command("fbroadcast"))
async def cmd_fbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="forward")
        return await msg.reply("↩️ Jo message forward karna hai usse reply karke /fbroadcast karo.")
    status = await msg.reply("⏳ Forward broadcasting to users DM...")
    try:
        await _do_broadcast(msg.chat.id, msg.reply_to_message.message_id, "forward", status)
    except Exception as e:
        await status.edit_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

@dp.message(Command("pinbroadcast"))
async def cmd_pinbroadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    if not msg.reply_to_message:
        await state.set_state(BroadcastFlow.waiting)
        await state.update_data(bc_force_mode="pin")
        return await msg.reply("↩️ Jo message pin karke broadcast karna hai usse reply karke /pinbroadcast karo.")
    status = await msg.reply("⏳ Broadcasting to users DM...")
    try:
        await _do_broadcast(msg.chat.id, msg.reply_to_message.message_id, "pin", status)
    except Exception as e:
        await status.edit_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

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
            return await msg.reply("Blacklist empty hai.")
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
    log_ch = get_setting("log_group", "❌ Not set")
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
        f"➺ <b>Log Group:</b> <code>{log_ch}</code>\n"
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
        f"└ /setlog — Log group set karo\n"
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
            reply_markup=main_menu_keyboard(msg.from_user.id),
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
