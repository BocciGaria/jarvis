@echo off
chcp 65001 > nul
cd /d "%~dp0"
title Jarvis Server
".venv\Scripts\python.exe" -m jarvis.gui
