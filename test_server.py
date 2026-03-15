# test_server.py
import requests
import json
from websocket import create_connection
import time

def test_http():
    """Тестирование HTTP endpoints"""
    print("🔍 Testing HTTP endpoints...")
    
    # Тест корневого эндпоинта
    response = requests.get("http://localhost:8000/")
    print(f"✅ Root endpoint: {response.status_code}")
    print(f"   Response: {response.json()}")
    
    # Тест health check
    response = requests.get("http://localhost:8000/health")
    print(f"✅ Health check: {response.status_code}")
    print(f"   Response: {response.json()}")
    
    return True

def test_websocket():
    """Тестирование WebSocket соединения"""
    print("\n🔌 Testing WebSocket connection...")
    
    try:
        # Подключаемся к WebSocket
        ws = create_connection("ws://localhost:8000/ws/test_user")
        print("✅ WebSocket connected")
        
        # Отправляем сообщение
        ws.send(json.dumps({
            "type": "ping",
            "timestamp": time.time()
        }))
        print("✅ Ping sent")
        
        # Получаем ответ
        result = ws.recv()
        print(f"✅ Received: {result}")
        
        ws.close()
        print("✅ WebSocket closed")
        return True
        
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Testing Limongram Server\n")
    
    # Сначала проверяем HTTP
    if test_http():
        print("\n✅ HTTP tests passed")
    else:
        print("\n❌ HTTP tests failed")
    
    # Затем WebSocket
    if test_websocket():
        print("\n✅ WebSocket tests passed")
    else:
        print("\n❌ WebSocket tests failed")
    
    print("\n✨ Server is ready for use!")
