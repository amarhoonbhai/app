import asyncio
import random
import os
from datetime import datetime, timedelta
from loguru import logger
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, SlowModeWaitError, ChatWriteForbiddenError
from sqlalchemy.orm import Session
from backend.database.db import SessionLocal, Account, Group, Stats

class ForwardingEngine:
    def __init__(self, account_id: int):
        self.account_id = account_id
        self.client = None
        self.is_running = True
        self.loop_task = None
        self._logs = []

    def log_event(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self._logs.append(log_entry)
        if len(self._logs) > 10:
            self._logs.pop(0)
        
        if level == "info": logger.info(f"[Acc {self.account_id}] {message}")
        elif level == "warning": logger.warning(f"[Acc {self.account_id}] {message}")
        elif level == "error": logger.error(f"[Acc {self.account_id}] {message}")

    async def start(self):
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == self.account_id).first()
        if not acc:
            db.close()
            return

        session_path = f"sessions/{acc.phone}"
        self.client = TelegramClient(session_path, acc.api_id, acc.api_hash)
        
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.log_event("Unauthorized session", "error")
                return

            self.log_event(f"Started engine for {acc.name}")
            
            # Register commands
            @self.client.on(events.NewMessage(outgoing=True))
            async def handle_commands(event):
                if event.raw_text.startswith(".stats"):
                    await self.send_stats(event)
                elif event.raw_text.startswith(".help"):
                    await event.respond("🛠 **Elite V6 Commands**\n`.stats` - Real-time statistics\n`.help` - Show this menu")

            self.loop_task = asyncio.create_task(self.forward_loop())
            await self.client.run_until_disconnected()
        except Exception as e:
            self.log_event(f"Engine failure: {str(e)}", "error")
        finally:
            self.is_running = False
            db.close()

    async def send_stats(self, event):
        db = SessionLocal()
        stats = db.query(Stats).filter(Stats.account_id == self.account_id).first()
        acc = db.query(Account).filter(Account.id == self.account_id).first()
        
        if not stats or not acc:
            await event.respond("❌ Stats not available.")
            db.close()
            return

        next_time = stats.next_msg_at.strftime("%H:%M:%S") if stats.next_msg_at else "N/A"
        
        log_text = "\n".join(self._logs[-5:])
        
        reply = (
            f"📊 **System Statistics**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Account:** {acc.name}\n"
            f"📡 **Status:** `{stats.status}`\n"
            f"✅ **Total Success:** `{stats.success_total}`\n"
            f"❌ **Total Failed:** `{stats.fail_total}`\n"
            f"🔄 **Current Cycle:** `{stats.current_cycle_success}` Success / `{stats.current_cycle_fail}` Fail\n"
            f"⏳ **Next Message:** `{next_time}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 **Recent Logs:**\n"
            f"```{log_text}```"
        )
        await event.respond(reply)
        db.close()

    async def forward_loop(self):
        while self.is_running:
            db = SessionLocal()
            try:
                acc = db.query(Account).filter(Account.id == self.account_id).first()
                stats = db.query(Stats).filter(Stats.account_id == self.account_id).first()
                groups = [g.url for g in acc.groups]

                if not groups:
                    self.update_status(db, stats, "No groups")
                    await asyncio.sleep(60)
                    continue

                # Fetch latest message
                messages = await self.client.get_messages("me", limit=1)
                if not messages:
                    self.update_status(db, stats, "Waiting for message in Saved")
                    await asyncio.sleep(30)
                    continue

                msg = messages[0]
                stats.current_cycle_success = 0
                stats.current_cycle_fail = 0
                db.commit()

                for i, group in enumerate(groups):
                    self.update_status(db, stats, f"Forwarding ({i+1}/{len(groups)})")
                    try:
                        if acc.use_copy:
                            if msg.media:
                                await self.client.send_file(group, msg.media, caption=msg.text)
                            else:
                                await self.client.send_message(group, msg.text)
                        else:
                            await self.client.forward_messages(group, msg)
                        
                        stats.success_total += 1
                        stats.current_cycle_success += 1
                        self.log_event(f"Delivered to {group}")
                    except Exception as e:
                        stats.fail_total += 1
                        stats.current_cycle_fail += 1
                        self.log_event(f"Failed {group}: {type(e).__name__}", "warning")

                    # Delay between groups
                    delay = acc.msg_delay_sec * random.uniform(0.9, 1.1)
                    stats.next_msg_at = datetime.now() + timedelta(seconds=delay)
                    db.commit()
                    await asyncio.sleep(delay)

                # Cycle complete
                stats.last_cycle_at = datetime.now()
                self.update_status(db, stats, "Cycle Waiting")
                cycle_delay = acc.cycle_delay_min * 60
                stats.next_msg_at = datetime.now() + timedelta(seconds=cycle_delay)
                db.commit()
                await asyncio.sleep(cycle_delay)

            except Exception as e:
                self.log_event(f"Loop error: {str(e)}", "error")
                await asyncio.sleep(60)
            finally:
                db.close()

    def update_status(self, db, stats, status_msg):
        stats.status = status_msg
        db.commit()
