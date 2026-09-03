# server_fixed.py
# Limongram backend — WebSocket relay
#
# Сервер НЕ расшифровывает сообщения.
# Он хранит только публичные ECDH-ключи активных подключений
# и пересылает зашифрованные конверты между пользователями.

import asyncio
import base64
import binascii
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState


# ============================================================
# CONFIG
# ============================================================

APP_TITLE = "Limongram Messenger API"

# Имя пользователя: 1–32 символа.
# Не разрешаем управляющие символы, слэши и пробелы по краям.
USERNAME_RE = re.compile(r"^[^\x00-\x1F\x7F/\\]{1,32}$")

# Текст/служебные сообщения небольшие, но медиа в текущем клиенте
# передаются как base64 внутри зашифрованного конверта.
# 8 MiB оставляет запас для ~3 MiB файла + base64 + JSON.
MAX_WEBSOCKET_TEXT = 8 * 1024 * 1024

# Максимальный размер зашифрованного payload.
MAX_CIPHERTEXT_BYTES = 6 * 1024 * 1024

# Не разрешаем бесконечно часто менять публичный ключ.
KEY_CHANGE_COOLDOWN = 2.0

# WebSocket heartbeat. Это не заменяет клиентскую reconnect-логику,
# но помогает не держать полностью молчаливое соединение.
HEARTBEAT_SECONDS = 25


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("limongram")


# ============================================================
# APP
# ============================================================

app = FastAPI(title=APP_TITLE)

# CORS нужен для HTTP API/health. WebSocket сам по себе не использует
# CORS как браузерный fetch, поэтому безопасность WS обеспечивается
# проверками ниже.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))


