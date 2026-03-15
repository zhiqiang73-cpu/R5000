# -*- coding: utf-8 -*-
"""
LiveBroker：对接 Binance USDT 合约 REST API
支持 Testnet / Mainnet，负责下单、撤单、查仓、查余额
"""

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class BinanceError(Exception):
    """Binance API 错误"""
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Binance Error {code}: {msg}")


class LiveBroker:
    TESTNET_BASE = "https://testnet.binancefuture.com"
    MAINNET_BASE = "https://fapi.binance.com"

    def __init__(self, api_key: str, secret: str, testnet: bool = True):
        self.api_key = api_key
        self.secret = secret
        self.base_url = self.TESTNET_BASE if testnet else self.MAINNET_BASE
        self.testnet = testnet
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})
        self._time_offset: int = 0   # 本机时钟与服务器时钟的差值(ms)
        self._forcing_one_way: bool = False
        self.sync_time()             # 初始化时立即同步，避免 -1021

    # ── 签名与请求 ────────────────────────────────────────────────────

    def sync_time(self) -> int:
        """
        与 Binance 服务器同步时间，计算本机时钟偏移量。
        初始化时自动调用；若持续出现 -1021，可手动再调用一次。
        返回偏移量（ms），正值表示本机偏慢。
        """
        local_before = int(time.time() * 1000)
        try:
            resp = self.session.get(
                f"{self.base_url}/fapi/v1/time", timeout=5
            )
            server_time = resp.json()["serverTime"]
        except Exception as exc:
            logger.warning("时间同步失败: %s，偏移保持 %d ms", exc, self._time_offset)
            return self._time_offset
        local_after = int(time.time() * 1000)
        local_mid = (local_before + local_after) // 2
        self._time_offset = server_time - local_mid
        logger.info("时间同步: 服务器=%d  本机=%d  偏移=%+d ms",
                    server_time, local_mid, self._time_offset)
        return self._time_offset

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # 用校正时间戳，消除本机时钟漂移导致的 -1021
        params["timestamp"] = int(time.time() * 1000) + self._time_offset
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(
            self.secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        return params

    def _get(self, path: str, params: Optional[Dict] = None, signed: bool = True) -> Any:
        params = dict(params or {})
        if signed:
            params = self._sign(params)
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=10)
        try:
            return self._handle(resp)
        except BinanceError as exc:
            if exc.code == -1021 and signed:          # 时钟漂移：重新同步后重试一次
                logger.warning("-1021 时钟偏差，重新同步时间后重试...")
                self.sync_time()
                params = self._sign(dict(params))
                resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=10)
                return self._handle(resp)
            raise

    def _post(self, path: str, params: Optional[Dict] = None) -> Any:
        raw = dict(params or {})
        signed = self._sign(dict(raw))
        resp = self.session.post(f"{self.base_url}{path}", data=signed, timeout=10)
        try:
            return self._handle(resp)
        except BinanceError as exc:
            if exc.code == -1021:                     # 时钟漂移：重新同步后重试一次
                logger.warning("-1021 时钟偏差，重新同步时间后重试...")
                self.sync_time()
                signed = self._sign(dict(raw))
                resp = self.session.post(f"{self.base_url}{path}", data=signed, timeout=10)
                return self._handle(resp)
            if exc.code == -4061 and "/fapi/v1/order" in path:
                # 仓位模式不匹配（测试网可能自动恢复 HEDGE 模式）
                # 强制修复链路：撤单 -> 平掉对冲仓 -> 切单向模式 -> 重试签名请求
                if self._forcing_one_way:
                    raise
                symbol = str(raw.get("symbol", "")).upper() if raw.get("symbol") else "BTCUSDT"
                logger.warning("-4061 仓位模式不匹配，执行强制修复并重试... symbol=%s", symbol)
                self.force_one_way_mode(symbol)
                signed = self._sign(dict(raw))
                resp = self.session.post(f"{self.base_url}{path}", data=signed, timeout=10)
                return self._handle(resp)
            raise

    def _delete(self, path: str, params: Optional[Dict] = None) -> Any:
        raw = dict(params or {})
        signed = self._sign(dict(raw))
        resp = self.session.delete(f"{self.base_url}{path}", params=signed, timeout=10)
        try:
            return self._handle(resp)
        except BinanceError as exc:
            if exc.code == -1021:
                logger.warning("-1021 时钟偏差，重新同步时间后重试...")
                self.sync_time()
                signed = self._sign(dict(raw))
                resp = self.session.delete(f"{self.base_url}{path}", params=signed, timeout=10)
                return self._handle(resp)
            raise

    def _handle(self, resp: requests.Response) -> Any:
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            return {}
        if isinstance(data, dict) and "code" in data and data["code"] != 200:
            raise BinanceError(data["code"], data.get("msg", ""))
        return data

    # ── 账户信息 ──────────────────────────────────────────────────────

    def ping(self) -> bool:
        """测试连接"""
        try:
            self._get("/fapi/v1/ping", signed=False)
            return True
        except Exception as exc:
            logger.error("Ping 失败: %s", exc)
            return False

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """设置交易品种的杠杆倍数"""
        params = {
            "symbol": symbol,
            "leverage": leverage,
        }
        try:
            result = self._post("/fapi/v1/leverage", params)
            logger.info("杠杆已设置: %s=%dx", symbol, leverage)
        except BinanceError as exc:
            if exc.code == -4059:
                logger.info("杠杆已是 %dx，无需设置", leverage)
            else:
                raise

    def ensure_one_way_mode(self) -> None:
        """确保账户为单向持仓模式（非对冲模式），下单时无需指定 positionSide"""
        try:
            self._post("/fapi/v1/positionSide/dual", {"dualSidePosition": "false"})
            logger.info("已切换为单向持仓模式")
        except BinanceError as exc:
            if exc.code == -4059:
                logger.info("已是单向持仓模式，无需切换")
            else:
                raise

    def close_all_hedge_positions(self) -> int:
        """
        平掉所有非零仓位（兼容 HEDGE/BOTH）。
        返回：成功平仓数量。
        """
        try:
            all_positions = self._get("/fapi/v2/positionRisk")
        except Exception as exc:
            logger.error("查询仓位失败，无法清理: %s", exc)
            return 0

        closed = 0
        for pos in all_positions:
            amt = Decimal(str(pos.get("positionAmt", "0")))
            if amt == 0:
                continue

            symbol = str(pos.get("symbol", "")).upper()
            pos_side = str(pos.get("positionSide", "BOTH")).upper()
            side = "SELL" if amt > 0 else "BUY"
            qty = abs(amt)

            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": f"{float(qty):.3f}",
                "reduceOnly": "true",
            }
            if pos_side in {"LONG", "SHORT"}:
                params["positionSide"] = pos_side

            try:
                self._post("/fapi/v1/order", params)
                closed += 1
                logger.warning("已清理仓位: %s %s %s %s", symbol, pos_side, side, qty)
            except BinanceError as exc:
                logger.error("清理仓位失败: %s %s code=%d msg=%s", symbol, pos_side, exc.code, exc.msg)
            except Exception as exc:
                logger.error("清理仓位异常: %s %s err=%s", symbol, pos_side, exc)

        return closed

    def _cancel_all_open_orders_all_symbols(self) -> int:
        """撤销全账户所有品种挂单。返回成功撤单的品种数量。"""
        try:
            open_orders = self._get("/fapi/v1/openOrders")
        except BinanceError as exc:
            logger.warning("查询全量挂单失败 code=%d: %s", exc.code, exc.msg)
            return 0
        except Exception as exc:
            logger.warning("查询全量挂单异常: %s", exc)
            return 0

        symbols = sorted({str(order.get("symbol", "")).upper() for order in open_orders if order.get("symbol")})
        cancelled = 0
        for symbol in symbols:
            try:
                self.cancel_all_orders(symbol)
                cancelled += 1
            except Exception as exc:
                logger.warning("撤销 %s 挂单失败: %s", symbol, exc)
        return cancelled

    def force_one_way_mode(self, preferred_symbol: str = "BTCUSDT") -> None:
        """
        强制恢复单向模式：
          1) 先撤全账户挂单（避免 -4067）
          2) 平掉所有对冲/残留仓位
          3) 再次撤单（避免平仓时产生新挂单冲突）
          4) 切换到单向模式
        """
        logger.warning("开始强制恢复单向模式... preferred=%s", preferred_symbol)
        self._forcing_one_way = True
        try:
            cancelled_1 = self._cancel_all_open_orders_all_symbols()
            closed = self.close_all_hedge_positions()
            cancelled_2 = self._cancel_all_open_orders_all_symbols()

            try:
                self.ensure_one_way_mode()
            except BinanceError as exc:
                if exc.code == -4067:
                    # 极端情况下仍有挂单，最后再清一次并重试
                    logger.warning("切换单向模式遇到 -4067，再次清挂单后重试...")
                    self._cancel_all_open_orders_all_symbols()
                    self.ensure_one_way_mode()
                else:
                    raise

            logger.warning(
                "强制恢复完成: 首轮撤单品种=%d 平仓数量=%d 次轮撤单品种=%d",
                cancelled_1, closed, cancelled_2,
            )
        finally:
            self._forcing_one_way = False

    def get_server_time(self) -> int:
        """获取服务器时间戳（ms）"""
        data = self._get("/fapi/v1/time", signed=False)
        return int(data["serverTime"])

    def get_balance(self) -> Decimal:
        """
        获取 USDT 钱包总余额（用于仓位计算）
        注：totalWalletBalance 包含未实现盈亏，适合作为资金基数
        """
        data = self._get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return Decimal(str(asset["balance"]))
        return Decimal("0")

    def get_available_balance(self) -> Decimal:
        """获取 USDT 可用余额（下单前检查是否有足够保证金）"""
        data = self._get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return Decimal(str(asset["availableBalance"]))
        return Decimal("0")

    def get_position(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """获取当前持仓，无持仓返回 None"""
        data = self._get("/fapi/v2/positionRisk", {"symbol": symbol})
        for pos in data:
            if pos["symbol"] == symbol and Decimal(pos["positionAmt"]) != 0:
                return pos
        return None

    def get_open_orders(self, symbol: str = "BTCUSDT") -> list:
        """获取所有挂单"""
        return self._get("/fapi/v1/openOrders", {"symbol": symbol})

    def get_order(self, symbol: str, order_id: int) -> Dict:
        """查询单笔订单状态"""
        return self._get("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})

    # ── 下单 ──────────────────────────────────────────────────────────

    def place_limit_order(
        self,
        symbol: str,
        side: str,          # "BUY" | "SELL"
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> Dict:
        """挂限价单（趋势跟随入场）"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "quantity": f"{float(quantity):.3f}",
            "price": f"{float(price):.1f}",
            "timeInForce": time_in_force,
        }
        result = self._post("/fapi/v1/order", params)
        logger.info("限价单已提交 orderId=%s side=%s qty=%s price=%s",
                    result.get("orderId"), side, quantity, price)
        return result

    def place_market_order(self, symbol: str, side: str, quantity: Decimal) -> Dict:
        """市价单（均值回归入场 / 时间止损平仓）"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{float(quantity):.3f}",
        }
        result = self._post("/fapi/v1/order", params)
        logger.info("市价单已成交 orderId=%s side=%s qty=%s",
                    result.get("orderId"), side, quantity)
        return result

    def place_stop_loss(self, symbol: str, side: str, quantity: Decimal, stop_price: Decimal) -> Dict:
        """止损单（STOP_MARKET closePosition=true，平掉全部持仓）"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": f"{float(stop_price):.1f}",
            "closePosition": "true",   # 触发后平全仓，无需指定 quantity
        }
        result = self._post("/fapi/v1/order", params)
        logger.info("止损单已挂 orderId=%s stopPrice=%s", result.get("orderId"), stop_price)
        return result

    def place_take_profit(self, symbol: str, side: str, quantity: Decimal, tp_price: Decimal) -> Dict:
        """止盈单（TAKE_PROFIT_MARKET closePosition=true，平掉全部持仓）"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": f"{float(tp_price):.1f}",
            "closePosition": "true",   # 触发后平全仓
        }
        result = self._post("/fapi/v1/order", params)
        logger.info("止盈单已挂 orderId=%s tpPrice=%s", result.get("orderId"), tp_price)
        return result

    # ── 撤单 ──────────────────────────────────────────────────────────

    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """撤销单笔订单"""
        result = self._delete("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
        logger.info("已撤单 orderId=%s", order_id)
        return result

    def cancel_all_orders(self, symbol: str) -> Dict:
        """撤销该品种所有挂单（含止损/止盈）"""
        result = self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        logger.info("已撤销 %s 全部挂单", symbol)
        return result

    def close_position(self, symbol: str, position: Dict) -> Dict:
        """强制市价平仓"""
        amt = Decimal(str(position["positionAmt"]))
        if amt == 0:
            return {}
        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)
        logger.warning("时间止损：市价平仓 %s side=%s qty=%s", symbol, side, qty)
        return self.place_market_order(symbol, side, qty)
