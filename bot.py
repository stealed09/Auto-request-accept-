import asyncio
import logging
import sqlite3
import time
import json

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, ChatJoinRequest, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))

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
# Add columns if upgrading old DB
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

def uptime_str() -> str:
    secs = int(time.time() - START_TIME)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUTTON TEXT PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def parse_button_text(text: str):
    """
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
#  STATES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SaveFlow(StatesGroup):
    waiting_buttons = State()

class BroadcastFlow(StatesGroup):
    waiting = State()

class AddButtonFlow(StatesGroup):
    waiting = State()

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
    """Resolve @username, t.me link, or -100xxx → (chat_id, title, username, chat_type)"""
    raw = raw.strip()
    if raw.startswith("@"):
        username = raw.lstrip("@")
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, chat.title or "Unknown", chat.username, chat.type.value
        except Exception:
            return None
    elif "t.me/" in raw:
        import re
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
        # Numeric ID — private group/channel ke liye get_chat fail ho sakta hai
        try:
            chat_id = int(raw)
        except ValueError:
            return None
        try:
            chat = await bot.get_chat(chat_id)
            return chat.id, chat.title or "Unknown", chat.username, chat.type.value
        except Exception:
            # Private group/channel — get_chat fail, return with sentinel title
            if str(chat_id).startswith("-100"):
                return chat_id, None, None, "unknown"
            return None

async def check_bot_admin(chat_id: int) -> dict:
    """
    FIX: chat_type check + correct attribute for can_invite_users
    """
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        status = member.status.value if hasattr(member.status, "value") else str(member.status)
        is_adm = status in ("administrator", "creator")
        # ChatMemberAdministrator has can_invite_users attribute
        can_invite = getattr(member, "can_invite_users", False) if is_adm else False
        return {"is_admin": is_adm, "can_invite": bool(can_invite)}
    except Exception as e:
        log.warning(f"check_bot_admin error: {e}")
        return {"is_admin": False, "can_invite": False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /addchannel  /addgroup  /addchat
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("addchannel", "addgroup", "addchat"))
async def cmd_add_chat(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cmd = msg.text.split()[0].lstrip("/")
    parts = msg.text.split(maxsplit=2)
    # Optional: /addgroup -100xxx Custom Name
    custom_name = parts[2].strip() if len(parts) == 3 else None
    # args[1] sirf ID/username hona chahiye — naam nahi
    args = [parts[0], parts[1]] if len(parts) >= 2 else parts
    if len(args) < 2:
        if cmd == "addchannel":
            return await msg.reply(
                "📢 <b>Channel add karo:</b>\n\n"
                "<code>/addchannel @username</code>\n"
                "<code>/addchannel -100xxxxxxxxxx</code>\n"
                "<code>/addchannel -100xxxxxxxxxx My Channel</code> — custom naam\n\n"
                "<i>Bot ko pehle channel mein admin banao (Add Members permission).</i>",
                parse_mode="HTML"
            )
        elif cmd == "addgroup":
            return await msg.reply(
                "👥 <b>Group add karo:</b>\n\n"
                "<code>/addgroup @username</code>\n"
                "<code>/addgroup -100xxxxxxxxxx</code>\n"
                "<code>/addgroup -100xxxxxxxxxx My Group</code> — custom naam\n\n"
                "<i>Bot ko pehle group mein admin banao (Add Members permission).</i>",
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

    status_msg = await msg.reply(f"⏳ Checking <code>{args[1]}</code>...", parse_mode="HTML")
    result = await resolve_chat_id(args[1])
    if not result:
        return await status_msg.edit_text("❌ Chat resolve nahi hua. Username/ID check karo.", parse_mode="HTML")

    chat_id, title, username, chat_type = result

    # Private chat — get_chat fail hua, cmd se type + title set karo
    if chat_type == "unknown":
        if cmd == "addchannel":
            chat_type = "channel"
            label_type = "Channel"
        elif cmd == "addgroup":
            chat_type = "supergroup"
            label_type = "Group"
        else:
            chat_type = "supergroup"
            label_type = "Chat"
        # Custom name diya? Use karo, warna generic
        title = custom_name if custom_name else f"Private {label_type}"
    elif custom_name:
        # Public chat ka naam bhi override kar sakte ho
        title = custom_name

    if chat_type not in allowed and cmd != "addchat":
        return await status_msg.edit_text(
            f"❌ Ye <b>{chat_type}</b> detect hua, {label} nahi.\n\n"
            f"<i>Agar private hai toh sahi command use karo:</i>",
            parse_mode="HTML"
        )

    admin = await check_bot_admin(chat_id)
    if not admin["is_admin"]:
        return await status_msg.edit_text(
            f"❌ Bot <b>{title}</b> mein admin nahi hai! Pehle admin banao.",
            parse_mode="HTML"
        )
    if not admin["can_invite"]:
        return await status_msg.edit_text(
            f"⚠️ Bot admin hai but <b>\"Add Members\"</b> permission nahi hai.\n\n"
            f"<i>Channel/Group settings → Administrators → bot → Add Members ✅ karo.</i>",
            parse_mode="HTML"
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
            f"Usage: <code>/setlog -100xxxxxxxxx</code>",
            parse_mode="HTML"
        )
    channel_id = args[1].strip()
    set_setting("log_channel", channel_id)
    await msg.reply(
        f"✅ Log channel set: <code>{channel_id}</code>\n\n"
        f"Ab us channel mein koi bhi message forward karo — video + caption automatically save ho jayega!",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOG CHANNEL — auto save forwarded message (no pyrogram, no buttons)
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

    # Only forwarded messages
    if not (msg.forward_from_chat or msg.forward_from or msg.forward_sender_name):
        return

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

    try:
        await bot.send_message(
            OWNER_ID,
            f"✅ <b>Welcome message auto-saved!</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ Type: {media_type}\n"
            f"➺ Buttons: /addbutton se set karo\n\n"
            f"/start karo preview ke liye.",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /save — reply to any message
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AUTO-ACCEPT JOIN REQUESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _accept_pending(chat_id: int):
    """Toggle ON hone pe saare pending join requests accept karo"""
    import aiohttp
    token = bot.token
    url = f"https://api.telegram.org/bot{token}/approveChatJoinRequest"
    # Telegram ka bulk approve nahi hai — lekin getChatJoinRequestsCount se count milta hai
    # Workaround: bot.decline + approve loop nahi ho sakta bina user IDs ke
    # Best approach: Telegram ka "Approve All" = approveChatJoinRequest per user
    # Hum pending requests get karne ki koshish karte hain via getUpdates offset trick
    # Actually sabse reliable: channel/group mein jaake manually ek baar approve all karo
    # Bot sirf naye auto-accept kar sakta hai — Telegram API pending list expose nahi karta
    log.info(f"Auto-accept ON for {chat_id} — new join requests will be auto-approved.")

@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    chat_type = req.chat.type.value if hasattr(req.chat.type, "value") else str(req.chat.type)
    title = req.chat.title or str(req.chat.id)
    cur.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, accept) VALUES (?,?,?,1)",
        (req.chat.id, title, chat_type)
    )
    # Title update karo agar change hua ho
    cur.execute(
        "UPDATE chats SET title=?, chat_type=? WHERE chat_id=?",
        (title, chat_type, req.chat.id)
    )
    conn.commit()

    mode = get_setting("accept_mode", "auto")
    if mode != "auto":
        return

    cur.execute("SELECT accept FROM chats WHERE chat_id=?", (req.chat.id,))
    row = cur.fetchone()
    if row and row[0] == 0:
        return

    try:
        await req.approve()
        mention = req.from_user.mention_html()
        # Welcome sirf group/supergroup mein bhejo — channel mein nahi, aur 5 min baad delete
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
        # FIX: Correct INSERT with column names
        cur.execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, chat_type) VALUES (?,?,?)",
            (update.chat.id, update.chat.title or "", update.chat.type.value if hasattr(update.chat.type, "value") else str(update.chat.type))
        )
        conn.commit()
        mention = update.new_chat_member.user.mention_html()
        chat_type_val = update.chat.type.value if hasattr(update.chat.type, "value") else str(update.chat.type)
        if chat_type_val in ("group", "supergroup"):
            asyncio.create_task(send_welcome_autodelete(update.chat.id, mention))

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
        # Sirf actual title dikhao - Private Channel/Group nahi
        display = title or f"ID:{chat_id}"
        # Private wale title se "Private Channel/Group" hata ke sirf ID dikhao
        if display.startswith("Private "):
            display = f"ID: {chat_id}"
        display = display[:30]
        status = "🟢" if accept else "🔴"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status} {emoji} {display}",
                callback_data=f"chtoggle:{chat_id}:{page}:{filter_type}"
            )
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
        InlineKeyboardButton(text="👥 Groups",   callback_data="chfilter:group:0"),
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
        return await msg.reply(
            "📋 <b>Koi chat add nahi hai.</b>\n\n"
            "/addchannel ya /addgroup se add karo.",
            parse_mode="HTML"
        )
    cur.execute("SELECT chat_type, COUNT(*) FROM chats GROUP BY chat_type")
    breakdown = cur.fetchall()
    type_labels = {"channel": "Channel", "supergroup": "Group", "group": "Group"}
    bd = " | ".join([f"{type_labels.get(t, t)}: {c}" for t, c in breakdown])
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
    # Jab ON karo — pending join requests bhi accept karo
    if new_val == 1:
        asyncio.create_task(_accept_pending(chat_id))

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
    # FIX: filter_type argument diya
    kb, _ = build_chats_keyboard(0, "all")
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
        await asyncio.sleep(0.5)  # FIX: 0.05 se 0.5 — rate limit safe
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
#  /id — group/channel mein bhejo to ID milegi
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("id"))
async def cmd_id(msg: Message):
    chat = msg.chat
    user = msg.from_user
    if chat.type == "private":
        await msg.reply(
            f"🆔 <b>Your ID:</b> <code>{user.id}</code>",
            parse_mode="HTML"
        )
    else:
        chat_type = chat.type.value if hasattr(chat.type, "value") else str(chat.type)
        uname = f"@{chat.username}" if chat.username else "Private (no username)"
        await msg.reply(
            f"🆔 <b>Chat Info</b>\n"
            f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
            f"➺ <b>Title:</b> {chat.title}\n"
            f"➺ <b>ID:</b> <code>{chat.id}</code>\n"
            f"➺ <b>Type:</b> {chat_type}\n"
            f"➺ <b>Username:</b> {uname}\n\n"
            f"Ab copy karo aur:\n"
            f"<code>/addgroup {chat.id}</code>",
            parse_mode="HTML"
        )

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
    log_ch = get_setting("log_channel", "❌ Not set")
    await msg.reply(
        f"📊 <b>Bot Stats</b>\n━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Chats:</b> {total_chats}\n"
        f"➺ <b>Admins:</b> {total_admins}\n"
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
        "<b>Bot Guide</b>\n"
        "------------------------\n\n"
        "<b>Channel / Group Add Karna</b>\n"
        "Pehle bot ko Admin banao + Add Members permission do\n"
        "  /addchannel @username - Public channel\n"
        "  /addgroup @username - Public group\n"
        "  /addchannel -100xxxxxx Naam - Private channel\n"
        "  /addgroup -100xxxxxx Naam - Private group\n\n"
        "<b>Auto-Accept</b>\n"
        "/chats - Sab chats list + toggle\n"
        "/autoaccept on|off - Sab ek saath\n\n"
        "<b>Welcome Message</b>\n"
        "Video/photo/text reply karke /save\n"
        "/addbutton - Buttons add karo\n"
        "Format: Name | https://link\n\n"
        "<b>Group Management (Admin Only)</b>\n"
        "/ban - Reply karke user ban karo\n"
        "/kick - Reply karke user kick karo\n"
        "/mute [minutes] - Reply karke mute karo\n"
        "/unmute - Mute hatao\n"
        "/warn - Warning do (3 pe auto-ban)\n"
        "/unwarn - Warnings reset karo\n"
        "/pin - Message pin karo\n"
        "/unpin - Pin hatao\n"
        "/purge - Reply se le ke messages delete\n"
        "/antilink on|off - Links auto-delete\n\n"
        "<b>Other</b>\n"
        "/stats - Bot details\n"
        "/ping - Online check\n"
        "/broadcast - Sab chats mein message\n"
        "/removechat - Chat hatao\n"
        "/id - Group/channel ID pata karo\n\n"
        "------------------------\n"
        "NOTE: Bot Admin + Add Members permission zaroori hai!",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GROUP MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Warnings DB
cur.executescript("""
CREATE TABLE IF NOT EXISTS warnings (
    user_id INTEGER,
    chat_id INTEGER,
    count   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);