def is_base64_value(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False

    try:
        # validate=True требует нормальный стандартный base64.
        base64.b64decode(value, validate=True)
        return True
    except (binascii.Error, ValueError, TypeError):
        return False


def decode_base64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def valid_public_key(jwk: object) -> bool:
    """
    Проверяем именно публичный EC P-256 JWK.

    Ожидаемый клиентом формат:
    {
        "kty": "EC",
        "crv": "P-256",
        "x": "...",
        "y": "...",
        ...
    }

    Критично: приватное поле "d" запрещено.
    """
    if not isinstance(jwk, dict):
        return False

    if jwk.get("kty") != "EC":
        return False

    if jwk.get("crv") != "P-256":
        return False

    if "d" in jwk:
        return False

    x = jwk.get("x")
    y = jwk.get("y")

    if not isinstance(x, str) or not isinstance(y, str):
        return False

    # JWK EC coordinates используют base64url без "=".
    # Для P-256 координата — 32 байта, обычно 43 символа base64url.
    if len(x) != 43 or len(y) != 43:
        return False

    if not re.fullmatch(r"[A-Za-z0-9_-]+", x):
        return False

    if not re.fullmatch(r"[A-Za-z0-9_-]+", y):
        return False

    return True


def valid_encrypted_packet(packet: object) -> bool:
    """
    Проверяет только структуру шифротекста.
    Сервер не знает и не пытается знать его содержимое.
    """
    if not isinstance(packet, dict):
        return False

    if packet.get("v") != 2:
        return False

    if packet.get("alg") != "ECDH-P256+A256GCM":
        return False

    iv = packet.get("iv")
    ciphertext = packet.get("ciphertext")

    if not is_base64_value(iv) or not is_base64_value(ciphertext):
        return False

    try:
        iv_bytes = decode_base64(iv)
        ciphertext_bytes = decode_base64(ciphertext)
    except (binascii.Error, ValueError):
        return False

    # AES-GCM в нашем клиенте использует 96-bit nonce.
    if len(iv_bytes) != 12:
        return False

    # AES-GCM ciphertext включает authentication tag.
    if len(ciphertext_bytes) < 16:
        return False

    if len(ciphertext_bytes) > MAX_CIPHERTEXT_BYTES:
        return False

    return True


# ============================================================
# CONNECTION MANAGER
# ============================================================

class ConnectionManager:
    def __init__(self):
        # Один активный WebSocket на имя пользователя.
        self.active_connections: Dict[str, WebSocket] = {}

        # Обратная связь WebSocket -> username.
        self.connection_users: Dict[WebSocket, str] = {}

        # ТОЛЬКО публичные ключи.
        self.user_public_keys: Dict[str, dict] = {}

        # Время последнего принятого ключа.
        self.last_key_update: Dict[str, float] = {}

        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, username: str) -> bool:
        await websocket.accept()

        async with self.lock:
            existing = self.active_connections.get(username)

            # Не даём двум подключениям с одним именем одновременно
            # перехватывать сообщения друг друга.
            if existing is not None and existing is not websocket:
                try:
                    await existing.close(
                        code=4008,
                        reason="Another connection with this username was opened",
                    )
                except Exception:
                    pass

                self.connection_users.pop(existing, None)

            self.active_connections[username] = websocket
            self.connection_users[websocket] = username

        logger.info("User connected: %s", username)

        await self.send_user_list(username)

        # Отправляем этому пользователю публичные ключи уже подключённых.
        for user, public_key in list(self.user_public_keys.items()):
            if user != username and user in self.active_connections:
                await self.send_to_user(
                    {
                        "type": "public_key",
                        "from": user,
                        "public_key": public_key,
                    },
                    username,
                )

        await self.broadcast_user_list()
        return True

    async def disconnect(self, websocket: WebSocket) -> Optional[str]:
        async with self.lock:
            username = self.connection_users.pop(websocket, None)

            if not username:
                return None

            # Важно: старое соединение не должно удалить новое.
            if self.active_connections.get(username) is websocket:
                self.active_connections.pop(username, None)
                self.user_public_keys.pop(username, None)
                self.last_key_update.pop(username, None)
                removed = True
            else:
                removed = False

        if removed:
            logger.info("User disconnected: %s", username)
            await self.broadcast_user_list()

        return username

    async def send_to_user(self, message: dict, username: str) -> bool:
        connection = self.active_connections.get(username)

        if connection is None:
            return False

        try:
            await connection.send_json(message)
            return True
        except Exception as exc:
            logger.warning("Send to %s failed: %s", username, exc)

            # Не удаляем новое соединение, если ошибка пришла от старого.
            await self.disconnect(connection)
            return False

    async def broadcast(self, message: dict, exclude: Optional[str] = None):
        connections = list(self.active_connections.items())

        for username, connection in connections:
            if exclude and username == exclude:
                continue

            try:
                await connection.send_json(message)
            except Exception as exc:
                logger.warning("Broadcast to %s failed: %s", username, exc)
                await self.disconnect(connection)

    async def broadcast_user_list(self):
        users = list(self.active_connections.keys())

        await self.broadcast(
            {
                "type": "user_list",
                "users": users,
                "timestamp": now_iso(),
            }
        )

    async def send_user_list(self, username: str):
        users = list(self.active_connections.keys())

        await self.send_to_user(
            {
                "type": "user_list",
                "users": users,
                "timestamp": now_iso(),
            },
            username,
        )


manager = ConnectionManager()


# ============================================================
# HEARTBEAT
# ============================================================

