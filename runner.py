
import os
import json
import asyncio
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from datetime import datetime

USERS_DIR = "users"
SESSIONS_DIR = "sessions"
clients = {}
started_phones = set()

async def run_user_bot(config):
    phone = config["phone"]
    if phone in started_phones:
        return

    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    api_id = int(config["api_id"])
    api_hash = config["api_hash"]
    groups = config.get("groups", [])
    delay = config.get("msg_delay_sec", 5)
    cycle = config.get("cycle_delay_min", 15)

    user_state = {
        "delay": delay,
        "cycle": cycle,
    }

    client = TelegramClient(session_path, api_id, api_hash)
    await client.start()

    started_phones.add(phone)
    print(f"[✔] Started bot for {config['name']} ({phone})")

    @client.on(events.NewMessage)
    async def command_handler(event):
        me = await client.get_me()
        if event.sender_id != me.id:
            return

        text = event.raw_text.strip()

        if text.startswith(".time"):
            value = int(''.join(filter(str.isdigit, text)))
            if 'h' in text:
                user_state["cycle"] = value * 60
            else:
                user_state["cycle"] = value
            await event.respond(f"✅ Cycle delay set to {user_state['cycle']} minutes")

        elif text.startswith(".delay"):
            value = int(''.join(filter(str.isdigit, text)))
            user_state["delay"] = value
            await event.respond(f"✅ Message delay set to {value} seconds")

        elif text.startswith(".status"):
            await event.respond(
                f"📊 Status:\nCycle Delay: {user_state['cycle']} minutes\n"
                f"Message Delay: {user_state['delay']} seconds"
            )

        elif text.startswith(".info"):
            reply = (
                f"📄 User Info:\n"
                f"Name: {config.get('name')}\n"
                f"Phone: {config.get('phone')}\n"
                f"Cycle Delay: {user_state['cycle']} min\n"
                f"Message Delay: {user_state['delay']} sec\n"
                f"Groups: {len(groups)}\n"
                f"Plan Expiry: {config.get('plan_expiry', 'N/A')}"
            )
            await event.respond(reply)

        elif text.startswith(".addgroup"):
            parts = text.split()
            if len(parts) == 2:
                new_group = parts[1]
                if new_group not in groups:
                    groups.append(new_group)
                    config["groups"] = groups
                    with open(os.path.join(USERS_DIR, f"{phone}.json"), "w") as f:
                        json.dump(config, f, indent=2)
                    await event.respond("✅ Group added.")
                else:
                    await event.respond("⚠️ Group already in list.")

        elif text.startswith(".delgroup"):
            parts = text.split()
            if len(parts) == 2 and parts[1] in groups:
                groups.remove(parts[1])
                config["groups"] = groups
                with open(os.path.join(USERS_DIR, f"{phone}.json"), "w") as f:
                    json.dump(config, f, indent=2)
                await event.respond("✅ Group removed.")

        elif text.startswith(".groups"):
            if groups:
                await event.respond("📋 Groups:\n" + "\n".join([g for g in groups if "t.me" in g]))
            else:
                await event.respond("📋 No groups configured.")

        elif text.startswith(".help"):
            await event.respond(
                "🛠 Available Commands:\n"
                ".time <10m|1h> — Set cycle delay\n"
                ".delay <sec> — Set delay between messages\n"
                ".status — Show timing settings\n"
                ".info — Show full user info\n"
                ".addgroup <url> — Add group\n"
                ".delgroup <url> — Remove group\n"
                ".groups — List groups\n"
                ".help — Show this message"
            )

    async def forward_loop():
        while True:
            messages = await client.get_messages("me", limit=100)
            messages = list(reversed(messages))

            for msg in messages:
                if msg.message is None and not msg.media:
                    continue

                for group in groups:
                    try:
                        await client.forward_messages(group, msg)
                        print(f"[{phone}] Forwarded to {group}")
                    except Exception as e:
                        print(f"[{phone}] Error: {e}")

                await asyncio.sleep(user_state["delay"])

            print(f"[{phone}] Cycle complete. Sleeping for {user_state['cycle']} minutes...")
            await asyncio.sleep(user_state["cycle"] * 60)

    asyncio.create_task(forward_loop())
    await client.run_until_disconnected()

async def user_loader():
    while True:
        for file in os.listdir(USERS_DIR):
            if file.endswith(".json"):
                path = os.path.join(USERS_DIR, file)
                with open(path, 'r') as f:
                    config = json.load(f)
                    expiry = config.get("plan_expiry")
                    if expiry and datetime.now() > datetime.fromisoformat(expiry):
                        print(f"[⏳] Plan expired for {config['phone']}. Skipping.")
                        continue
                    asyncio.create_task(run_user_bot(config))
        await asyncio.sleep(60)

async def main():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)
    await user_loader()

if __name__ == "__main__":
    asyncio.run(main())
