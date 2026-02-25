from cryptography.fernet import Fernet

print("="*50)
print("🔐 ТЕСТ КРИПТОГРАФИИ")
print("="*50)

# Генерируем ключ
print("1. Генерируем ключ...")
key = Fernet.generate_key()
print(f"✅ Ключ создан: {key.decode()[:20]}...")

# Создаем шифровальщик
cipher = Fernet(key)

# Исходное сообщение (используем обычную строку, не байты)
message = "Привет, Limongram с шифрованием!"
print(f"2. Исходное сообщение: {message}")

# Шифруем (преобразуем строку в байты)
print("3. Шифруем...")
message_bytes = message.encode('utf-8')
encrypted = cipher.encrypt(message_bytes)
print(f"✅ Зашифровано: {encrypted[:30]}...")

# Расшифровываем
print("4. Расшифровываем...")
decrypted_bytes = cipher.decrypt(encrypted)
decrypted = decrypted_bytes.decode('utf-8')
print(f"✅ Расшифровано: {decrypted}")

# Проверяем
print("5. Проверка...")
if message == decrypted:
    print("✅ УСПЕХ! Сообщения совпадают!")
else:
    print("❌ Ошибка: сообщения не совпадают")

print("="*50)
print("🎉 Библиотека cryptography работает правильно!")
print("="*50)

# Дополнительная информация
print(f"\nВерсия cryptography: {cryptography.__version__}")