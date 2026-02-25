from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from datetime import datetime

app = FastAPI()

# Разрешаем подключения из браузера
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
    
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"✅ {username} подключился")
        
        # Отправляем всем обновленный список пользователей
        await self.broadcast_user_list()
        
        # Отправляем приветственное сообщение новому пользователю
        await websocket.send_json({
            "type": "system",
            "message": f"Добро пожаловать в Limongram, {username}! 🍋",
            "users": list(self.active_connections.keys())
        })
    
    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]
            print(f"❌ {username} отключился")
    
    async def send_message(self, message: dict, username: str):
        if username in self.active_connections:
            await self.active_connections[username].send_json(message)
    
    async def broadcast_user_list(self):
        users = list(self.active_connections.keys())
        user_list_message = {
            "type": "user_list",
            "users": users,
            "timestamp": datetime.now().isoformat()
        }
        
        # Отправляем список всем подключенным пользователям
        for connection in self.active_connections.values():
            try:
                await connection.send_json(user_list_message)
            except:
                pass

manager = ConnectionManager()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    
    try:
        while True:
            # Получаем данные от клиента
            data = await websocket.receive_json()
            print(f"📨 {username}: {data}")
            
            # Обработка сообщений
            if "to" in data and "message" in data:
                # Отправляем сообщение указанному пользователю
                await manager.send_message({
                    "type": "message",
                    "from": username,
                    "message": data["message"],
                    "timestamp": datetime.now().isoformat()
                }, data["to"])
            
            # Обработка запроса списка пользователей
            elif data.get("type") == "get_users":
                await manager.send_message({
                    "type": "user_list",
                    "users": list(manager.active_connections.keys())
                }, username)
                
    except WebSocketDisconnect:
        manager.disconnect(username)
        await manager.broadcast_user_list()
    except Exception as e:
        print(f"Ошибка с пользователем {username}: {e}")
        manager.disconnect(username)
        await manager.broadcast_user_list()

@app.get("/")
def home():
    return {"message": "Limongram Server работает!", "online": len(manager.active_connections)}

if __name__ == "__main__":
    print("="*50)
    print("🍋 LIMONGRAM MESSENGER - СЕРВЕР ЗАПУЩЕН")
    print("="*50)
    print("Функции:")
    print("1. Отправка сообщений между пользователями")
    print("2. Автоматическое обновление списка онлайн пользователей")
    print("3. Уведомления о подключении/отключении")
    print("="*50)
    print(f"Сервер: http://localhost:8000")
    print("="*50)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)