@echo off
chcp 65001 > nul
title R5000  Layer1 监控面板
color 0B

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   R5000  Layer1  环境监控仪表盘            ║
echo  ╚══════════════════════════════════════════╝
echo.
echo  启动后请在浏览器打开:
echo  http://localhost:8501
echo.

cd /d "D:\MyAI\My work team\R5000"

"C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" -m streamlit run src/layers/layer1_frontend.py --server.port 8501

echo.
pause > nul
