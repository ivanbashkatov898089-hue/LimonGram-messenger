# encrypted_storage.py
import os
import json
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from datetime import datetime

class EncryptedMessageStorage:
    def __init__(self, storage_dir="message_history"):
        self.storage_dir = storage_dir
        self.user_keys = {}
        
        if not os.path.exists(storage_dir):
            os.makedirs(storage_dir)
    
    def _get_user_salt(self, username):
        salt_file = os.path.join(self.storage_dir, f"{username}.salt")
        
        if os.path.exists(salt_file):
            with open(salt_file, 'rb') as f:
                return f.read()
        else:
            salt = os.urandom(16)
            with open(salt_file, 'wb') as f:
                f.write(salt)
            return salt
    
    def _derive_key_from_password(self, password, salt):
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def register_user(self, username, password):
        try:
            salt = self._get_user_salt(username)
            key = self._derive_key_from_password(password, salt)
            self.user_keys[username] = Fernet(key)
            
            user_file = os.path.join(self.storage_dir, f"{username}.history")
            if not os.path.exists(user_file):
                encrypted_empty = self.user_keys[username].encrypt(json.dumps([]).encode())
                with open(user_file, 'wb') as f:
                    f.write(encrypted_empty)
            
            return True
        except Exception as e:
            print(f"Registration error: {e}")
            return False
    
    def authenticate_user(self, username, password):
        try:
            salt = self._get_user_salt(username)
            key = self._derive_key_from_password(password, salt)
            fernet = Fernet(key)
            
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
        if username not in self.user_keys:
            return False
        
        fernet = self.user_keys[username]
        user_file = os.path.join(self.storage_dir, f"{username}.history")
        
        if os.path.exists(user_file):
            with open(user_file, 'rb') as f:
                encrypted_data = f.read()
                decrypted_data = fernet.decrypt(encrypted_data)
                history = json.loads(decrypted_data.decode())
        else:
            history = []
        
        message_data['timestamp'] = datetime.now().isoformat()
        history.append(message_data)
        
        if len(history) > 1000:
            history = history[-1000:]
        
        encrypted_history = fernet.encrypt(json.dumps(history, ensure_ascii=False).encode())
        with open(user_file, 'wb') as f:
            f.write(encrypted_history)
        
        return True
    
    def get_user_history(self, username):
        if username not in self.user_keys:
            return []
        
        fernet = self.user_keys[username]
        user_file = os.path.join(self.storage_dir, f"{username}.history")
        
        if not os.path.exists(user_file):
            return []
        
        with open(user_file, 'rb') as f:
            encrypted_data = f.read()
            decrypted_data = fernet.decrypt(encrypted_data)
            history = json.loads(decrypted_data.decode())
        
        return history

storage = EncryptedMessageStorage()
