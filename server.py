# server.py
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

# Импортируем наше шифрованное хранилище
from encrypted_storage import storage

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

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        self.connection_users[websocket] = username
        
        logger.info(f"User {username} connected")
        
        # Загружаем историю сообщений для пользователя
        try:
            history = storage.get_user_history(username)
            await self.send_to_user({
                "type": "history",
                "messages": history
            }, username)
        except Exception as e:
            logger.error(f"Error loading history for {username}: {e}")
        
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        username = self.connection_users.get(websocket)
        if username:
            del self.active_connections[username]
            del self.connection_users[websocket]
            logger.info(f"User {username} disconnected")
            asyncio.create_task(self.broadcast_user_list())

    async def send_to_user(self, message: dict, username: str):
        if username in self.active_connections:
            try:
                await self.active_connections[username].send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to {username}: {e}")

    async def broadcast(self, message: dict):
        for username, connection in self.active_connections.items():
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

manager = ConnectionManager()

# Эндпоинт для регистрации
@app.post("/register")
async def register(request: Request):
    try:
        data = await request.json()
        username = data.get("username")
        password = data.get("password")
        
        logger.info(f"Registration attempt for user: {username}")
        
        if not username or not password:
            return JSONResponse(
                {"success": False, "error": "Имя пользователя и пароль обязательны"}, 
                status_code=400
            )
        
        if len(username) < 3:
            return JSONResponse(
                {"success": False, "error": "Имя должно быть минимум 3 символа"}, 
                status_code=400
            )
        
        if len(password) < 4:
            return JSONResponse(
                {"success": False, "error": "Пароль должен быть минимум 4 символа"}, 
                status_code=400
            )
        
        # Регистрируем пользователя
        success = storage.register_user(username, password)
        
        if success:
            logger.info(f"User {username} registered successfully")
            return JSONResponse({"success": True, "message": "Регистрация успешна"})
        else:
            logger.warning(f"Registration failed for user {username}")
            return JSONResponse(
                {"success": False, "error": "Ошибка регистрации. Возможно пользователь уже существует"}, 
                status_code=400
            )
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return JSONResponse(
            {"success": False, "error": f"Ошибка сервера: {str(e)}"}, 
            status_code=500
        )

# Эндпоинт для входа
@app.post("/login")
async def login(request: Request):
    try:
        data = await request.json()
        username = data.get("username")
        password = data.get("password")
        
        logger.info(f"Login attempt for user: {username}")
        
        if not username or not password:
            return JSONResponse(
                {"success": False, "error": "Имя пользователя и пароль обязательны"}, 
                status_code=400
            )
        
        # Аутентифицируем пользователя
        success = storage.authenticate_user(username, password)
        
        if success:
            logger.info(f"User {username} logged in successfully")
            return JSONResponse({"success": True, "message": "Вход выполнен успешно"})
        else:
            logger.warning(f"Failed login attempt for user {username}")
            return JSONResponse(
                {"success": False, "error": "Неверное имя пользователя или пароль"}, 
                status_code=401
            )
    except Exception as e:
        logger.error(f"Login error: {e}")
        return JSONResponse(
            {"success": False, "error": f"Ошибка сервера: {str(e)}"}, 
            status_code=500
        )

# Эндпоинт для получения истории сообщений
@app.get("/history/{username}")
async def get_history(username: str, password: str):
    try:
        logger.info(f"History request for user: {username}")
        
        if storage.authenticate_user(username, password):
            history = storage.get_user_history(username)
            return JSONResponse({"history": history})
        
        return JSONResponse(
            {"error": "Аутентификация не удалась"}, 
            status_code=401
        )
    except Exception as e:
        logger.error(f"History error: {e}")
        return JSONResponse(
            {"error": f"Ошибка сервера: {str(e)}"}, 
            status_code=500
        )

