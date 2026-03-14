# encrypted_storage.py
import os
import json
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from datetime import datetime
import hashlib

class EncryptedMessageStorage:
    def __init__(self, storage_dir="message_history"):
        self.storage_dir = storage_dir
        self.user_keys = {}  # Кэш ключей для каждого пользователя
        
        # Создаем папку для хранения если её нет
        if not os.path.exists(storage_dir):
            os.makedirs(storage_dir)
    
    def _get_user_salt(self, username):
        """Получаем или создаем соль для пользователя"""
        salt_file = os.path.join(self.storage_dir, f"{username}.salt")
        
        if os.path.exists(salt_file):
            with open(salt_file, 'rb') as f:
                return f.read()
        else:
            # Создаем новую соль
            salt = os.urandom(16)
            with open(salt_file, 'wb') as f:
                f.write(salt)
            return salt
    
    def _derive_key_from_password(self, password, salt):
        """Создаем ключ шифрования из пароля и соли"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def register_user(self, username, password):
        """Регистрируем нового пользователя и создаем для него ключ"""
        salt = self._get_user_salt(username)
        key = self._derive_key_from_password(password, salt)
        self.user_keys[username] = Fernet(key)
        
        # Создаем файл для истории пользователя
        user_file = os.path.join(self.storage_dir, f"{username}.history")
        if not os.path.exists(user_file):
            with open(user_file, 'wb') as f:
                # Инициализируем пустой зашифрованный файл
                encrypted_empty = self.user_keys[username].encrypt(json.dumps([]).encode())
                f.write(encrypted_empty)
        
        return True
    
    def authenticate_user(self, username, password):
        """Проверяем пароль и получаем доступ к ключу"""
        try:
            salt = self._get_user_salt(username)
            key = self._derive_key_from_password(password, salt)
            fernet = Fernet(key)
            
            # Проверяем, можем ли мы расшифровать историю
            user_file = os.path.join(self.storage_dir, f"{username}.history")
            if os.path.exists(user_file):
                with open(user_file, 'rb') as f:
                    encrypted_data = f.read()
                    try:
                        fernet.decrypt(encrypted_data)
                        self.user_keys[username] = fernet
                        return True
                    except:
                        return False
            return False
        except:
            return False
    
    def save_message(self, username, message_data):
        """Сохраняем зашифрованное сообщение в историю пользователя"""
        if username not in self.user_keys:
            raise Exception("Пользователь не аутентифицирован")
        
        fernet = self.user_keys[username]
        user_file = os.path.join(self.storage_dir, f"{username}.history")
        
        # Читаем существующую историю
        if os.path.exists(user_file):
            with open(user_file, 'rb') as f:
                encrypted_data = f.read()
                decrypted_data = fernet.decrypt(encrypted_data)
                history = json.loads(decrypted_data.decode())
        else:
            history = []
        
        # Добавляем новое сообщение
        message_data['timestamp'] = datetime.now().isoformat()
        history.append(message_data)
        
        # Ограничиваем историю последними 1000 сообщениями
        if len(history) > 1000:
            history = history[-1000:]
        
        # Сохраняем зашифрованную историю
        encrypted_history = fernet.encrypt(json.dumps(history, ensure_ascii=False).encode())
        with open(user_file, 'wb') as f:
            f.write(encrypted_history)
        
        return True
    
    def get_user_history(self, username):
        """Получаем расшифрованную историю пользователя"""
        if username not in self.user_keys:
            raise Exception("Пользователь не аутентифицирован")
        
        fernet = self.user_keys[username]
        user_file = os.path.join(self.storage_dir, f"{username}.history")
        
        if not os.path.exists(user_file):
            return []
        
        with open(user_file, 'rb') as f:
            encrypted_data = f.read()
            decrypted_data = fernet.decrypt(encrypted_data)
            history = json.loads(decrypted_data.decode())
        
        return history
    
    def save_chat_history(self, user1, user2, messages):
        """Сохраняем историю чата между двумя пользователями (в обе стороны)"""
        chat_id = hashlib.md5(f"{min(user1, user2)}_{max(user1, user2)}".encode()).hexdigest()
        chat_file = os.path.join(self.storage_dir, f"chat_{chat_id}.enc")
        
        # Используем общий ключ для чата (на основе паролей обоих пользователей)
        # В реальном проекте здесь должен быть более сложный механизм
        chat_key = Fernet.generate_key()
        fernet = Fernet(chat_key)
        
        # Сохраняем ключ чата в зашифрованном виде для каждого участника
        for user in [user1, user2]:
            if user in self.user_keys:
                user_fernet = self.user_keys[user]
                encrypted_key = user_fernet.encrypt(chat_key)
                key_file = os.path.join(self.storage_dir, f"{user}_chat_{chat_id}.key")
                with open(key_file, 'wb') as f:
                    f.write(encrypted_key)
        
        # Сохраняем сообщения чата
        encrypted_messages = fernet.encrypt(json.dumps(messages, ensure_ascii=False).encode())
        with open(chat_file, 'wb') as f:
            f.write(encrypted_messages)
    
    def get_chat_history(self, username, chat_partner):
        """Получаем историю чата между пользователями"""
        chat_id = hashlib.md5(f"{min(username, chat_partner)}_{max(username, chat_partner)}".encode()).hexdigest()
        key_file = os.path.join(self.storage_dir, f"{username}_chat_{chat_id}.key")
        chat_file = os.path.join(self.storage_dir, f"chat_{chat_id}.enc")
        
        if not os.path.exists(key_file) or not os.path.exists(chat_file):
            return []
        
        # Получаем ключ чата
        with open(key_file, 'rb') as f:
            encrypted_key = f.read()
        
        if username in self.user_keys:
            user_fernet = self.user_keys[username]
            chat_key = user_fernet.decrypt(encrypted_key)
            chat_fernet = Fernet(chat_key)
            
            # Расшифровываем сообщения
            with open(chat_file, 'rb') as f:
                encrypted_messages = f.read()
                messages = json.loads(chat_fernet.decrypt(encrypted_messages).decode())
            
            return messages
        
        return []

# Глобальный экземпляр хранилища
storage = EncryptedMessageStorage()
