#!/usr/bin/env python3
"""
WebSocket数据采集命令行接口
可以通过python -m src.data.websocket运行
"""

import asyncio
import sys
from pathlib import Path

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.websocket import main

if __name__ == "__main__":
    main()