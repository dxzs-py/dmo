@echo off
title Celery Worker - Django_xm
chcp 65001 >nul
echo ========================================
echo 启动 Celery Worker
echo ========================================
echo.

:: 激活 conda 环境
call conda activate Django_xm
if errorlevel 1 (
    echo [错误] 激活 conda 环境 Django_xm 失败！
    pause
    exit /b 1
)

:: 切换到项目目录
cd /d "%~dp0"

:: 启动 Celery Worker
echo [提示] 正在启动 Celery Worker...
echo        Windows: 使用 -P solo 进程池
echo        队列: celery
echo.
celery -A Django_xm worker -l info -Q celery -P solo

pause
