from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from typing import Dict

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatServer:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_keys: Dict[str, str] = {}
    
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"✅ {username} подключился")
        
        # Отправляем список пользователей всем
        await self.broadcast_user_list()
    
    async def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]
            if username in self.user_keys:
                del self.user_keys[username]
            print(f"❌ {username} отключился")
            await self.broadcast_user_list()
    
    async def broadcast_user_list(self):
        """Отправляем список пользователей ВСЕМ"""
        users = list(self.active_connections.keys())
        print(f"📢 Отправка списка пользователей: {users}")
        
        for username, connection in self.active_connections.items():
            try:
                await connection.send_json({
                    "type": "users",
                    "users": users
                })
            except:
                pass
    
    async def handle_key_exchange(self, from_user: str, key: str):
        """Обработка обмена ключами"""
        print(f"🔑 Получен ключ от {from_user}")
        self.user_keys[from_user] = key
        
        # Пересылаем ключ ВСЕМ остальным пользователям
        for username, connection in self.active_connections.items():
            if username != from_user:
                try:
                    await connection.send_json({
                        "type": "key_exchange",
                        "from": from_user,
                        "key": key
                    })
                    print(f"📨 Ключ {from_user} отправлен {username}")
                except:
                    pass
    
    async def handle_encrypted_message(self, from_user: str, to_user: str, data: dict):
        """Обработка зашифрованного сообщения"""
        if to_user in self.active_connections:
            await self.active_connections[to_user].send_json({
                "type": "encrypted",
                "from": from_user,
                "data": data
            })
            print(f"📨 Зашифрованное сообщение от {from_user} к {to_user}")

chat = ChatServer()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await chat.connect(websocket, username)
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📥 Получено от {username}: {data.get('type', 'unknown')}")
            
            if data.get("type") == "key_exchange":
                await chat.handle_key_exchange(username, data.get("key", ""))
            
            elif data.get("type") == "encrypted":
                await chat.handle_encrypted_message(
                    username,
                    data["to"],
                    data["data"]
                )
            
            elif data.get("type") == "get_users":
                # Отправляем список конкретному пользователю
                users = list(chat.active_connections.keys())
                await websocket.send_json({
                    "type": "users",
                    "users": users
                })
    
    except WebSocketDisconnect:
        await chat.disconnect(username)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await chat.disconnect(username)

@app.get("/")
def home():
    return {
        "message": "Limongram Cloud Server 🍋",
        "online": len(chat.active_connections),
        "users": list(chat.active_connections.keys())
    }

@app.get("/health")
def health():
    return {"status": "ok", "connections": len(chat.active_connections)}

if __name__ == "__main__":
    print("="*60)
    print("🍋 LIMONGRAM CLOUD SERVER - FIXED VERSION")
    print("="*60)
    print("✅ Обработка ключей шифрования включена")
    print("✅ Автоматическая рассылка списка пользователей")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)