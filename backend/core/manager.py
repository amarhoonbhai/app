import asyncio
from backend.core.engine import ForwardingEngine
from backend.database.db import SessionLocal, Account
from loguru import logger

class AccountManager:
    def __init__(self):
        self.engines = {}

    async def start_all(self):
        db = SessionLocal()
        accounts = db.query(Account).all()
        db.close()

        for acc in accounts:
            await self.start_account(acc.id)

    async def start_account(self, account_id: int):
        if account_id in self.engines:
            return
        
        engine = ForwardingEngine(account_id)
        self.engines[account_id] = engine
        asyncio.create_task(engine.start())
        logger.info(f"Initialized engine for Account ID: {account_id}")

    async def stop_account(self, account_id: int):
        if account_id in self.engines:
            self.engines[account_id].is_running = False
            del self.engines[account_id]
            logger.info(f"Stopped engine for Account ID: {account_id}")

manager = AccountManager()
