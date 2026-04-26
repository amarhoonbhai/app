from telethon.sync import TelegramClient
import sys

# Get credentials from user
api_id = input("Enter API ID: ")
api_hash = input("Enter API HASH: ")
phone = input("Enter Phone (+country format): ")

client = TelegramClient(f"sessions/{phone}", int(api_id), api_hash)

print("Attempting to send OTP...")
try:
    client.start(phone=phone)
    print("Success! You are logged in.")
    me = client.get_me()
    print(f"Logged in as: {me.first_name}")
except Exception as e:
    print(f"Error: {e}")
finally:
    client.disconnect()
