import os
import json
from backend.database.db import SessionLocal, Account, Stats, Group, init_db

def migrate():
    init_db()
    db = SessionLocal()
    
    USERS_DIR = "users"
    if not os.path.exists(USERS_DIR):
        print("No users directory found. Nothing to migrate.")
        return

    for file in os.listdir(USERS_DIR):
        if file.endswith(".json"):
            path = os.path.join(USERS_DIR, file)
            with open(path, 'r', encoding="utf-8") as f:
                data = json.load(f)
                
                # Check if account exists
                phone = data.get("phone")
                if db.query(Account).filter(Account.phone == phone).first():
                    print(f"Account {phone} already in DB. Skipping.")
                    continue
                
                acc = Account(
                    phone=phone,
                    name=data.get("name", "Unknown"),
                    api_id=int(data.get("api_id")),
                    api_hash=data.get("api_hash"),
                    cycle_delay_min=data.get("cycle_delay_min", 15),
                    msg_delay_sec=data.get("msg_delay_sec", 30),
                    use_copy=data.get("use_copy", True)
                )
                db.add(acc)
                db.flush() # Get ID
                
                # Add groups
                for url in data.get("groups", []):
                    db.add(Group(url=url, account_id=acc.id))
                
                # Add stats
                db.add(Stats(account_id=acc.id))
                
                print(f"Migrated {phone} ({len(data.get('groups', []))} groups)")
    
    db.commit()
    db.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
