"""
REST 数据定期采集器
每15分钟存一次资金费率，每5分钟存一次 Open Interest
写入 data/raw/funding_rate/YYYY-MM-DD.jsonl
       data/raw/open_interest/YYYY-MM-DD.jsonl
"""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com"
DATA_ROOT = Path("data/raw")
SYMBOL = "BTCUSDT"

# 采集间隔（秒）
FR_INTERVAL = 15 * 60   # 资金费率：15分钟
OI_INTERVAL = 5 * 60    # Open Interest：5分钟

_running = True


def _write_jsonl(subdir: str, record: dict) -> None:
    """写一条记录到当天的 JSONL 文件"""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DATA_ROOT / subdir / f"{date_str}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _get(endpoint: str, params: dict) -> dict | list | None:
    """带超时和重试的 REST 请求"""
    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL + endpoint, params=params, timeout=8)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == 2:
                logger.warning(f"REST请求失败 {endpoint}: {exc}")
                return None
            time.sleep(2 ** attempt)


def collect_funding_rate() -> None:
    """采集当前资金费率"""
    data = _get("/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    if not data:
        return
    record = {
        "ts": int(time.time() * 1000),
        "symbol": SYMBOL,
        "funding_rate": str(Decimal(str(data.get("lastFundingRate", "0")))),
        "next_funding_time": data.get("nextFundingTime"),
        "mark_price": str(data.get("markPrice", "0")),
    }
    _write_jsonl("funding_rate", record)
    logger.info(f"[FR] {record['funding_rate']}")


def collect_open_interest() -> None:
    """采集当前 Open Interest"""
    data = _get("/fapi/v1/openInterest", {"symbol": SYMBOL})
    if not data:
        return
    record = {
        "ts": int(time.time() * 1000),
        "symbol": SYMBOL,
        "open_interest": str(data.get("openInterest", "0")),
    }
    _write_jsonl("open_interest", record)
    logger.info(f"[OI] {record['open_interest']}")


def main() -> None:
    global _running

    def _stop(*_):
        global _running
        logger.info("REST采集器停止中...")
        _running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info(f"REST采集器启动 — FR每{FR_INTERVAL//60}分钟, OI每{OI_INTERVAL//60}分钟")

    last_fr = 0.0
    last_oi = 0.0

    # 启动立即采集一次
    collect_funding_rate()
    collect_open_interest()
    last_fr = last_oi = time.time()

    while _running:
        now = time.time()
        if now - last_fr >= FR_INTERVAL:
            collect_funding_rate()
            last_fr = now
        if now - last_oi >= OI_INTERVAL:
            collect_open_interest()
            last_oi = now
        time.sleep(10)  # 每10秒检查一次定时器

    logger.info("REST采集器已停止")


if __name__ == "__main__":
    main()
