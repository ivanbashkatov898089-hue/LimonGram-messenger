from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
import uvicorn
import json
import os
from typing import Dict, Optional
import secrets

# ============ НАСТРОЙКИ ============
app = FastAPI(title="Limongram with Accounts")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB подключение
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://limongram_user:ВАШ_ПАРОЛЬ@cluster0.xxxxx.mongodb.net/limongram")
client = MongoClient(MONGODB_URL)
db = client["limongram"]
users_collection = db["users"]
messages_collection = db["messages"]

# Индексы для быстрого поиска
users_collection.create_index("username", unique=True)
messages_collection.create_index([("from_user", 1), ("to_user", 1), ("timestamp", -1)])

# Хеширование паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT настройки
SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# ============ МОДЕЛИ ДАННЫХ ============
class User:
    def __init__(self, username: str, password: str, public_key: str = ""):
        self.username = username
        self.password_hash = pwd_context.hash(password)
        self.public_key = public_key
        self.created_at = datetime.now()
        self.last_seen = datetime.now()
        self.online = False
    
    def to_dict(self):
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "public_key": self.public_key,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "online": self.online
        }
    
    @staticmethod
    def from_dict(data):
        return data

# ============ ФУНКЦИИ АУТЕНТИФИКАЦИИ ============
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_user_by_username(username: str):
    return users_collection.find_one({"username": username})

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ============ ВЕБ-СОКЕТ СОЕДИНЕНИЯ ============
class ChatServer:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_keys: Dict[str, str] = {}
    
    async def authenticate_user(self, websocket: WebSocket, username: str, token: str) -> bool:
        """Проверяем JWT токен"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("sub") != username:
                return False
            return True
        except JWTError:
            return False
    
    async def connect(self, websocket: WebSocket, username: str, token: str):
        await websocket.accept()
        
        # Проверяем авторизацию
        if not await self.authenticate_user(websocket, username, token):
            await websocket.send_json({"type": "error", "message": "Неверный токен"})
            await websocket.close()
            return
        
        # Обновляем статус в БД
        users_collection.update_one(
            {"username": username},
            {"$set": {"online": True, "last_seen": datetime.now()}}
        )
        
        self.active_connections[username] = websocket
        print(f"✅ {username} подключился")
        
        # Отправляем историю сообщений
        await self.send_message_history(username)
        
        # Оповещаем всех
        await self.broadcast_user_list()
    
    async def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]
            
            # Обновляем статус в БД
            users_collection.update_one(
                {"username": username},
                {"$set": {"online": False, "last_seen": datetime.now()}}
            )
            
            print(f"❌ {username} отключился")
            await self.broadcast_user_list()
    
    async def send_message_history(self, username: str):
        """Отправляем историю сообщений пользователю"""
        history = list(messages_collection.find({
            "$or": [
                {"from_user": username},
                {"to_user": username}
            ]
        }).sort("timestamp", -1).limit(50))
        
        if history and username in self.active_connections:
            # Конвертируем ObjectId в строку
            for msg in history:
                msg["_id"] = str(msg["_id"])
            
            await self.active_connections[username].send_json({
                "type": "message_history",
                "messages": history
            })
    
    async def save_message(self, from_user: str, to_user: str, encrypted_data: dict):
        """Сохраняем сообщение в БД"""
        message = {
            "from_user": from_user,
            "to_user": to_user,
            "encrypted_data": encrypted_data,
            "timestamp": datetime.now(),
            "delivered": False
        }
        messages_collection.insert_one(message)
    
    async def broadcast_user_list(self):
        """Отправляем список пользователей всем"""
        users = list(self.active_connections.keys())
        users_info = []
        
        for username in users:
            user_data = users_collection.find_one({"username": username})
            users_info.append({
                "username": username,
                "online": True,
                "last_seen": user_data.get("last_seen") if user_data else None
            })
        
        for connection in self.active_connections.values():
            try:
                await connection.send_json({
                    "type": "users",
                    "users": users_info
                })
            except:
                pass
    
    async def broadcast_key(self, from_user: str, key: str):
        """Рассылаем ключ всем"""
        for username, connection in self.active_connections.items():
            if username != from_user:
                try:
                    await connection.send_json({
                        "type": "key_exchange",
                        "from": from_user,
                        "key": key
                    })
                except:
                    pass
    
    async def forward_encrypted(self, from_user: str, to_user: str, data: dict):
        """Пересылаем сообщение и сохраняем"""
        # Сохраняем в БД
        await self.save_message(from_user, to_user, data)
        
        # Отправляем если получатель онлайн
        if to_user in self.active_connections:
            await self.active_connections[to_user].send_json({
                "type": "encrypted",
                "from": from_user,
                "data": data
            })
            
            # Отмечаем как доставленное
            messages_collection.update_many(
                {"from_user": from_user, "to_user": to_user, "delivered": False},
                {"$set": {"delivered": True}}
            )

chat = ChatServer()

# ============ HTTP ENDPOINTS ============

@app.post("/register")
async def register(username: str, password: str):
    """Регистрация нового пользователя"""
    print(f"📝 Попытка регистрации: {username}")
    
    # Проверяем длину
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Имя слишком короткое")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль слишком короткий")
    
    # Проверяем уникальность
    if users_collection.find_one({"username": username}):
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    
    # Создаем пользователя
    user = User(username, password)
    users_collection.insert_one(user.to_dict())
    print(f"✅ Пользователь {username} зарегистрирован")
    
    # Создаем токен
    access_token = create_access_token({"sub": username})
    
    return {
        "success": True,
        "message": "Регистрация успешна",
        "access_token": access_token,
        "username": username
    }

@app.post("/login")
async def login(username: str, password: str):
    """Вход в систему"""
    
    user = users_collection.find_one({"username": username})
    if not user:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Неверный пароль")
    
    # Создаем токен
    access_token = create_access_token({"sub": username})
    
    return {
        "success": True,
        "message": "Вход выполнен",
        "access_token": access_token,
        "username": username
    }

@app.get("/users")
async def get_users():
    """Получить список всех пользователей"""
    users = list(users_collection.find({}, {"username": 1, "online": 1, "last_seen": 1}))
    for user in users:
        user["_id"] = str(user["_id"])
    return users

@app.get("/messages/{username}")
async def get_user_messages(username: str, token: str):
    """Получить историю сообщений с пользователем"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        current_user = payload.get("sub")
        
        if not current_user:
            raise HTTPException(status_code=401, detail="Не авторизован")
        
        messages = list(messages_collection.find({
            "$or": [
                {"from_user": current_user, "to_user": username},
                {"from_user": username, "to_user": current_user}
            ]
        }).sort("timestamp", -1).limit(100))
        
        for msg in messages:
            msg["_id"] = str(msg["_id"])
        
        return messages
        
    except JWTError:
        raise HTTPException(status_code=401, detail="Неверный токен")

# ============ WEBSOCKET ENDPOINT ============
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str, token: str):
    await chat.connect(websocket, username, token)
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📥 Получено от {username}: {data.get('type', 'unknown')}")
            
            if data.get("type") == "key_exchange":
                # Сохраняем ключ
                users_collection.update_one(
                    {"username": username},
                    {"$set": {"public_key": data.get("key", "")}}
                )
                # Рассылаем всем
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
        "message": "Limongram with Accounts",
        "version": "2.0",
        "features": [
            "Регистрация и вход",
            "Сохранение сообщений",
            "История переписок",
            "Шифрование end-to-end"
        ]
    }

if __name__ == "__main__":
    print("="*60)
    print("👤 LIMONGRAM")
    print("="*60)
    print("✅ MongoDB подключена")
    print("✅ Система регистрации готова")
    print("✅ Сообщения сохраняются")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)

