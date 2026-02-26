cat > server_fixed.py << 'EOF'
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
        
        # Отправляем новому пользователю список всех
        await self.send_user_list(username)
        
        # Отправляем всем остальным, что новый пользователь подключился
        await self.broadcast_new_user(username)
    
    async def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]
            if username in self.user_keys:
                del self.user_keys[username]
            print(f"❌ {username} отключился")
            await self.broadcast_user_list()
    
    async def broadcast_user_list(self):
        """Отправляем список ВСЕМ пользователям"""
        users = list(self.active_connections.keys())
        for connection in self.active_connections.values():
            try:
                await connection.send_json({
                    "type": "users",
                    "users": users
                })
            except:
                pass
    
    async def send_user_list(self, to_username: str):
        """Отправляем список конкретному пользователю"""
        if to_username in self.active_connections:
            users = list(self.active_connections.keys())
            await self.active_connections[to_username].send_json({
                "type": "users",
                "users": users
            })
    
    async def broadcast_new_user(self, new_user: str):
        """Оповещаем всех о новом пользователе"""
        users = list(self.active_connections.keys())
        for username, connection in self.active_connections.items():
            if username != new_user:
                try:
                    await connection.send_json({
                        "type": "users",
                        "users": users
                    })
                except:
                    pass
    
    async def broadcast_key(self, from_user: str, key: str):
        """Отправляем ключ ВСЕМ пользователям"""
        print(f"🔑 Рассылаю ключ от {from_user} всем")
        for username, connection in self.active_connections.items():
            if username != from_user:
                try:
                    await connection.send_json({
                        "type": "key_exchange",
                        "from": from_user,
                        "key": key
                    })
                    print(f"   → отправлено {username}")
                except Exception as e:
                    print(f"   ❌ ошибка отправки {username}: {e}")
    
    async def forward_encrypted(self, from_user: str, to_user: str, data: dict):
        """Пересылаем зашифрованное сообщение"""
        if to_user in self.active_connections:
            await self.active_connections[to_user].send_json({
                "type": "encrypted",
                "from": from_user,
                "data": data
            })
            print(f"📨 Сообщение от {from_user} к {to_user}")

chat = ChatServer()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await chat.connect(websocket, username)
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📥 Получено от {username}: {data.get('type', 'unknown')}")
            
            if data.get("type") == "key_exchange":
                # Сохраняем ключ
                chat.user_keys[username] = data.get("key", "")
                # Рассылаем ключ всем
                await chat.broadcast_key(username, data.get("key", ""))
            
            elif data.get("type") == "encrypted":
                await chat.forward_encrypted(
                    username,
                    data["to"],
                    data["data"]
                )
    
    except WebSocketDisconnect:
        await chat.disconnect(username)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await chat.disconnect(username)

@app.get("/")
def home():
    return {
        "message": "Limongram Server",
        "online": len(chat.active_connections),
        "users": list(chat.active_connections.keys())
    }

@app.get("/health")
def health():
    return {"status": "ok", "connections": len(chat.active_connections)}

if __name__ == "__main__":
    print("="*60)
    print("🍋 LIMONGRAM SERVER FIXED")
    print("="*60)
    print("✅ Рассылка ключей всем пользователям")
    print("✅ Автоматическое обновление списков")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
EOF

# Перезапустим сервер
echo "Сервер обновлен! Перезапустите его вручную на Render.com"
