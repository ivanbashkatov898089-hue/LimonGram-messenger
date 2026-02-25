@echo off
echo ========================================
echo    LIMONGRAM MESSENGER - АВТОЗАПУСК
echo ========================================
echo.
echo Шаг 1: Проверяю установленные пакеты...
python -m pip install fastapi uvicorn websockets --quiet
echo.
echo Шаг 2: Запускаю сервер мессенджера...
echo.
echo СЕРВЕР ЗАПУЩЕН! Не закрывайте это окно.
echo.
echo Теперь откройте файл index.html в браузере.
echo.
echo ========================================
python server.py
pause