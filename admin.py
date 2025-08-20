import json
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Replace with your actual bot token
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

USERS_FILE = Path("users.json")
USERS_DIR = Path("users")


def load_users():
    try:
        with USERS_FILE.open('r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    try:
        with USERS_FILE.open('w') as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"Error saving users: {e}")


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    if not users:
        await update.message.reply_text("No users found.")
        return

    msg = "📋 *Registered Users:*
"
    for phone, data in users.items():
        plan = data.get("plan", "free")
        expiry = data.get("expiry", "N/A")
        msg += f"- `{phone}` | {data.get('name', 'Unknown')} | {plan} | {expiry}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        phone = context.args[0]
        days = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /upgrade <phone> <days>")
        return

    users = load_users()
    if phone not in users:
        await update.message.reply_text(f"User {phone} not found.")
        return

    expiry_date = datetime.now() + timedelta(days=days)
    users[phone]["plan"] = "premium"
    users[phone]["expiry"] = expiry_date.isoformat()
    save_users(users)

    user_file = USERS_DIR / f"{phone}.json"
    if user_file.exists():
        try:
            with user_file.open("r") as f:
                data = json.load(f)
            data["plan"] = "premium"
            data["expiry"] = expiry_date.isoformat()
            with user_file.open("w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to update user file: {e}")

    await update.message.reply_text(f"✅ Upgraded {phone} to premium for {days} days.")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("upgrade", upgrade_command))

    print("Admin bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
