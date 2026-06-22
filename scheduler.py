# Verified Python 3.11 compatible
import os
import sys
import json
import asyncio
import re
import random
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, SlowModeWaitError, ChatWriteForbiddenError
from colorama import Fore, Style, init

init(autoreset=True)

SESSIONS_DIR = "sessions"
USERS_DIR    = "users"
USERS_FILE   = "users.json"
AUTONIGHT_FILE = "autonight.json"

# ---------- Local Time Helpers ----------
def _get_now_tz(tz_name: str) -> datetime:
    if not tz_name:
        tz_name = "Asia/Kolkata"
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    try:
        from datetime import timezone, timedelta
        if tz_name == "Asia/Kolkata":
            return datetime.now(timezone(timedelta(hours=5, minutes=30)))
    except Exception:
        pass
    return datetime.now()

def _parse_hhmm(s: str) -> time:
    s = s.strip()
    if re.fullmatch(r"\d{1,2}", s):
        h = int(s)
        if not (0 <= h <= 23):
            raise ValueError("Hour must be 0..23")
        return time(h, 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError("Time must be HH or HH:MM (24h)")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError("Invalid time")
    return time(h, mm)

def _in_window(now_t: time, start_t: time, end_t: time) -> bool:
    if start_t <= end_t:
        return start_t <= now_t < end_t
    return (now_t >= start_t) or (now_t < end_t)

def autonight_is_quiet_at(check_dt: datetime, cfg: dict) -> bool:
    if not cfg.get("enabled", True):
        return False
    try:
        start_t = _parse_hhmm(cfg.get("start", "00:00"))
        end_t   = _parse_hhmm(cfg.get("end", "06:00"))
        return _in_window(check_dt.time(), start_t, end_t)
    except Exception:
        return False

def get_seconds_until_quiet_end_at(check_dt: datetime, cfg: dict) -> int:
    try:
        start_t = _parse_hhmm(cfg.get("start", "00:00"))
        end_t   = _parse_hhmm(cfg.get("end", "06:00"))
        today = check_dt.date()
        
        if start_t <= end_t:
            end_dt = datetime.combine(today, end_t, tzinfo=check_dt.tzinfo)
            if check_dt.time() >= end_t:
                end_dt = end_dt + timedelta(days=1)
        else:
            if check_dt.time() < end_t:
                end_dt = datetime.combine(today, end_t, tzinfo=check_dt.tzinfo)
            else:
                end_dt = datetime.combine(today + timedelta(days=1), end_t, tzinfo=check_dt.tzinfo)
        
        seconds = int((end_dt - check_dt).total_seconds())
        return max(1, seconds)
    except Exception:
        return 0

# ---------- Group resolving & health ----------
async def resolve_group_entity(client: TelegramClient, group_url: str):
    clean_link = group_url.strip().rstrip('/')
    if "t.me/+" in clean_link or "t.me/joinchat/" in clean_link:
        if "t.me/+" in clean_link:
            hash_val = clean_link.split('+')[-1]
        else:
            hash_val = clean_link.split('joinchat/')[-1]
        from telethon.tl.functions.messages import CheckChatInviteRequest
        from telethon.tl.types import ChatInviteAlready
        try:
            res = await client(CheckChatInviteRequest(hash_val))
            if isinstance(res, ChatInviteAlready) and res.chat:
                return res.chat
        except Exception:
            pass
    try:
        return await client.get_entity(clean_link)
    except Exception:
        return group_url

async def check_write_permission(client: TelegramClient, entity) -> str:
    try:
        from telethon.tl.types import Channel, Chat
        if isinstance(entity, Channel):
            if entity.broadcast and not entity.admin_rights:
                return "Read-Only Channel"
            if entity.banned_rights and entity.banned_rights.send_messages:
                return "Muted (Banned)"
        elif isinstance(entity, Chat):
            if entity.default_banned_rights and entity.default_banned_rights.send_messages:
                return "Muted (Default)"
        
        try:
            permissions = await client.get_permissions(entity)
            if permissions.is_banned:
                return "Banned"
            if hasattr(permissions, 'send_messages') and not permissions.send_messages:
                return "Muted"
        except Exception:
            pass
        return "Healthy"
    except Exception as e:
        return f"Access Denied: {type(e).__name__}"

# ---------- Clean-Up Scheduled Messages ----------
async def clear_scheduled_messages(client: TelegramClient, entity, group_name: str):
    try:
        res = await client(functions.messages.GetScheduledHistoryRequest(
            peer=entity,
            hash=0
        ))
        if res.messages:
            msg_ids = [m.id for m in res.messages]
            await client(functions.messages.DeleteScheduledMessagesRequest(
                peer=entity,
                id=msg_ids
            ))
            print(Fore.RED + f"  [✖] Cleared {len(msg_ids)} existing scheduled messages in {group_name}.")
    except Exception as e:
        print(Fore.YELLOW + f"  [!] Failed to clear scheduled messages for {group_name}: {e}")

# ---------- Core scheduling menu & executor ----------
async def schedule_menu():
    print(Fore.MAGENTA + Style.BRIGHT + "\n  ⚡ ONE-TIME TELEGRAM SCHEDULER")
    print(Fore.MAGENTA + "  " + "─" * 40)
    
    if not os.path.exists(USERS_FILE):
        print(Fore.RED + "  [!] No users list file found.")
        input("  Press Enter to return...")
        return
        
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
        
    if not users:
        print(Fore.YELLOW + "  [!] No registered user sessions found.")
        input("  Press Enter to return...")
        return
        
    print(Fore.CYAN + "  Select an account to schedule messages for:")
    user_list = list(users.items())
    for idx, (phone, data) in enumerate(user_list, 1):
        print(f"  {Fore.YELLOW}{idx}. {Fore.WHITE}{data.get('name', 'Unknown')} ({phone})")
        
    choice = input(Fore.YELLOW + "\n  ❯ Enter choice [1-{}]: ".format(len(user_list))).strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(user_list)):
        print(Fore.RED + "  [!] Invalid choice.")
        input("  Press Enter to return...")
        return
        
    phone, user_data = user_list[int(choice) - 1]
    
    # Load user config
    config_path = os.path.join(USERS_DIR, f"{phone}.json")
    if not os.path.exists(config_path):
        print(Fore.RED + f"  [!] Configuration file for {phone} is missing.")
        input("  Press Enter to return...")
        return
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    groups = config.get("groups", [])
    if not groups:
        print(Fore.RED + "  [!] No target groups configured for this user. Please add groups first.")
        input("  Press Enter to return...")
        return
        

    
    # Load Auto-Night Config
    autonight_cfg = {
        "enabled": True,
        "start": "00:00",
        "end": "06:00",
        "tz": "Asia/Kolkata"
    }
    if os.path.exists(AUTONIGHT_FILE):
        try:
            with open(AUTONIGHT_FILE, "r", encoding="utf-8") as f:
                autonight_cfg.update(json.load(f))
        except Exception:
            pass
            
    tz_name = autonight_cfg.get("tz", "Asia/Kolkata")
    
    # Connecting to Telegram client
    session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
    api_id = int(config["api_id"])
    api_hash = config["api_hash"]
    
    print(Fore.GREEN + f"\n  [🔁] Connecting to session for {phone}...")
    client = TelegramClient(session_path, api_id, api_hash)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(Fore.RED + "  [!] Session is revoked or unauthorized. Please log in again.")
            return
            
        me = await client.get_me()
        print(Fore.GREEN + f"  [✔] Connected as: {me.first_name or me.username}")
        
        # Fetch Saved Messages ("me")
        print(Fore.GREEN + "  [📡] Fetching messages from Saved Messages...")
        messages = await client.get_messages("me", limit=100)
        valid_messages = [m for m in messages if m.text or m.media]
        
        if not valid_messages:
            print(Fore.RED + "  [!] No valid messages found in Saved Messages. Please add messages there.")
            return
            
        print(Fore.GREEN + f"  [✔] Found {len(valid_messages)} valid messages.")
        
        delay = config.get("msg_delay_sec", 30)
        cycle_min = config.get("cycle_delay_min", 7)
        use_copy = config.get("use_copy", True) # Default to Copy mode
        
        # Prompts for scheduling mode:
        print(Fore.CYAN + "\n  Select scheduling mode:")
        print("  1. Specify number of Cycles")
        print("  2. Schedule for a specific Duration (e.g. 1 Week / 7 Days)")
        mode = input(Fore.YELLOW + "  ❯ Choose option [1 or 2, default: 1]: ").strip()
        
        total_cycles = 5
        if mode == '2':
            days_in = input(Fore.YELLOW + "  ❯ How many days to schedule for? [Default: 7 (1 week)]: ").strip()
            days = float(days_in) if days_in.replace('.', '', 1).isdigit() else 7.0
            if days <= 0:
                days = 7.0
            
            # Calculate cycles to cover days
            total_minutes = days * 24 * 60
            one_cycle_minutes = len(valid_messages) * cycle_min
            import math
            total_cycles = math.ceil(total_minutes / one_cycle_minutes)
            print(Fore.GREEN + f"  [✔] Calculated {total_cycles} cycles to cover {days} days.")
        else:
            cycles_in = input(Fore.YELLOW + "  ❯ How many cycles to schedule? [Default: 5]: ").strip()
            total_cycles = int(cycles_in) if cycles_in.isdigit() else 5
            if total_cycles <= 0:
                total_cycles = 5

        # Check and handle Telegram limits (max 100 scheduled messages per chat)
        messages_per_group = len(valid_messages) * total_cycles
        if messages_per_group > 100:
            print(Fore.RED + f"\n  [⚠️] WARNING: Your settings request {messages_per_group} scheduled messages per group.")
            print(Fore.RED + "      Telegram has a hard limit of 100 scheduled messages per group.")
            
            # Offer to auto-cap
            cap_cycles = 100 // len(valid_messages)
            if cap_cycles <= 0:
                print(Fore.RED + f"  [!] ERROR: You have {len(valid_messages)} messages in Saved Messages, which is more than the Telegram limit of 100.")
                print(Fore.RED + "      Please reduce your Saved Messages count below 100.")
                return
            
            covered_days = (cap_cycles * len(valid_messages) * cycle_min) / (24 * 60)
            
            print(Fore.YELLOW + f"      To fit the limit, we can cap the campaign at {cap_cycles} cycles.")
            print(Fore.YELLOW + f"      This will schedule 100 messages per group and cover {covered_days:.1f} days.")
            
            confirm_cap = input(Fore.YELLOW + f"  ❯ Apply this cap and proceed? [Y/n]: ").strip().lower()
            if confirm_cap in {"", "y", "yes"}:
                total_cycles = cap_cycles
                messages_per_group = len(valid_messages) * total_cycles
                print(Fore.GREEN + f"  [✔] Capped at {total_cycles} cycles.")
            else:
                print(Fore.RED + "  [✖] Scheduling cancelled.")
                return

        # Clear previous queue?
        clear_in = input(Fore.YELLOW + "  ❯ Clear existing scheduled messages in groups first? [y/N]: ").strip().lower()
        clear_existing = clear_in in {"y", "yes"}

        # Calculate limits
        total_group_sends = len(valid_messages) * len(groups) * total_cycles
        print(Fore.CYAN + f"\n  📊 Target Analysis:")
        print(f"  • Target Groups  : {len(groups)}")
        print(f"  • Source Messages: {len(valid_messages)}")
        print(f"  • Total Cycles   : {total_cycles}")
        print(f"  • Messages/Group : {messages_per_group} (Telegram Limit: 100)")
        print(f"  • Total Sends    : {total_group_sends}")
                
        # Resolve all group entities & check permissions beforehand
        print(Fore.CYAN + f"\n  [🔍] Auditing write permissions on {len(groups)} target groups...")
        resolved_groups = []
        for idx, group in enumerate(groups, 1):
            try:
                entity = await resolve_group_entity(client, group)
                if isinstance(entity, str):
                    print(Fore.RED + f"    ✖ {idx}. {group} | Unresolved Entity")
                    continue
                permission_status = await check_write_permission(client, entity)
                if permission_status == "Healthy":
                    resolved_groups.append((group, entity))
                    print(Fore.GREEN + f"    ✔ {idx}. {getattr(entity, 'title', group)} | Healthy")
                else:
                    print(Fore.RED + f"    ✖ {idx}. {getattr(entity, 'title', group)} | {permission_status}")
            except Exception as e:
                print(Fore.RED + f"    ✖ {idx}. {group} | Error: {type(e).__name__}")
                
        if not resolved_groups:
            print(Fore.RED + "\n  [!] No healthy target groups available to schedule.")
            return
            
        # Clear existing scheduled messages if requested
        if clear_existing:
            print(Fore.CYAN + f"\n  [🧹] Clearing previous scheduled queues...")
            for idx, (group_url, entity) in enumerate(resolved_groups, 1):
                group_name = getattr(entity, 'title', group_url)
                print(f"    • Clearing {group_name}...")
                await clear_scheduled_messages(client, entity, group_name)
                # Small delay to avoid API flood
                await asyncio.sleep(0.5)
                
        # Schedule calculations
        print(Fore.CYAN + f"\n  [📅] Pre-calculating schedule times (Auto-Night shifting enabled)...")
        schedule_queue = []
        start_time = _get_now_tz(tz_name)
        running_time = start_time
        
        for cycle in range(1, total_cycles + 1):
            for msg_idx, msg in enumerate(valid_messages, 1):
                for g_idx, (group_url, entity) in enumerate(resolved_groups):
                    # Compute spaced timing per group
                    jitter = random.uniform(0.9, 1.1)
                    offset_sec = int(g_idx * delay * jitter)
                    
                    scheduled_time = running_time + timedelta(seconds=offset_sec)
                    
                    # Shift if inside Auto-Night window
                    if autonight_is_quiet_at(scheduled_time, autonight_cfg):
                        shift_sec = get_seconds_until_quiet_end_at(scheduled_time, autonight_cfg)
                        running_time += timedelta(seconds=shift_sec)
                        scheduled_time = running_time + timedelta(seconds=offset_sec)
                        print(Fore.YELLOW + f"    [🌙] Time shifted past quiet window to: {scheduled_time.strftime('%Y-%m-%d %H:%M:%S')}")
                        
                    # Telegram scheduled messages must be at least 15-20s in the future
                    now_tz = _get_now_tz(tz_name)
                    min_future = now_tz + timedelta(seconds=20)
                    if scheduled_time < min_future:
                        diff = (min_future - scheduled_time).total_seconds()
                        running_time += timedelta(seconds=diff)
                        scheduled_time = min_future
                        
                    schedule_queue.append({
                        "msg_idx": msg_idx,
                        "msg": msg,
                        "group_url": group_url,
                        "entity": entity,
                        "time": scheduled_time,
                        "cycle": cycle
                    })
                
                # Advance base running_time by cycle delay after messaging this message
                running_time += timedelta(minutes=cycle_min)
                
        if not schedule_queue:
            print(Fore.RED + "  [!] No messages to schedule.")
            return
            
        first_time = schedule_queue[0]["time"]
        last_time = schedule_queue[-1]["time"]
        print(Fore.GREEN + f"\n  [✔] Ready to schedule {len(schedule_queue)} messages.")
        print(f"  • First message sends at: {first_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  • Last message sends at : {last_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        confirm = input(Fore.YELLOW + "\n  ❯ Confirm and begin scheduling? [y/N]: ").strip().lower()
        if confirm not in {"y", "yes"}:
            print(Fore.RED + "  [✖] Scheduling cancelled.")
            return
            
        print(Fore.CYAN + f"\n  [🚀] Scheduling started. Sleeping 1.5s between API calls to prevent Telegram FloodWait limits...")
        success_count = 0
        fail_count = 0
        
        for idx, item in enumerate(schedule_queue, 1):
            entity = item["entity"]
            group_name = getattr(entity, 'title', item["group_url"])
            msg = item["msg"]
            sch_time = item["time"]
            cycle = item["cycle"]
            msg_idx = item["msg_idx"]
            
            print(Fore.WHITE + f"    [{idx}/{len(schedule_queue)}] Cycle {cycle} | Msg {msg_idx} -> {group_name} @ {sch_time.strftime('%H:%M:%S')}... ", end="")
            sys.stdout.flush()
            
            try:
                if use_copy:
                    caption = msg.text or ""
                    from telethon.tl.types import MessageMediaWebPage
                    if msg.media and not isinstance(msg.media, MessageMediaWebPage):
                        await client.send_file(entity, msg.media, caption=caption, schedule=sch_time)
                    else:
                        await client.send_message(entity, caption, schedule=sch_time)
                else:
                    await client.forward_messages(entity, msg, schedule=sch_time)
                    
                success_count += 1
                print(Fore.GREEN + "SUCCESS ✅")
            except FloodWaitError as e:
                fail_count += 1
                print(Fore.RED + f"FAILED ✖ (FloodWait: {e.seconds}s)")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                fail_count += 1
                print(Fore.RED + f"FAILED ✖ ({type(e).__name__})")
                
            # Add anti-flood delay between calls
            await asyncio.sleep(1.5)
            
        print(Fore.GREEN + Style.BRIGHT + f"\n  🎉 Scheduling Campaign Complete!")
        print(f"  • Successfully Scheduled: {Fore.GREEN}{success_count}")
        print(f"  • Failed to Schedule    : {Fore.RED if fail_count > 0 else Fore.WHITE}{fail_count}")
        print(Fore.YELLOW + "\n  Note: You can now safely close the script. Telegram servers will deliver the scheduled messages.")
        input("\n  Press Enter to return to main menu...")
        
    finally:
        await client.disconnect()
