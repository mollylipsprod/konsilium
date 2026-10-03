@echo off
cd /d "%~dp0"
start "" http://localhost:7860
node studio\server.mjs