async def heartbeat(websocket: WebSocket, username: str):
    """
    Приложенческий heartbeat.
    Если соединение умерло, send_json завершится ошибкой,
    после чего основной обработчик закроет/очистит его.
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)

            if websocket.client_state != WebSocketState.CONNECTED:
                return

            await websocket.send_json(
                {
                    "type": "server_ping",
                    "timestamp": now_iso(),
                }
            )
    except asyncio.CancelledError:
        return
    except Exception:
        return


# ============================================================
# HTTP
# ============================================================

@app.get("/")
async def root():
    return {
        "message": "🍋 Limongram Server is running",
        "status": "online",
        "timestamp": now_iso(),
        "connections": len(manager.active_connections),
        "endpoints": {
            "GET /": "Server status",
            "GET /health": "Health check",
            "GET /stats": "Runtime statistics",
            "WS /ws/{username}": "WebSocket connection",
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": now_iso(),
        "connections": len(manager.active_connections),
    }


@app.get("/stats")
async def get_stats():
    return {
        "users_online": list(manager.active_connections.keys()),
        "total_connections": len(manager.active_connections),
        "public_keys": len(manager.user_public_keys),
        "timestamp": now_iso(),
    }


# ============================================================
# WEBSOCKET
# ============================================================

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    # FastAPI уже декодирует path parameter.
    username = username.strip()

    if not valid_username(username):
        await websocket.close(code=1008, reason="Invalid username")
        return

    # Принимаем соединение.
    connected = await manager.connect(websocket, username)
    if not connected:
        return

    heartbeat_task = asyncio.create_task(heartbeat(websocket, username))

    try:
        while True:
            # Получаем текст вручную, чтобы контролировать размер сообщения
            # до json.loads().
            raw = await websocket.receive_text()

            if len(raw.encode("utf-8")) > MAX_WEBSOCKET_TEXT:
                logger.warning("Oversized WebSocket message from %s", username)
                await websocket.close(code=1009, reason="Message too large")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to_user(
                    {
                        "type": "error",
                        "message": "Invalid JSON",
                    },
                    username,
                )
                continue

            if not isinstance(data, dict):
                await manager.send_to_user(
                    {
                        "type": "error",
                        "message": "Invalid message format",
                    },
                    username,
                )
                continue

            message_type = data.get("type")

            # Никогда не логируем payload: здесь могут быть ciphertext,
            # файлы или ключевые данные.
            logger.info("Message type from %s: %s", username, message_type)

            # ========================================================
            # PUBLIC KEY
            # ========================================================

            if message_type == "public_key":
                public_key = data.get("public_key")

                if not valid_public_key(public_key):
                    logger.warning("Invalid public key from %s", username)

                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid public key",
                        },
                        username,
                    )
                    continue

                now = asyncio.get_running_loop().time()
                previous = manager.last_key_update.get(username, 0.0)

                # Обычный reconnect сразу после открытия соединения должен
                # иметь возможность опубликовать ключ. Частые изменения
                # блокируем.
                if now - previous < KEY_CHANGE_COOLDOWN:
                    continue

                manager.last_key_update[username] = now
                manager.user_public_keys[username] = public_key

                logger.info("Public key updated for %s", username)

                # Только relay публичного ключа.
                for user in list(manager.active_connections.keys()):
                    if user != username:
                        await manager.send_to_user(
                            {
                                "type": "public_key",
                                "from": username,
                                "public_key": public_key,
                            },
                            user,
                        )

            # ========================================================
            # ENCRYPTED MESSAGE
            # ========================================================

            elif message_type == "encrypted":
                target = data.get("to")
                packet = data.get("data")

                if (
                    not isinstance(target, str)
                    or not valid_username(target)
                    or target == username
                ):
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid message recipient",
                        },
                        username,
                    )
                    continue

                if not valid_encrypted_packet(packet):
                    logger.warning(
                        "Invalid encrypted packet from %s to %s",
                        username,
                        target,
                    )
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid encrypted packet",
                        },
                        username,
                    )
                    continue

                success = await manager.send_to_user(
                    {
                        "type": "encrypted",
                        "from": username,
                        "data": packet,
                    },
                    target,
                )

                if success:
                    logger.info(
                        "Encrypted packet relayed: %s -> %s",
                        username,
                        target,
                    )
                else:
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "User is offline",
                        },
                        username,
                    )

            # ========================================================
            # CALL OFFER
            # ========================================================

            elif message_type == "call_offer":
                target = data.get("to")
                offer = data.get("offer")
                call_id = data.get("call_id")

                if (
                    not isinstance(target, str)
                    or not valid_username(target)
                    or target == username
                    or not isinstance(offer, dict)
                    or not isinstance(call_id, str)
                    or len(call_id) > 128
                ):
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid call offer",
                        },
                        username,
                    )
                    continue

                logger.info("Call offer: %s -> %s", username, target)

                await manager.send_to_user(
                    {
                        "type": "call_offer",
                        "caller": username,
                        "offer": offer,
                        "call_id": call_id,
                    },
                    target,
                )

            # ========================================================
            # CALL ANSWER
            # ========================================================

            elif message_type == "call_answer":
                target = data.get("to")
                answer = data.get("answer")
                call_id = data.get("call_id")

                if (
                    not isinstance(target, str)
                    or not valid_username(target)
                    or target == username
                    or not isinstance(answer, dict)
                    or not isinstance(call_id, str)
                    or len(call_id) > 128
                ):
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid call answer",
                        },
                        username,
                    )
                    continue

                logger.info("Call answer: %s -> %s", username, target)

                await manager.send_to_user(
                    {
                        "type": "call_answer",
                        "answer": answer,
                        "call_id": call_id,
                    },
                    target,
                )

            # ========================================================
            # ICE CANDIDATE
            # ========================================================

            elif message_type == "ice_candidate":
                target = data.get("to")
                candidate = data.get("candidate")
                call_id = data.get("call_id")

                if (
                    not isinstance(target, str)
                    or not valid_username(target)
                    or target == username
                    or not isinstance(candidate, dict)
                    or not isinstance(call_id, str)
                    or len(call_id) > 128
                ):
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid ICE candidate",
                        },
                        username,
                    )
                    continue

                await manager.send_to_user(
                    {
                        "type": "ice_candidate",
                        "candidate": candidate,
                        "call_id": call_id,
                    },
                    target,
                )

            # ========================================================
            # END CALL
            # ========================================================

            elif message_type == "end_call":
                target = data.get("to")
                call_id = data.get("call_id")

                if (
                    not isinstance(target, str)
                    or not valid_username(target)
                    or target == username
                    or not isinstance(call_id, str)
                    or len(call_id) > 128
                ):
                    await manager.send_to_user(
                        {
                            "type": "error",
                            "message": "Invalid end-call request",
                        },
                        username,
                    )
                    continue

                await manager.send_to_user(
                    {
                        "type": "end_call",
                        "call_id": call_id,
                    },
                    target,
                )

            # ========================================================
            # PING
            # ========================================================

            elif message_type == "ping":
                await manager.send_to_user(
                    {
                        "type": "pong",
                        "timestamp": now_iso(),
                    },
                    username,
                )

            # ========================================================
            # SERVER PING RESPONSE
            # ========================================================

            elif message_type == "pong":
                # Клиент может отвечать на heartbeat в будущих версиях.
                pass

            # ========================================================
            # OLD PLAINTEXT MESSAGE — REMOVED
            # ========================================================

            elif message_type == "message":
                # Старый plaintext-протокол намеренно больше не принимаем.
                # Это важно: после перехода на ECDH + AES-GCM сервер не
                # должен иметь запасной путь, который позволяет отправить
                # сообщение незашифрованным.
                await manager.send_to_user(
                    {
                        "type": "error",
                        "message": "Plaintext messages are disabled. Use encrypted messages.",
                    },
                    username,
                )

            # ========================================================
            # UNKNOWN
            # ========================================================

            else:
                await manager.send_to_user(
                    {
                        "type": "error",
                        "message": "Unknown message type",
                    },
                    username,
                )

    except WebSocketDisconnect:
        pass

    except Exception as exc:
        logger.exception("WebSocket error for %s: %s", username, exc)

    finally:
        heartbeat_task.cancel()

        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

        await manager.disconnect(websocket)


# ============================================================
# LOCAL / RENDER START
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = "0.0.0.0"

    print("🍋 Limongram Server starting...")
    print(f"🌐 HTTP: http://{host}:{port}")
    print(f"📡 WebSocket: ws://{host}:{port}/ws/{{username}}")
    print(f"🔍 Health: http://{host}:{port}/health")
    print(f"📊 Stats: http://{host}:{port}/stats")

    uvicorn.run(
        "server_fixed:app",
        host=host,
        port=port,
        reload=False,
    )
