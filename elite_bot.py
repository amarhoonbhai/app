import asyncio
import sys
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from backend.database.db import init_db, SessionLocal, Account, Stats
from backend.core.manager import manager

console = Console()

def create_dashboard_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3)
    )
    return layout

def get_stats_table():
    db = SessionLocal()
    accounts = db.query(Account).all()
    
    table = Table(title="[bold blue]Elite Forwarder V6 - Active Sessions[/bold blue]", expand=True)
    table.add_column("ID", justify="center", style="cyan")
    table.add_column("Account Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Success", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Next Message", justify="center", style="yellow")
    table.add_column("Last Cycle", justify="center", style="magenta")

    for acc in accounts:
        stats = acc.stats
        if not stats: continue
        
        status_style = "green" if "Forwarding" in stats.status else "yellow"
        if "Error" in stats.status: status_style = "red"
        
        next_msg = stats.next_msg_at.strftime("%H:%M:%S") if stats.next_msg_at else "N/A"
        last_cycle = stats.last_cycle_at.strftime("%H:%M:%S") if stats.last_cycle_at else "N/A"
        
        table.add_row(
            str(acc.id),
            acc.name,
            f"[{status_style}]{stats.status}[/{status_style}]",
            str(stats.success_total),
            str(stats.fail_total),
            next_msg,
            last_cycle
        )
    
    db.close()
    return table

async def run_dashboard():
    init_db()
    await manager.start_all()
    
    layout = create_dashboard_layout()
    layout["header"].update(Panel(Text("TELETHON MULTI-USER MANAGER V6 ELITE - PRESTIGE EDITION", justify="center", style="bold white on blue")))
    layout["footer"].update(Panel(Text(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", justify="center", style="dim")))

    with Live(layout, refresh_per_second=1, screen=True):
        while True:
            layout["main"].update(get_stats_table())
            layout["footer"].update(Panel(Text(f"System Status: ONLINE | Active Engines: {len(manager.engines)} | Last Updated: {datetime.now().strftime('%H:%M:%S')}", justify="center", style="dim")))
            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        console.print("\n[bold red]Shutting down...[/bold red]")
        sys.exit(0)
