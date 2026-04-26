from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from backend.database.db import init_db, get_db, Account, Stats, Group
from backend.models import schemas
from backend.core.manager import manager
import uvicorn
import asyncio

app = FastAPI(title="Elite Scheduler V6 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(manager.start_all())

@app.get("/stats", response_model=list[schemas.StatsResponse])
def get_all_stats(db: Session = Depends(get_db)):
    return db.query(Stats).all()

@app.get("/accounts", response_model=list[schemas.AccountResponse])
def get_accounts(db: Session = Depends(get_db)):
    return db.query(Account).all()

@app.post("/accounts", response_model=schemas.AccountResponse)
async def create_account(acc: schemas.AccountCreate, db: Session = Depends(get_db)):
    db_acc = Account(**acc.dict())
    db.add(db_acc)
    db.commit()
    db.refresh(db_acc)
    
    # Initialize stats
    db_stats = Stats(account_id=db_acc.id)
    db.add(db_stats)
    db.commit()
    
    await manager.start_account(db_acc.id)
    return db_acc

@app.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    
    await manager.stop_account(account_id)
    db.delete(acc)
    db.commit()
    return {"message": "Account deleted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
