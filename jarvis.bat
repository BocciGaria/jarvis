@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem サーバーはコンソールなしで裏で動き、Jarvis の画面を閉じると終了する（出力は data\logs\gui.log）
start "" ".venv\Scripts\pythonw.exe" -m jarvis.gui
