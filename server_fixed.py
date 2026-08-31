# server_fixed.py - для деплоя на Render.com
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Limongram Messenger API")

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

        # Храним ТОЛЬКО публичные ключи пользователей.
        # Приватные ключи никогда не должны попадать на сервер.
        self.user_public_keys: Dict[str, dict] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()

        self.active_connections[username] = websocket
        self.connection_users[websocket] = username

        logger.info(f"✅ User {username} connected")

        # Отправляем список пользователей новому пользователю
        await self.send_user_list(username)

        # Отправляем уже известные публичные ключи
        for user, public_key in self.user_public_keys.items():
            if user != username:
                await self.send_to_user(
                    {
                        "type": "public_key",
                        "from": user,
                        "public_key": public_key
                    },
                    username
                )

        # Уведомляем всех о новом пользователе
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        username = self.connection_users.get(websocket)

        if username:
            # Удаляем соединение только если оно действительно
            # принадлежит этому пользователю.
            if self.active_connections.get(username) is websocket:
                del self.active_connections[username]

            if websocket in self.connection_users:
                del self.connection_users[websocket]

            # Удаляем публичный ключ отключившегося пользователя
            if username in self.user_public_keys:
                del self.user_public_keys[username]

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
        for username, connection in list(self.active_connections.items()):
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
        """Отправляет список пользователей конкретному пользователю."""
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
        "message": "🍋 Limongram Server is running on Render.com",
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "connections": len(manager.active_connections),
        "endpoints": {
            "GET /": "This page",
            "GET /health": "Health check",
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


# Эндпоинт для получения статистики
@app.get("/stats")
async def get_stats():
    return {
        "users_online": list(manager.active_connections.keys()),
        "total_connections": len(manager.active_connections),
        "timestamp": datetime.now().isoformat()
    }


# WebSocket эндпоинт
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)

    try:
        while True:
            data = await websocket.receive_json()

            message_type = data.get("type")

            logger.info(
                f"📨 Received from {username}: {message_type}"
            )

            # =========================================================
            # PUBLIC KEY
            # =========================================================

            if message_type == "public_key":
                public_key = data.get("public_key")

                if not isinstance(public_key, dict):
                    logger.warning(
                        f"⚠️ Invalid public key from {username}"
                    )
                    continue

                manager.user_public_keys[username] = public_key

                logger.info(
                    f"🔑 Public key received from {username}"
                )

                # Рассылаем публичный ключ остальным пользователям
                for user in list(manager.active_connections.keys()):
                    if user != username:
                        await manager.send_to_user(
                            {
                                "type": "public_key",
                                "from": username,
                                "public_key": public_key
                            },
                            user
                        )

            # =========================================================
            # ENCRYPTED MESSAGE
            # =========================================================

            elif message_type == "encrypted":
                target = data.get("to")

                if target:
                    success = await manager.send_to_user(
                        {
                            "type": "encrypted",
                            "from": username,
                            "data": data.get("data")
                        },
                        target
                    )

                    if success:
                        logger.info(
                            f"🔐 Encrypted message from "
                            f"{username} to {target}"
                        )
                    else:
                        logger.warning(
                            f"❌ Failed to send message to "
                            f"{target} - user offline"
                        )

            # =========================================================
            # DIRECT MESSAGE
            # =========================================================

            elif message_type == "message":
                # Прямое сообщение для совместимости
                target = data.get("to")

                if target:
                    await manager.send_to_user(
                        {
                            "type": "message",
                            "from": username,
                            "content": data.get("content"),
                            "timestamp": datetime.now().isoformat()
                        },
                        target
                    )

            # =========================================================
            # CALL OFFER
            # =========================================================

            elif message_type == "call_offer":
                target = data.get("to")

                if target:
                    logger.info(
                        f"📞 Call offer from {username} to {target}"
                    )

                    await manager.send_to_user(
                        {
                            "type": "call_offer",
                            "caller": username,
                            "offer": data.get("offer"),
                            "call_id": data.get(
                                "call_id",
                                str(datetime.now().timestamp())
                            )
                        },
                        target
                    )

            # =========================================================
            # CALL ANSWER
            # =========================================================

            elif message_type == "call_answer":
                target = data.get("to")

                if target:
                    logger.info(
                        f"📞 Call answer from {username} to {target}"
                    )

                    await manager.send_to_user(
                        {
                            "type": "call_answer",
                            "answer": data.get("answer"),
                            "call_id": data.get("call_id")
                        },
                        target
                    )

            # =========================================================
            # ICE CANDIDATE
            # =========================================================

            elif message_type == "ice_candidate":
                target = data.get("to")

                if target:
                    logger.info(
                        f"❄️ ICE candidate from {username} to {target}"
                    )

                    await manager.send_to_user(
                        {
                            "type": "ice_candidate",
                            "candidate": data.get("candidate"),
                            "call_id": data.get("call_id")
                        },
                        target
                    )

            # =========================================================
            # END CALL
            # =========================================================

            elif message_type == "end_call":
                target = data.get("to")

                if target:
                    logger.info(
                        f"📞 Call ended from {username} to {target}"
                    )

                    await manager.send_to_user(
                        {
                            "type": "end_call",
                            "call_id": data.get("call_id")
                        },
                        target
                    )

            # =========================================================
            # PING
            # =========================================================

            elif message_type == "ping":
                await manager.send_to_user(
                    {
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    },
                    username
                )

            else:
                logger.warning(
                    f"⚠️ Unknown message type from "
                    f"{username}: {message_type}"
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(
            f"User {username} disconnected from WebSocket"
        )

    except Exception as e:
        logger.error(
            f"❌ WebSocket error for {username}: {e}"
        )
        manager.disconnect(websocket)


# Для локального запуска
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"

    print("🍋 Limongram Server starting...")
    print(
        f"📡 WebSocket endpoint: "
        f"ws://{host}:{port}/ws/{{username}}"
    )
    print(
        f"🌐 HTTP endpoint: "
        f"http://{host}:{port}"
    )
    print(
        f"🔍 Health check: "
        f"http://{host}:{port}/health"
    )
    print(
        f"📊 Stats: "
        f"http://{host}:{port}/stats"
    )
    print("Press Ctrl+C to stop the server")

    uvicorn.run(
        "server_fixed:app",
        host=host,
        port=port,
        reload=False
    )
