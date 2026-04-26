import os
import re
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from backend.database.db import SessionLocal, Account, Stats, Group, init_db

console = Console()

def main_menu():
    init_db()
    while True:
        console.clear()
        console.print(Panel("[bold blue]Elite Forwarder V6 - Session Manager[/bold blue]", expand=False))
        
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Option", style="dim", width=12)
        table.add_column("Description")
        
        table.add_row("1", "List Registered Accounts")
        table.add_row("2", "Add New Premium Session")
        table.add_row("3", "Manage Groups for Account")
        table.add_row("4", "Remove Account")
        table.add_row("5", "Exit")
        
        console.print(table)
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5"])
        
        if choice == "1":
            list_accounts()
        elif choice == "2":
            add_account()
        elif choice == "3":
            manage_groups()
        elif choice == "4":
            remove_account()
        elif choice == "5":
            break

def list_accounts():
    db = SessionLocal()
    accounts = db.query(Account).all()
    
    table = Table(title="Registered Accounts")
    table.add_column("ID", justify="center")
    table.add_column("Name")
    table.add_column("Phone")
    table.add_column("Groups")
    
    for acc in accounts:
        table.add_row(str(acc.id), acc.name, acc.phone, str(len(acc.groups)))
    
    console.print(table)
    db.close()
    Prompt.ask("\nPress Enter to return")

def add_account():
    console.print("\n[bold cyan]Add New Premium Session[/bold cyan]")
    name = Prompt.ask("Account Name")
    api_id = Prompt.ask("API ID")
    api_hash = Prompt.ask("API Hash")
    phone = Prompt.ask("Phone (with +country code)")
    
    if not api_id.isdigit():
        console.print("[red]Error: API ID must be numeric.[/red]")
        return

    os.makedirs("sessions", exist_ok=True)
    session_path = f"sessions/{phone}"
    client = TelegramClient(session_path, int(api_id), api_hash)
    
    try:
        client.connect()
        if not client.is_user_authorized():
            client.send_code_request(phone)
            code = Prompt.ask("Enter Telegram Code")
            try:
                client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = Prompt.ask("2FA Password", password=True)
                client.sign_in(password=password)
        
        me = client.get_me()
        user_display = (getattr(me, "first_name", "") or getattr(me, "username", "") or str(me.id))
        console.print(f"[green]Successfully logged in as: {user_display}[/green]")
        
        db = SessionLocal()
        acc = Account(
            phone=phone,
            name=name or user_display,
            api_id=int(api_id),
            api_hash=api_hash
        )
        db.add(acc)
        db.flush()
        db.add(Stats(account_id=acc.id))
        db.commit()
        db.close()
        
    except Exception as e:
        console.print(f"[red]Login failed: {e}[/red]")
    finally:
        client.disconnect()

def manage_groups():
    db = SessionLocal()
    accounts = db.query(Account).all()
    if not accounts:
        console.print("[yellow]No accounts found.[/yellow]")
        db.close()
        return
    
    list_accounts()
    acc_id = Prompt.ask("Enter Account ID to manage groups")
    acc = db.query(Account).filter(Account.id == acc_id).first()
    
    if not acc:
        console.print("[red]Account not found.[/red]")
        db.close()
        return
    
    while True:
        console.clear()
        console.print(f"[bold]Managing Groups for: {acc.name}[/bold]")
        for i, g in enumerate(acc.groups):
            console.print(f"{i+1}. {g.url}")
            
        console.print("\nOptions: [a] Add Group, [d] Delete Group, [b] Back")
        opt = Prompt.ask("Choice", choices=["a", "d", "b"])
        
        if opt == "a":
            url = Prompt.ask("Enter Group URL (https://t.me/...)")
            db.add(Group(url=url, account_id=acc.id))
            db.commit()
        elif opt == "d":
            idx = int(Prompt.ask("Enter group number to delete")) - 1
            if 0 <= idx < len(acc.groups):
                db.delete(acc.groups[idx])
                db.commit()
        elif opt == "b":
            break
    db.close()

def remove_account():
    db = SessionLocal()
    list_accounts()
    acc_id = Prompt.ask("Enter Account ID to remove")
    acc = db.query(Account).filter(Account.id == acc_id).first()
    
    if acc:
        confirm = Prompt.ask(f"Are you sure you want to delete {acc.name}?", choices=["y", "n"])
        if confirm == "y":
            db.delete(acc)
            db.commit()
            console.print("[red]Account removed.[/red]")
    else:
        console.print("[red]Account not found.[/red]")
    db.close()

if __name__ == "__main__":
    main_menu()
