# server.py (обновленная версия с шифрованием)
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Request
import uvicorn

# Импортируем наше шифрованное хранилище
from encrypted_storage import storage

app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_users: Dict[WebSocket, str] = {}
        self.user_sessions: Dict[str, dict] = {}  # Сессии пользователей

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        self.connection_users[websocket] = username
        
        # Загружаем историю сообщений для пользователя
        history = storage.get_user_history(username)
        await self.send_to_user({
            "type": "history",
            "messages": history
        }, username)
        
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        username = self.connection_users.get(websocket)
        if username:
            del self.active_connections[username]
            del self.connection_users[websocket]
            asyncio.create_task(self.broadcast_user_list())

    async def send_to_user(self, message: dict, username: str):
        if username in self.active_connections:
            try:
                await self.active_connections[username].send_json(message)
            except:
                pass

    async def broadcast(self, message: dict):
        for connection in self.active_connections.values():
            try:
                await connection.send_json(message)
            except:
                pass

    async def broadcast_user_list(self):
        users = list(self.active_connections.keys())
        message = {
            "type": "user_list",
            "users": users,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast(message)

manager = ConnectionManager()

# Эндпоинт для регистрации
@app.post("/register")
async def register(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return JSONResponse({"error": "Username and password required"}, status_code=400)
    
    try:
        storage.register_user(username, password)
        return JSONResponse({"success": True, "message": "User registered successfully"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

# Эндпоинт для аутентификации
@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")
    
    if storage.authenticate_user(username, password):
        return JSONResponse({"success": True, "message": "Login successful"})
    else:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

# Эндпоинт для получения истории
@app.get("/history/{username}")
async def get_history(username: str, password: str):
    if storage.authenticate_user(username, password):
        history = storage.get_user_history(username)
        return JSONResponse({"history": history})
    return JSONResponse({"error": "Authentication failed"}, status_code=401)

@app.get("/")
async def get():
    return HTMLResponse(open("index.html").read())

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    try:
        while True:
            data = await websocket.receive_json()

            if data["type"] == "message":
                # Создаем сообщение для сохранения
                message_data = {
                    "from": username,
                    "to": data.get("to"),
                    "content": data["content"],
                    "type": "text",
                    "timestamp": datetime.now().isoformat()
                }
                
                # Сохраняем в историю отправителя
                storage.save_message(username, message_data)
                
                # Если есть получатель, сохраняем и в его историю
                if data.get("to"):
                    # Добавляем пометку о том, что сообщение входящее
                    receiver_message = message_data.copy()
                    receiver_message["from"] = username
                    receiver_message["type"] = "received"
                    storage.save_message(data["to"], receiver_message)
                
                # Отправляем сообщение
                if data.get("to"):
                    await manager.send_to_user({
                        "type": "message",
                        "from": username,
                        "content": data["content"],
                        "timestamp": message_data["timestamp"]
                    }, data["to"])
                else:
                    await manager.broadcast({
                        "type": "message",
                        "from": username,
                        "content": data["content"],
                        "timestamp": message_data["timestamp"]
                    })

            elif data["type"] == "call_offer":
                # Сохраняем информацию о звонке
                call_data = {
                    "from": username,
                    "to": data["to"],
                    "type": "call_offer",
                    "call_id": data.get("call_id"),
                    "timestamp": datetime.now().isoformat()
                }
                storage.save_message(username, call_data)
                
                await manager.send_to_user({
                    "type": "call_offer",
                    "from": username,
                    "offer": data["offer"],
                    "call_id": data.get("call_id")
                }, data["to"])

            elif data["type"] == "call_answer":
                await manager.send_to_user({
                    "type": "call_answer",
                    "from": username,
                    "answer": data["answer"],
                    "call_id": data.get("call_id")
                }, data["to"])

            elif data["type"] == "ice_candidate":
                await manager.send_to_user({
                    "type": "ice_candidate",
                    "from": username,
                    "candidate": data["candidate"],
                    "call_id": data.get("call_id")
                }, data["to"])

            elif data["type"] == "end_call":
                # Сохраняем информацию о завершении звонка
                call_end_data = {
                    "from": username,
                    "to": data.get("to"),
                    "type": "call_ended",
                    "call_id": data.get("call_id"),
                    "timestamp": datetime.now().isoformat()
                }
                storage.save_message(username, call_end_data)
                
                if data.get("to"):
                    await manager.send_to_user({
                        "type": "end_call",
                        "from": username,
                        "call_id": data.get("call_id")
                    }, data["to"])

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logging.info(f"User {username} disconnected")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
