@echo off
chcp 65001 >nul
title MCMesh - 命令行菜单
set PYTHONUTF8=1
set "TOOL=%~dp0"
if not exist "%TOOL%mcmesh.py" set "TOOL=%USERPROFILE%\MCMesh\"
if not exist "%TOOL%mcmesh.py" set "TOOL=%USERPROFILE%\Desktop\MCMesh\"
if not exist "%TOOL%mcmesh.py" (
    echo.
    echo [X] 找不到 mcmesh.py —— 把这个 bat 放回 MCMesh 文件夹，或者把 MCMesh 文件夹放到 C:\Users\%USERNAME%\
    echo.
    pause
    exit /b 1
)
cd /d "%TOOL%"
where py >nul 2>nul
if %errorlevel%==0 (set "PYCMD=py -3") else (set "PYCMD=python")
%PYCMD% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo [X] 没找到 Python。先装一个：https://www.python.org/downloads/
    echo     安装时记得勾选 "Add python.exe to PATH"，装完重开这个窗口。
    echo.
    pause
    exit /b 1
)
%PYCMD% mcmesh.py %*
if "%~1"=="" pause
