@echo off
chcp 65001 >nul
title MC 联机助手
set PYTHONUTF8=1
set "TOOL=%~dp0"
if not exist "%TOOL%mcmesh_gui.py" set "TOOL=%USERPROFILE%\MCMesh\"
if not exist "%TOOL%mcmesh_gui.py" set "TOOL=%USERPROFILE%\Desktop\MCMesh\"
if not exist "%TOOL%mcmesh_gui.py" (
    echo.
    echo [X] 找不到 mcmesh_gui.py —— 把这个 bat 放回 MCMesh 文件夹
    echo.
    pause
    exit /b 1
)
cd /d "%TOOL%"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw mcmesh_gui.py
    exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (set "PYCMD=py -3") else (set "PYCMD=python")
%PYCMD% mcmesh_gui.py
echo.
pause
