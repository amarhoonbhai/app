import os
import re
import subprocess
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from colorama import Fore, Style, init
from backend.database.db import SessionLocal, Account, Stats, Group, init_db

# ---------- Init ----------
init(autoreset=True)
RUNNER_LOG = "runner.log"

def is_runner_running() -> bool:
    try:
        if os.name == 'nt':
            cmd = 'powershell "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \'*runner.py*\' }"'
            output = subprocess.check_output(cmd, shell=True).decode('cp1252', 'ignore')
            return "runner.py" in output
        else:
            out = subprocess.run(["pgrep", "-af", "runner.py"], stdout=subprocess.PIPE, text=True)
            return bool(out.stdout.strip())
    except Exception:
        return False

def start_runner():
    if is_runner_running():
        return
    
    python_cmd = "python" if os.name == "nt" else "python3"
    with open(RUNNER_LOG, "ab") as logf:
        subprocess.Popen([python_cmd, "runner.py"], stdout=logf, stderr=logf)
    print(Fore.CYAN + "  [🔁] Background engine started.")

def main_menu():
    init_db()
    start_runner() # Automatic start
    
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(Fore.BLUE + Style.BRIGHT + "╔════════════════════════════════════════╗")
        print(Fore.BLUE + Style.BRIGHT + "║      ELITE FORWARDER V6 MANAGER        ║")
        print(Fore.BLUE + Style.BRIGHT + "╚════════════════════════════════════════╝")
        
        print(f"  1. List Accounts")
        print(f"  2. Add New Session (Fixed OTP)")
        print(f"  3. Manage Groups")
        print(f"  4. Remove Account")
        print(f"  5. Restart Engine")
        print(f"  6. Exit")
        
        choice = input(Fore.YELLOW + "\n  ❯ Select an option: ")
        
        if choice == "1":
            list_accounts()
        elif choice == "2":
            add_account()
        elif choice == "3":
            manage_groups()
        elif choice == "4":
            remove_account()
        elif choice == "5":
            start_runner()
            input("\n  Press Enter...")
        elif choice == "6":
            break

def list_accounts():
    db = SessionLocal()
    accounts = db.query(Account).all()
    print(Fore.CYAN + "\n  ⭐ Registered Accounts:")
    for acc in accounts:
        print(f"  ID: {acc.id} | Name: {acc.name} | Groups: {len(acc.groups)}")
    db.close()
    input("\n  Press Enter...")

def add_account():
    print(Fore.CYAN + "\n  [ NEW SESSION LOGIN ]")
    name = input("  Account Name: ").strip()
    api_id = input("  API ID: ").strip()
    api_hash = input("  API Hash: ").strip()
    phone = input("  Phone (with +country): ").strip()
    
    if not api_id.isdigit():
        print(Fore.RED + "  [!] API ID must be numeric.")
        input()
        return

    os.makedirs("sessions", exist_ok=True)
    session_path = os.path.join("sessions", f"{phone}")
    client = TelegramClient(session_path, int(api_id), api_hash)
    
    try:
        # Use client.start() for a much more robust interactive login (handles OTP/2FA automatically)
        client.start(phone=phone)
        
        me = client.get_me()
        user_display = (getattr(me, "first_name", "") or getattr(me, "username", "") or str(me.id))
        print(Fore.GREEN + f"  [✔] Logged in as: {user_display}")
        
        db = SessionLocal()
        acc = Account(phone=phone, name=name or user_display, api_id=int(api_id), api_hash=api_hash)
        db.add(acc)
        db.flush()
        db.add(Stats(account_id=acc.id))
        db.commit()
        db.close()
        
    except Exception as e:
        print(Fore.RED + f"  [!] Login failed: {e}")
    finally:
        client.disconnect()
    input("\n  Press Enter...")

def manage_groups():
    db = SessionLocal()
    accounts = db.query(Account).all()
    if not accounts:
        print(Fore.YELLOW + "  [!] No accounts found.")
        db.close()
        input()
        return
    
    for acc in accounts: print(f"  {acc.id}. {acc.name}")
    acc_id = input("\n  Enter Account ID: ")
    acc = db.query(Account).filter(Account.id == acc_id).first()
    
    if not acc:
        print(Fore.RED + "  [!] Not found.")
        db.close()
        input()
        return
    
    while True:
        print(f"\n  Groups for {acc.name}:")
        for i, g in enumerate(acc.groups): print(f"  {i+1}. {g.url}")
        print("\n  [A] Add  [D] Delete  [B] Back")
        opt = input("  ❯ ").lower()
        if opt == 'a':
            url = input("  URL: ")
            db.add(Group(url=url, account_id=acc.id))
            db.commit()
        elif opt == 'd':
            idx = int(input("  Number: ")) - 1
            if 0 <= idx < len(acc.groups):
                db.delete(acc.groups[idx])
                db.commit()
        elif opt == 'b':
            break
    db.close()

def remove_account():
    db = SessionLocal()
    acc_id = input("  ID to remove: ")
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if acc:
        db.delete(acc)
        db.commit()
        print(Fore.RED + "  [✖] Removed.")
    db.close()
    input()

if __name__ == "__main__":
    main_menu()
