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

class SecureMessenger:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"✅ {username} подключился")
        await self.broadcast_users()
    
    async def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]
            print(f"❌ {username} отключился")
            await self.broadcast_users()
    
    async def broadcast_users(self):
        users = list(self.active_connections.keys())
        for ws in self.active_connections.values():
            try:
                await ws.send_json({
                    "type": "users",
                    "users": users
                })
            except:
                pass
    
    async def forward_encrypted(self, from_user: str, to_user: str, encrypted_data: dict):
        """Пересылаем зашифрованные данные"""
        if to_user in self.active_connections:
            await self.active_connections[to_user].send_json({
                "type": "encrypted",
                "from": from_user,
                "data": encrypted_data
            })
            print(f"📨 Зашифрованное сообщение от {from_user} к {to_user}")

messenger = SecureMessenger()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await messenger.connect(websocket, username)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "encrypted":
                await messenger.forward_encrypted(
                    username,
                    data["to"],
                    data["data"]
                )
    
    except WebSocketDisconnect:
        await messenger.disconnect(username)

@app.get("/")
def home():
    return {"message": "Limongram Secure Server"}

if __name__ == "__main__":
    print("="*60)
    print("🔐 LIMONGRAM SECURE SERVER")
    print("="*60)
    print("Сервер: http://localhost:8000")
    print("Все сообщения шифруются!")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)