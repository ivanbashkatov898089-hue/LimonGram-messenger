# server_fixed.py
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Limongram Messenger API")

# Настройка CORS - разрешаем все источники для разработки
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
        self.user_keys: Dict[str, str] = {}  # Хранилище ключей шифрования

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        self.connection_users[websocket] = username
        
        logger.info(f"✅ User {username} connected")
        
        # Отправляем список пользователей новому пользователю
        await self.send_user_list(username)
        
        # Уведомляем всех о новом пользователе
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        username = self.connection_users.get(websocket)
        if username:
            del self.active_connections[username]
            del self.connection_users[websocket]
            if username in self.user_keys:
                del self.user_keys[username]
            logger.info(f"❌ User {username} disconnected")
            asyncio.create_task(self.broadcast_user_list())

    async def send_to_user(self, message: dict, username: str):
        if username in self.active_connections:
            try:
                await self.active_connections[username].send_json(message)
                return True
            except Exception as e:
                logger.error(f"Error sending message to {username}: {e}")
        return False

    async def broadcast(self, message: dict, exclude: str = None):
        for username, connection in self.active_connections.items():
            if exclude and username == exclude:
                continue
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to {username}: {e}")

    async def broadcast_user_list(self):
        users = list(self.active_connections.keys())
        message = {
            "type": "user_list",
            "users": users,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast(message)

    async def send_user_list(self, username: str):
        """Отправляет список пользователей конкретному пользователю"""
        users = list(self.active_connections.keys())
        message = {
            "type": "user_list",
            "users": users,
            "timestamp": datetime.now().isoformat()
        }
        await self.send_to_user(message, username)

manager = ConnectionManager()

# Эндпоинт для проверки работы сервера
@app.get("/")
async def root():
    return {
        "message": "🍋 Limongram Server is running",
        "status": "online",
        "endpoints": {
            "GET /": "This page",
            "GET /health": "Health check",
            "POST /register": "Register user (not used in this version)",
            "POST /login": "Login user (not used in this version)",
            "WS /ws/{username}": "WebSocket connection"
        }
    }

# Эндпоинт для проверки здоровья
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "connections": len(manager.active_connections)
    }

# Эндпоинт для статического HTML (если нужно)
@app.get("/app")
async def get_app():
    try:
        if os.path.exists("index.html"):
            with open("index.html", "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)
        else:
            return JSONResponse({
                "error": "index.html not found",
                "message": "Please create index.html file"
            })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# WebSocket эндпоинт для обмена сообщениями
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    # Подключаем пользователя
    await manager.connect(websocket, username)
    
    try:
        while True:
            # Получаем сообщение от клиента
            data = await websocket.receive_json()
            logger.info(f"📨 Received from {username}: {data.get('type')}")
            
            # Обрабатываем разные типы сообщений
            if data["type"] == "key_exchange":
                # Сохраняем ключ шифрования пользователя
                manager.user_keys[username] = data.get("key")
                logger.info(f"🔑 Key received from {username}")
                
                # Отправляем подтверждение
                await manager.send_to_user({
                    "type": "key_exchange_ack",
                    "status": "success"
                }, username)
                
                # Отправляем ключ всем остальным пользователям
                for user in manager.active_connections:
                    if user != username and user in manager.user_keys:
                        await manager.send_to_user({
                            "type": "key_exchange",
                            "from": username,
                            "key": data.get("key")
                        }, user)
            
            elif data["type"] == "encrypted":
                # Пересылаем зашифрованное сообщение получателю
                target = data.get("to")
                if target:
                    success = await manager.send_to_user({
                        "type": "encrypted",
                        "from": username,
                        "data": data.get("data")
                    }, target)
                    
                    if success:
                        logger.info(f"🔐 Encrypted message from {username} to {target}")
                    else:
                        logger.warning(f"❌ Failed to send message to {target} - user offline")
            
            elif data["type"] == "message":
                # Прямое сообщение (для совместимости)
                target = data.get("to")
                if target:
                    await manager.send_to_user({
                        "type": "message",
                        "from": username,
                        "content": data.get("content"),
                        "timestamp": datetime.now().isoformat()
                    }, target)
            
            elif data["type"] == "call_offer":
                # Предложение звонка
                target = data.get("to")
                if target:
                    logger.info(f"📞 Call offer from {username} to {target}")
                    await manager.send_to_user({
                        "type": "call_offer",
                        "caller": username,
                        "offer": data.get("offer"),
                        "call_id": data.get("call_id", str(datetime.now().timestamp()))
                    }, target)
            
            elif data["type"] == "call_answer":
                # Ответ на звонок
                target = data.get("to")
                if target:
                    logger.info(f"📞 Call answer from {username} to {target}")
                    await manager.send_to_user({
                        "type": "call_answer",
                        "answer": data.get("answer"),
                        "call_id": data.get("call_id")
                    }, target)
            
            elif data["type"] == "ice_candidate":
                # ICE кандидат для WebRTC
                target = data.get("to")
                if target:
                    logger.info(f"❄️ ICE candidate from {username} to {target}")
                    await manager.send_to_user({
                        "type": "ice_candidate",
                        "candidate": data.get("candidate"),
                        "call_id": data.get("call_id")
                    }, target)
            
            elif data["type"] == "end_call":
                # Завершение звонка
                target = data.get("to")
                if target:
                    logger.info(f"📞 Call ended from {username} to {target}")
                    await manager.send_to_user({
                        "type": "end_call",
                        "call_id": data.get("call_id")
                    }, target)
            
            elif data["type"] == "ping":
                # Проверка соединения
                await manager.send_to_user({
                    "type": "pong",
                    "timestamp": datetime.now().isoformat()
                }, username)
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(f"User {username} disconnected from WebSocket")
    except Exception as e:
        logger.error(f"❌ WebSocket error for {username}: {e}")
        manager.disconnect(websocket)

# Функция для запуска сервера
def start_server():
    """Запускает сервер на порту 8000"""
    print("🍋 Limongram Server starting...")
    print("📡 WebSocket endpoint: ws://localhost:8000/ws/{username}")
    print("🌐 HTTP endpoint: http://localhost:8000")
    print("🔍 Health check: http://localhost:8000/health")
    print("📁 Static files: http://localhost:8000/app (if index.html exists)")
    print("Press Ctrl+C to stop the server")
    
    uvicorn.run(
        "server_fixed:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    start_server()
