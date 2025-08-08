import os
import html
import secrets
import pathlib
from typing import Optional

import uvicorn
import sqlite3
import jwt

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends,
    Request, HTTPException, status, Path
)
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

SECRET_KEY = "some_worlds_secret_key_for_jwt"
ALGORITHM = "HS256"
CHAT_DB_USERS = "chat.db"
GLOBAL_URL = "127.0.0.1:8000"

module_path = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=module_path / "templates")

app = FastAPI(title="WebSocket Secure Chat")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def create_tables():
    with sqlite3.connect(CHAT_DB_USERS) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                token TEXT NOT NULL UNIQUE
            );
        """)
        conn.commit()


@app.on_event("startup")
async def startup_event():
    create_tables()


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


def create_jwt_token(username: str) -> str:
    return jwt.encode({"sub": username}, SECRET_KEY, algorithm=ALGORITHM)


def decode_jwt_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


@app.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    username = form_data.username
    if not username or "admin" not in username:
        raise HTTPException(status_code=400, detail="Invalid username or password")

    token = create_jwt_token(username)
    return Token(access_token=token)


def verify_user_token(token: str):
    username = decode_jwt_token(token)
    if not username:
        return None

    with sqlite3.connect(CHAT_DB_USERS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE name = ? AND token = ?", (username, token))
        if cursor.fetchone():
            return username
    return None


@app.post("/register/{name}")
async def register(name: str = Path(min_length=2, max_length=30)):
    token = create_jwt_token(name)
    with sqlite3.connect(CHAT_DB_USERS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
        if cursor.fetchone():
            raise HTTPException(400, "User already exists")

        cursor.execute("INSERT INTO users (name, token) VALUES (?, ?)", (name, token))
        conn.commit()
    return {"success": {"user": name, "url": f"{GLOBAL_URL}/chat/{name}/{token}"}}


@app.get("/chat/{name}/{token}")
async def chat_page(
    request: Request,
    name: str,
    token: str,
):
    username = verify_user_token(token)
    if username is None or username != name:
        return HTMLResponse(content="<h1>403 Forbidden</h1>", status_code=403)

    return templates.TemplateResponse(
        request,
        name="chat.html",
        context={"token": token, "name": name, "ws_base_url": GLOBAL_URL},
    )


class WebSocketManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, token: str):
        await websocket.accept()
        self.active_connections[token] = websocket

    def disconnect(self, token: str):
        self.active_connections.pop(token, None)

    async def send_personal(self, token: str, message: str):
        ws = self.active_connections.get(token)
        if ws:
            await ws.send_text(message)

    async def broadcast(self, message: str, exclude: set[str] = None):
        exclude = exclude or set()
        for token, ws in self.active_connections.items():
            if token not in exclude:
                await ws.send_text(message)


manager = WebSocketManager()


@app.websocket("/ws/{name}/{token}")
async def websocket_endpoint(websocket: WebSocket, name: str, token: str):
    username = verify_user_token(token)
    if username is None or username != name:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, token)
    await manager.broadcast(f"{name.title()} joined the chat", exclude={token})
    try:
        while True:
            data = await websocket.receive_json()
            msg_raw = data.get("message", "")
            to_user = data.get("to")

            message = html.escape(msg_raw[:500])

            if to_user:
                with sqlite3.connect(CHAT_DB_USERS) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT token FROM users WHERE name = ?", (to_user,))
                    result = cursor.fetchone()

                if result and result[0] in manager.active_connections:
                    await manager.send_personal(result[0], f"{name} >>> {message}")
                    await manager.send_personal(token, f"You >>> {message}")
                else:
                    await manager.send_personal(token, f"User {to_user} is offline.")
            else:
                await manager.broadcast(f"{name} >>> {message}", exclude={token})
    except WebSocketDisconnect:
        manager.disconnect(token)
        await manager.broadcast(f"{name} left the chat")


if __name__ == "__main__":
    filename = os.path.basename(__file__).split(".")[0]
    uvicorn.run(f"{filename}:app", host="127.0.0.1", port=8000, reload=True)
