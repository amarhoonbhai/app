import asyncio
import sys
import os
from loguru import logger
from backend.database.db import init_db, SessionLocal, Account
from backend.core.manager import manager

# Configure loguru to be simpler
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

async def main():
    init_db()
    
    # Start all accounts
    await manager.start_all()
    
    logger.info(f"Engine Started. Active Accounts: {len(manager.engines)}")
    
    # Keep running and show periodic stats in console
    while True:
        try:
            await asyncio.sleep(60)
            # Periodic health check or simple heartbeat
            logger.info(f"Heartbeat: {len(manager.engines)} engines running.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Shutdown requested...")
        sys.exit(0)
