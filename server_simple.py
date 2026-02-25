from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Храним подключения
connections = {}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    connections[username] = websocket
    print(f"✅ {username} подключился")
    
    # Отправляем приветствие
    await websocket.send_json({
        "type": "system",
        "message": f"Привет, {username}!",
        "users": list(connections.keys())
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 {username}: {data}")
            
            # Если есть "to", отправляем сообщение
            if "to" in data and data["to"] in connections:
                await connections[data["to"]].send_json({
                    "type": "message",
                    "from": username,
                    "message": data.get("message", ""),
                    "users": list(connections.keys())
                })
                
    except Exception as e:
        print(f"❌ {username} отключился: {e}")
        if username in connections:
            del connections[username]

@app.get("/")
def home():
    return {"message": "Limongram работает!", "online": len(connections)}

if __name__ == "__main__":
    print("="*50)
    print("🍋 LIMONGRAM - ПРОСТОЙ СЕРВЕР")
    print("="*50)
    print("Откройте index.html в браузере")
    print("Сервер: http://localhost:8000")
    print("="*50)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)