# Корневой эндпоинт - отдаем HTML
@app.get("/")
async def get():
    try:
        # Пытаемся прочитать index.html
        if os.path.exists("index.html"):
            with open("index.html", "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)
        else:
            # Если файла нет, показываем информацию о сервере
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Limongram Server</title>
                <style>
                    body { font-family: Arial, sans-serif; margin: 40px; background: #f0f2f5; }
                    .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                    h1 { color: #128C7E; }
                    .status { padding: 10px; background: #d4edda; color: #155724; border-radius: 5px; margin: 20px 0; }
                    .info { color: #666; line-height: 1.6; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🍋 Limongram Server</h1>
                    <div class="status">✅ Сервер запущен и работает</div>
                    <p class="info">
                        <strong>API Endpoints:</strong><br>
                        - POST /register - регистрация пользователя<br>
                        - POST /login - вход в систему<br>
                        - GET /history/{username} - история сообщений<br>
                        - WS /ws/{username} - WebSocket соединение
                    </p>
                    <p class="info">
                        <strong>Файл index.html не найден.</strong><br>
                        Пожалуйста, создайте файл index.html в той же папке, что и server.py
                    </p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Ошибка</h1><p>{str(e)}</p>")

# WebSocket эндпоинт для обмена сообщениями
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    # Подключаем пользователя
    await manager.connect(websocket, username)
    
    try:
        while True:
            # Получаем сообщение от клиента
            data = await websocket.receive_json()
            logger.info(f"Received from {username}: {data.get('type')}")
            
            # Обрабатываем разные типы сообщений
            if data["type"] == "message":
                await handle_message(username, data)
                
            elif data["type"] == "call_offer":
                await handle_call_offer(username, data)
                
            elif data["type"] == "call_answer":
                await handle_call_answer(username, data)
                
            elif data["type"] == "ice_candidate":
                await handle_ice_candidate(username, data)
                
            elif data["type"] == "end_call":
                await handle_end_call(username, data)
                
            elif data["type"] == "typing":
                await handle_typing(username, data)
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(f"User {username} disconnected from WebSocket")
    except Exception as e:
        logger.error(f"WebSocket error for {username}: {e}")
        manager.disconnect(websocket)

# Обработчик текстовых сообщений
async def handle_message(username: str, data: dict):
    # Создаем сообщение для сохранения
    message_data = {
        "from": username,
        "to": data.get("to"),
        "content": data["content"],
        "type": "text",
        "timestamp": datetime.now().isoformat()
    }
    
    # Сохраняем в историю отправителя
    try:
        storage.save_message(username, message_data)
        logger.info(f"Message from {username} saved")
    except Exception as e:
        logger.error(f"Error saving message for {username}: {e}")
    
    # Если есть получатель, сохраняем и в его историю
    if data.get("to"):
        receiver = data["to"]
        
        # Сохраняем копию для получателя
        receiver_message = message_data.copy()
        receiver_message["type"] = "received"
        
        try:
            storage.save_message(receiver, receiver_message)
        except Exception as e:
            logger.error(f"Error saving message for receiver {receiver}: {e}")
        
        # Отправляем сообщение получателю
        await manager.send_to_user({
            "type": "message",
            "from": username,
            "content": data["content"],
            "timestamp": message_data["timestamp"]
        }, receiver)
        
        # Отправляем подтверждение отправителю
        await manager.send_to_user({
            "type": "message_delivered",
            "to": receiver,
            "timestamp": message_data["timestamp"]
        }, username)
    else:
        # Broadcast всем
        await manager.broadcast({
            "type": "message",
            "from": username,
            "content": data["content"],
            "timestamp": message_data["timestamp"]
        })

# Обработчик предложения звонка
async def handle_call_offer(username: str, data: dict):
    # Сохраняем информацию о звонке
    call_data = {
        "from": username,
        "to": data["to"],
        "type": "call_offer",
        "call_id": data.get("call_id"),
        "call_type": data.get("call_type", "video"),
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        storage.save_message(username, call_data)
    except Exception as e:
        logger.error(f"Error saving call offer: {e}")
    
    # Отправляем предложение получателю
    await manager.send_to_user({
        "type": "call_offer",
        "from": username,
        "offer": data["offer"],
        "call_id": data.get("call_id"),
        "call_type": data.get("call_type")
    }, data["to"])

# Обработчик ответа на звонок
async def handle_call_answer(username: str, data: dict):
    await manager.send_to_user({
        "type": "call_answer",
        "from": username,
        "answer": data["answer"],
        "call_id": data.get("call_id")
    }, data["to"])

# Обработчик ICE кандидатов
async def handle_ice_candidate(username: str, data: dict):
    await manager.send_to_user({
        "type": "ice_candidate",
        "from": username,
        "candidate": data["candidate"],
        "call_id": data.get("call_id")
    }, data["to"])

# Обработчик завершения звонка
async def handle_end_call(username: str, data: dict):
    # Сохраняем информацию о завершении звонка
    call_end_data = {
        "from": username,
        "to": data.get("to"),
        "type": "call_ended",
        "call_id": data.get("call_id"),
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        storage.save_message(username, call_end_data)
    except Exception as e:
        logger.error(f"Error saving call end: {e}")
    
    if data.get("to"):
        await manager.send_to_user({
            "type": "end_call",
            "from": username,
            "call_id": data.get("call_id")
        }, data["to"])

# Обработчик индикатора печатания
async def handle_typing(username: str, data: dict):
    if data.get("to"):
        await manager.send_to_user({
            "type": "typing",
            "from": username
        }, data["to"])

# Запуск сервера
if __name__ == "__main__":
    print("🍋 Limongram Server starting...")
    print("📁 Message history will be stored in 'message_history' folder")
    print("🌐 Open http://localhost:8000 in your browser")
    print("Press Ctrl+C to stop the server")
    
    # Создаем папку для истории если её нет
    if not os.path.exists("message_history"):
        os.makedirs("message_history")
        print("✅ Created message_history folder")
    
    # Запускаем сервер
    uvicorn.run(
        "server:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )
