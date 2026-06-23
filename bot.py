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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG — fill these before running
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
OWNER_ID  = 123456789   # your Telegram numeric ID

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
    chat_id INTEGER PRIMARY KEY,
    title   TEXT
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
#  WELCOME — rebuild keyboard from saved JSON
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def saved_keyboard() -> InlineKeyboardMarkup | None:
    raw = get_setting("welcome_buttons")
    if not raw:
        return None
    try:
        data = json.loads(raw)   # [[{"text":..,"url":..}, ...], ...]
        rows = []
        for row in data:
            rows.append([InlineKeyboardButton(**btn) for btn in row])
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    except Exception:
        return None

async def send_welcome(chat_id: int, mention: str = None):
    """Send saved welcome message to chat_id, optionally prepending mention."""
    msg_type = get_setting("welcome_type")          # video | photo | text
    file_id  = get_setting("welcome_file_id")
    caption  = get_setting("welcome_caption", "")
    keyboard = saved_keyboard()

    # Prepend mention if provided
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
class BroadcastFlow(StatesGroup):
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
    await send_welcome(msg.chat.id)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /save  — reply to any message to set as welcome
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("save"))
async def cmd_save(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")

    r = msg.reply_to_message
    if not r:
        return await msg.reply(
            "↩️ <b>Kisi bhi message ko reply karke /save likho</b>\n"
            "Video, photo, text — jo bhi ho wahi welcome message ban jaayega.",
            parse_mode="HTML"
        )

    # Detect type & extract data
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
        return await msg.reply("❌ Ye message type support nahi hota. Video/Photo/GIF/Text bhejo.")

    # Save inline buttons if any
    if r.reply_markup and hasattr(r.reply_markup, "inline_keyboard"):
        rows_data = []
        for row in r.reply_markup.inline_keyboard:
            row_data = []
            for btn in row:
                if btn.url:
                    row_data.append({"text": btn.text, "url": btn.url})
            if row_data:
                rows_data.append(row_data)
        set_setting("welcome_buttons", json.dumps(rows_data))
    else:
        set_setting("welcome_buttons", "")

    await msg.reply(
        f"✅ <b>Welcome message saved!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ Type: {media_type}\n"
        f"➺ Buttons: {'✅ Yes' if get_setting('welcome_buttons') else '❌ None'}\n\n"
        f"Ab /start karo ya koi group mein join kare to yahi message jayega.",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AUTO-ACCEPT JOIN REQUESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    # Register chat
    cur.execute("INSERT OR IGNORE INTO chats VALUES (?,?)",
                (req.chat.id, req.chat.title or ""))
    conn.commit()

    mode = get_setting("accept_mode", "auto")
    if mode != "auto":
        return

    try:
        await req.approve()
        mention = req.from_user.mention_html()
        # DM the user
        try:
            await send_welcome(req.from_user.id, mention)
        except Exception:
            pass
        # Welcome in group/channel
        await send_welcome(req.chat.id, mention)
    except Exception as e:
        log.warning(f"Join approve failed: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NEW MEMBER (direct join, no request)
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
#  /autoaccept on|off
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
    await msg.reply(
        f"{emoji} Auto-accept <b>{'ON' if new_mode=='auto' else 'OFF'}</b>.",
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
    await msg.reply(
        f"📢 <b>Broadcast done!</b>\n✅ Sent: {ok}\n❌ Failed: {fail}",
        parse_mode="HTML"
    )

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
    lines = [f"👑 Owner: <code>{OWNER_ID}</code>"]
    lines += [f"🛡 <code>{r[0]}</code>" for r in rows]
    await msg.reply("\n".join(lines), parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /ping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("ping"))
async def cmd_ping(msg: Message):
    t = time.time()
    m = await msg.reply("🏓 Pinging...")
    ms = round((time.time() - t) * 1000, 2)
    await m.edit_text(
        f"🏓 <b>PONG!</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>PING:</b> 🏓 {ms} ms\n"
        f"➺ <b>UPTIME:</b> ⏳ {uptime_str()}",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /stats
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.reply("⚠️ Only admins can use this.")
    cur.execute("SELECT COUNT(*) FROM chats")
    total_chats = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins")
    total_admins = cur.fetchone()[0]
    mode = get_setting("accept_mode", "auto")
    st = "🟢 ON" if mode == "auto" else "🔴 OFF"
    wtype = get_setting("welcome_type", "❌ Not set")
    await msg.reply(
        f"📊 <b>Bot Stats</b>\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        f"➺ <b>Chats:</b> {total_chats}\n"
        f"➺ <b>Admins:</b> {total_admins}\n"
        f"➺ <b>Auto-Accept:</b> {st}\n"
        f"➺ <b>Welcome Type:</b> {wtype}\n"
        f"➺ <b>Uptime:</b> ⏳ {uptime_str()}\n"
        f"━━━━━━━━━━▧▣▧━━━━━━━━━━",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /help
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.reply(
        "⚔️ <b>Bot Commands</b>\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "🌀 <b>General</b>\n"
        "/start — Welcome message dekho\n"
        "/ping — Ping + uptime check\n\n"
        "🛡 <b>Admin Only</b>\n"
        "/save — Kisi message ko reply karke welcome set karo\n"
        "/autoaccept on|off — Join request auto-accept toggle\n"
        "/broadcast — Sab chats mein message bhejo\n"
        "/stats — Bot stats\n"
        "/admins — Admin list\n\n"
        "👑 <b>Owner Only</b>\n"
        "/addadmin — Reply to user → admin banao\n"
        "/removeadmin — Reply to user → admin hatao\n"
        "━━━━━━━━━━▧▣▧━━━━━━━━━━\n"
        "✨ Bot ko group/channel mein <b>Admin</b> banao with <b>Add Members</b> permission.",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main():
    log.info("⚔️ Satoru Gojo Bot starting...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
          
