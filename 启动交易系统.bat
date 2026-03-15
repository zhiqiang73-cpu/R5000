@echo off
chcp 65001 > nul
title R5000  BTCUSDT  模拟盘 [运行中]
color 0A

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   R5000  BTCUSDT  模拟盘交易系统          ║
echo  ║   Binance Testnet   U本位合约             ║
echo  ╚══════════════════════════════════════════╝
echo.
echo  日志同步写入 logs\live_YYYYMMDD.log
echo  按 Ctrl+C 安全退出
echo.

cd /d "D:\MyAI\My work team\R5000"

echo  [1/3] 启动 WebSocket 采集器（liquidations + trades + depth）...
start "R5000 WS采集" /min "C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" -m src.data.websocket --streams liquidations,trades,depth

echo  [2/3] 启动 REST 采集器（资金费率 + Open Interest）...
start "R5000 REST采集" /min "C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" -m src.data.rest_collector

echo  数据采集已在后台启动，写入 data\raw\
echo.

echo  [3/3] 启动模拟盘交易系统...
"C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" -m src.live_trader

echo.
echo  ══════════════════════════════════════════
echo    程序已退出（按任意键关闭窗口）
echo  ══════════════════════════════════════════
pause > nul
