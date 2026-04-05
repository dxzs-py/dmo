@echo off
chcp 65001 >nul
echo ========================================
echo Django_xm 后端一键启动
echo ========================================
echo.

:: 检查 conda 是否可用
where conda >nul 2>&1
if errorlevel 1 (
    echo [错误] conda 命令未找到！请先安装 Anaconda/Miniconda 并配置环境变量。
    pause
    exit /b 1
)

:: 切换到项目目录
cd /d "%~dp0"

:: 激活 conda 环境（先检查一次环境是否存在）
echo [1/4] 激活 conda 环境 Django_xm...
call conda activate Django_xm 2>&1 >nul
if errorlevel 1 (
    echo [错误] conda 环境 Django_xm 不存在！
    echo   请创建环境或检查项目规则文档。
    pause
    exit /b 1
)
echo   ✓ conda 环境激活成功
echo.

:: 检查并启动 Redis
echo [2/4] 检查 Redis 连接...
python -c "import redis; r=redis.Redis(host='127.0.0.1', port=6379, socket_timeout=3); r.ping(); print('  ✓ Redis 连接成功')" 2>&1 >nul
if errorlevel 1 (
    echo   [信息] Redis 未运行，正在启动...
    if exist "D:\Reids\redis-server.exe" (
        start "Redis Server" cmd /k "cd /d D:\Reids & redis-server.exe redis.windows.conf"
        echo   ✓ Redis 已启动
        timeout /t 3 >nul
    ) else (
        echo   [警告] 未找到 Redis：D:\Reids\redis-server.exe
        echo     Celery 队列任务无法处理，但 Django 仍可启动
    )
) else (
    echo   ✓ Redis 连接成功
)
echo.

:: 启动三个独立终端
echo [3/4] 启动服务...
echo   • 终端 1: Django Server (端口 8000)
echo   • 终端 2: Celery Worker
echo.

start "Django Server - Django_xm" cmd /k "cd /d %~dp0 & call conda activate Django_xm & python manage.py runserver"
timeout /t 2 >nul
start "Celery Worker - Django_xm" cmd /k "cd /d %~dp0 & call conda activate Django_xm & celery -A Django_xm worker -l info -Q celery -P solo"

echo.
echo ========================================
echo ✓ 启动完成！
echo ========================================
echo.
echo [提示] 请不要关闭这三个窗口
echo       按任意键可以关闭此启动窗口...
pause >nul
