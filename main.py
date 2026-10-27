import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
 
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel


DB_PATH = os.environ.get("DB_PATH", "panel.db")
ADMIN_KEY = os.environ.get("ADMIN_KEY")
ONLINE_WINDOW = 45
COMMAND_TTL = 300
ALLOWED_COMMANDS = {"shutdown"}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machines (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL UNIQUE,
            token_hash     TEXT NOT NULL UNIQUE,
            last_seen      REAL,            -- unix time of last heartbeat
            pending_cmd    TEXT,            -- e.g. 'shutdown', or NULL
            cmd_queued_at  REAL
        )
        """
    )
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ADMIN_KEY:
        raise RuntimeError("Set the ADMIN_KEY envirement variable before starting.")
    init_db()
    yield



app = FastAPI(lifespan=lifespan)

def hash_token(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def require_admin(x_admin_key: str = Header()):
    if not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=401, detail="Bad admin key")


def get_machine_from_token(
        authorization: str = Header(),
        db: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    row = db.execute(
        "SELECT * FROM machines WHERE token_hash = ?", (hash_token(token),)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown token.")

    return row



class RegisterBody(BaseModel):
    name: str

class CommandBody(BaseModel):
    command: str




@app.post("/api/machines", dependencies=[Depends(require_admin)])
def register_machine(body: RegisterBody, db: sqlite3.connection = Depends(get_db)):
    token = secrets.token_urlsafe(32)
    try:
        db.execute(
            "INSERT INTO machines (name, token_hash) VALUES (?, ?)",
            (body.name, hash_token(token)),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Machine name already exists.")

    return {"name": body.name, "token": token}





@app.post("/api/heartbeat")
def heartbeat(
    machine: sqlite3.Row = Depends(get_machine_from_token),
    db: sqlite3.Connection = Depends(get_db),
):
    now = time.time()
    command = machine["pending_cmd"]


    if command and now - machine["cmd_queued_at"] > COMMAND_TTL:
        command = None

    db.execute(
        "UPDATE machines SET last_seen = ?, pending_cmd = NULL, cmd_queued_at = NULL WHERE id = ?",
        (now, machine["id"]),
    )

    return {"command": command}



@app.get("/api/machines", dependencies=[Depends(require_admin)])
def list_machines(db: sqlite3.Connection = Depends(get_db)):
    now = time.time()
    rows = db.execute("SELECT * FROM machines ORDER BY name").fetchall()
    return [
        {
            "name": r["name"],
            "online": r["last_seen"] is not None and now - r["last_seen"] < ONLINE_WINDOW,
            "last_seen": r["last_seen"],
            "seconds_since_seen": None if r["last_seen"] is None else round(now - r["last_seen"]),
            "pending_cmd": r["pending_cmd"]
        }
        for r in rows
    ]


@app.post("/api/machines/{name}/command", dependencies=[Depends(require_admin)])
def queue_command(name: str, body: CommandBody, db: sqlite3.Connection = Depends(get_db)):
    if body.command not in ALLOWED_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Allowed commands: {sorted(ALLOWED_COMMANDS)}")


    cur = db.execute(
        "UPDATE machines SET pending_cmd = ?, cmd_queued_at = ? WHERE name = ?",
        (body.command, time.time(), name),
    )

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="No such machine.")

    return {"queued": body.command, "machine": name}