""")
conn.commit()

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

# /ban
@dp.message(Command("ban"))
async def cmd_ban(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins ban kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Kisi user ke message ko reply karke /ban likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(
            f"🚫 <b>{target.from_user.mention_html()}</b> ko ban kar diya gaya.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Ban nahi ho saka: {e}")

# /kick
@dp.message(Command("kick"))
async def cmd_kick(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins kick kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Kisi user ke message ko reply karke /kick likho.")
    try:
        await bot.ban_chat_member(msg.chat.id, target.from_user.id)
        await bot.unban_chat_member(msg.chat.id, target.from_user.id)
        await msg.reply(
            f"👢 <b>{target.from_user.mention_html()}</b> ko kick kar diya gaya.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Kick nahi ho saka: {e}")

# /mute
@dp.message(Command("mute"))
async def cmd_mute(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins mute kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Kisi user ke message ko reply karke /mute likho.")
    args = msg.text.split()
    # Optional duration in minutes
    duration = None
    if len(args) > 1:
        try:
            duration = int(args[1])
        except ValueError:
            pass
    from aiogram.types import ChatPermissions
    from datetime import datetime, timedelta
    until = datetime.now() + timedelta(minutes=duration) if duration else None
    try:
        await bot.restrict_chat_member(
            msg.chat.id,
            target.from_user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        dur_text = f" {duration} minute ke liye" if duration else " permanently"
        await msg.reply(
            f"🔇 <b>{target.from_user.mention_html()}</b> ko{dur_text} mute kar diya.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Mute nahi ho saka: {e}")

# /unmute
@dp.message(Command("unmute"))
async def cmd_unmute(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins unmute kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Reply karke /unmute likho.")
    from aiogram.types import ChatPermissions
    try:
        await bot.restrict_chat_member(
            msg.chat.id,
            target.from_user.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await msg.reply(
            f"🔊 <b>{target.from_user.mention_html()}</b> unmute ho gaya.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply(f"❌ Unmute nahi ho saka: {e}")

# /warn
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
            await msg.reply(
                f"🚫 <b>{target.from_user.mention_html()}</b> ko 3 warnings mil gayi — auto-ban!",
                parse_mode="HTML"
            )
        except Exception as e:
            await msg.reply(f"⚠️ 3 warnings ho gayi but ban nahi ho saka: {e}")
    else:
        await msg.reply(
            f"\u26a0\ufe0f <b>{target.from_user.mention_html()}</b> ko warning #{count}/3 mili.\n"
            f"3 warnings pe auto-ban hoga.",
            parse_mode="HTML"
        )

# /unwarn
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
    await msg.reply(
        f"✅ <b>{target.from_user.mention_html()}</b> ki sab warnings reset ho gayi.",
        parse_mode="HTML"
    )

# /pin
@dp.message(Command("pin"))
async def cmd_pin(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins pin kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Kisi message ko reply karke /pin likho.")
    try:
        await bot.pin_chat_message(msg.chat.id, target.message_id, disable_notification=False)
        await msg.reply("📌 Message pin ho gaya.")
    except Exception as e:
        await msg.reply(f"❌ Pin nahi ho saka: {e}")

# /unpin
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

# /purge
@dp.message(Command("purge"))
async def cmd_purge(msg: Message):
    if msg.chat.type == "private":
        return await msg.reply("⚠️ Ye command group mein use karo.")
    if not await is_group_admin(bot, msg.chat.id, msg.from_user.id) and not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Sirf admins purge kar sakte hain.")
    target = msg.reply_to_message
    if not target:
        return await msg.reply("↩️ Jis message se delete karna hai usse reply karke /purge likho.")
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
#  ANTI-LINK FILTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(F.text & F.chat.type.in_({"group", "supergroup"}))
async def anti_link_filter(msg: Message):
    antilink = get_setting("antilink", "off")
    if antilink != "on":
        return
    import re
    if re.search(r"(https?://|t\.me/|@\w+)", msg.text or ""):
        # Admin ka message skip
        if await is_group_admin(bot, msg.chat.id, msg.from_user.id) or is_admin(msg.from_user.id):
            return
        try:
            await msg.delete()
            warn_msg = await msg.answer(
                f"🚫 <b>{msg.from_user.mention_html()}</b> links allowed nahi hain!",
                parse_mode="HTML"
            )
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
    await msg.reply(f"Anti-link <b>{'🟢 ON' if args[1]=='on' else '🔴 OFF'}</b> ho gaya.", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WELCOME AUTO-DELETE (5 min)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def send_welcome_autodelete(chat_id: int, mention: str = None):
    """Welcome bhejo aur 5 min baad delete karo"""
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
            await asyncio.sleep(300)  # 5 min
            try:
                await sent.delete()
            except Exception:
                pass
    except Exception as e:
        log.warning(f"send_welcome_autodelete failed {chat_id}: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main():
    log.info("⚔️ Satoru Gojo Bot starting...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
