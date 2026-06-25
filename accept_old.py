"""
accept_old.py — Standalone script
Satoru Gojo Bot ke DB se session lekar
saare channels/groups ke purane pending join requests accept karta hai.

Usage:
    python3 accept_old.py                    # DB ke sab chats
    python3 accept_old.py -100xxxxxxxxxx     # Sirf ek chat
    python3 accept_old.py @username          # Username se
"""

import asyncio
import sqlite3
import sys
import os

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import HideChatJoinRequestRequest

DB_PATH = os.environ.get("DB_PATH", "satoru.db")

def get_session():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("SELECT api_id, api_hash, phone, session FROM tg_sessions LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row

def get_all_chats():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("SELECT chat_id, title FROM chats WHERE accept=1")
    rows = cur.fetchall()
    conn.close()
    return rows

async def accept_for_chat(client: TelegramClient, chat_id_or_username: str | int, label: str):
    ok = fail = 0
    try:
        entity = await client.get_entity(chat_id_or_username)
        # Pending join requests iterate karo
        from telethon.tl.types import ChannelParticipantsKicked
        from telethon.tl.functions.channels import GetParticipantsRequest
        from telethon.tl.types import ChannelParticipantsBanned

        # Telethon mein join requests = iter_participants with filter
        async for user in client.iter_participants(entity, filter="requests"):
            try:
                await client(HideChatJoinRequestRequest(
                    peer=entity,
                    user_id=user,
                    approved=True
                ))
                ok += 1
                print(f"  ✅ Accepted: {getattr(user, 'first_name', '')} (ID: {user.id})")
                await asyncio.sleep(0.3)   # Flood wait se bachao
            except Exception as e:
                fail += 1
                print(f"  ❌ Failed: {user.id} — {e}")

    except Exception as e:
        print(f"  ❌ Chat fetch error ({label}): {e}")
        return 0, 1

    return ok, fail

async def main():
    row = get_session()
    if not row:
        print("❌ DB mein koi session nahi mila.")
        print("   Bot mein /login karo pehle, phir yahan chalao.")
        sys.exit(1)

    api_id, api_hash, phone, session_str = row
    print(f"✅ Session mila — Phone: {phone}")

    client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Session expired. Bot mein /logout karke /login karo.")
        await client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    print(f"👤 Logged in as: {me.first_name} (@{me.username})")
    print("━" * 50)

    # Target chats decide karo
    if len(sys.argv) > 1:
        raw = sys.argv[1].strip()
        # Single chat
        try:
            chat_id = int(raw)
            chats = [(chat_id, str(chat_id))]
        except ValueError:
            chats = [(raw, raw)]   # username
    else:
        chats = get_all_chats()
        if not chats:
            print("❌ DB mein koi chat nahi (accept=1 wala).")
            print("   Bot mein /addchannel ya /addgroup karo pehle.")
            await client.disconnect()
            sys.exit(1)

    print(f"📋 {len(chats)} chat(s) process honge...\n")

    total_ok = total_fail = 0

    for chat_id, title in chats:
        print(f"🔄 Processing: {title} ({chat_id})")
        ok, fail = await accept_for_chat(client, chat_id, str(title))
        total_ok   += ok
        total_fail += fail
        if ok == 0 and fail == 0:
            print(f"  ℹ️  Koi pending request nahi tha.")
        else:
            print(f"  📊 {ok} accepted, {fail} failed")
        print()

    print("━" * 50)
    print(f"✅ DONE!")
    print(f"   Total Accepted : {total_ok}")
    print(f"   Total Failed   : {total_fail}")
    print(f"   Chats Processed: {len(chats)}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
