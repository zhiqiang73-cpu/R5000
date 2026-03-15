# 回测引擎详细设计

## 核心要求

### 1. 避免未来数据泄露
- 分类等待窗口（45秒）必须严格遵守
- 只能使用冲击时间点之后的数据
- 事件时间戳精确到毫秒

### 2. 真实成本模型
- 手续费：双边0.10%
- 滑点：0.02-0.05%
- 价格冲击：0.01-0.02%
- 总成本：0.13-0.17%

### 3. 事件驱动架构
- 按时间顺序处理所有市场事件
- 模拟真实交易延迟
- 支持限价单、市价单成交逻辑

## 引擎类设计

```python
class BacktestEngine:
    def __init__(self, config: Dict):
        self.config = config
        self.events = []
        self.positions = []
        self.trades = []
        self.capital = Decimal("10000.0")
        self.current_time = None

    def load_data(self, data_path: str):
        """加载历史数据"""
        # 加载以下数据源：
        # 1. 清算数据
        # 2. 逐笔成交
        # 3. 订单簿快照
        # 4. K线数据
        # 5. 资金费率
        # 6. OI变化
        pass

    def simulate_layer1(self, timestamp: int):
        """第1层环境过滤模拟"""
        # 输入：时间戳
        # 输出：env_data = {
        #     'status': 'tradable' | 'dormant',
        #     'direction_bias': 'long' | 'short' | 'neutral',
        #     'liquidation_side': 'long' | 'short' | None,
        #     'stop_multiplier': 0.8-1.3
        # }
        pass

    def detect_impacts(self, start_time: int, end_time: int):
        """检测冲击事件"""
        # 基于价格和成交量变化
        # 返回冲击事件列表
        pass

    def simulate_layer2(self, impact_event, env_data):
        """第2层冲击分类模拟"""
        # 等待45秒观察窗口
        # 收集清算数据
        # 分类为：真突破/过度反应/不确定
        pass

    def simulate_layer3(self, classification, env_data):
        """第3层执行模拟"""
        # 根据分类选择策略
        # 计算入场、止损、止盈
        # 检查盈亏比和偏离度
        # 确定信号等级和仓位
        pass

    def execute_order(self, order, current_price: Decimal):
        """执行订单（含成本）"""
        # 应用滑点
        # 应用手续费
        # 记录交易
        pass

    def run_backtest(self, start_date: str, end_date: str):
        """运行回测"""
        results = {
            'metrics': {},
            'trades': [],
            'daily_pnl': []
        }
        return results
```

## 验证指标计算

### 第1层验证指标
```python
def validate_layer1(results):
    """验证第1层性能"""

    # 1. 休眠有效性
    dormant_periods = find_dormant_periods(results)
    trades_in_dormant = simulate_trades_in_dormant(dormant_periods)
    loss_rate = calculate_loss_rate(trades_in_dormant)
    assert loss_rate > 0.55, f"休眠有效性不足: {loss_rate}"

    # 2. 方向偏向准确性
    direction_predictions = extract_direction_bias(results)
    liquidation_directions = extract_actual_liquidation_directions(results)
    accuracy = calculate_direction_accuracy(direction_predictions, liquidation_directions)
    assert accuracy > 0.55, f"方向偏向准确性不足: {accuracy}"

    # 3. 样本量检查
    assert len(results['timestamps']) >= 300, "样本量不足300"
```

### 第2层验证指标
```python
def validate_layer2(results):
    """验证第2层性能"""

    # 1. 冲击检测召回率
    true_impacts = find_true_impacts(results)  # 人工标注的真实冲击
    detected_impacts = results['detected_impacts']
    recall = calculate_recall(true_impacts, detected_impacts)
    assert recall > 0.80, f"召回率不足: {recall}"

    # 2. 有清算冲击趋势延续
    liquidation_impacts = filter_impacts_with_liquidation(results)
    trend_continuation_rate = calculate_trend_continuation(liquidation_impacts)
    assert trend_continuation_rate > 0.60, f"趋势延续率不足: {trend_continuation_rate}"

    # 3. 无清算冲击价格回归
    no_liquidation_impacts = filter_impacts_without_liquidation(results)
    mean_reversion_rate = calculate_mean_reversion(no_liquidation_impacts)
    assert mean_reversion_rate > 0.55, f"价格回归率不足: {mean_reversion_rate}"

    # 4. vs随机基准
    random_classification = generate_random_classification(results)
    random_accuracy = calculate_classification_accuracy(random_classification)
    our_accuracy = calculate_classification_accuracy(results['classifications'])
    improvement = our_accuracy - random_accuracy
    assert improvement > 0.10, f"相对随机提升不足: {improvement}"
```

### 第3层验证指标
```python
def validate_layer3(results):
    """验证第3层性能"""

    # 1. 均值回归净期望
    mean_reversion_trades = filter_mean_reversion_trades(results)
    mean_reversion_expectancy = calculate_expectancy(mean_reversion_trades)
    assert mean_reversion_expectancy > 0, f"均值回归净期望为负: {mean_reversion_expectancy}"

    # 2. 趋势跟随净期望
    trend_follow_trades = filter_trend_follow_trades(results)
    trend_follow_expectancy = calculate_expectancy(trend_follow_trades)
    assert trend_follow_expectancy > 0, f"趋势跟随净期望为负: {trend_follow_expectancy}"

    # 3. 综合绩效
    all_trades = results['trades']
    win_rate = calculate_win_rate(all_trades)
    avg_rr = calculate_avg_risk_reward(all_trades)
    assert win_rate > 0.42, f"胜率不足: {win_rate}"
    assert avg_rr > 1.5, f"平均盈亏比不足: {avg_rr}"

    # 4. 熔断机制
    circuit_breaker_triggers = results['circuit_breaker_triggers']
    assert len(circuit_breaker_triggers) > 0, "熔断机制未触发"

    # 5. Sharpe和回撤
    sharpe = calculate_sharpe_ratio(results)
    max_dd = calculate_max_drawdown(results)
    assert sharpe > 0.8, f"Sharpe不足: {sharpe}"
    assert max_dd < 0.15, f"最大回撤超限: {max_dd}"
```

### 集成验证指标
```python
def validate_integration(results):
    """验证集成性能"""

    # 1. 所有fold净期望 > 0
    folds_results = results['walk_forward_folds']
    for fold in folds_results:
        assert fold['expectancy'] > 0, f"Fold {fold['id']}净期望为负"

    # 2. 参数稳定性
    parameter_sensitivity = analyze_parameter_sensitivity(results)
    max_sharpe_change = parameter_sensitivity['max_sharpe_change']
    assert max_sharpe_change < 0.25, f"参数敏感性过高: {max_sharpe_change}"

    # 3. 极端行情存活
    extreme_periods = ['2020-03', '2022-05', '2024-08']
    for period in extreme_periods:
        period_results = filter_results_by_period(results, period)
        if period_results['max_drawdown'] > 0.30:
            print(f"警告：{period}期间回撤过大: {period_results['max_drawdown']}")
```

## 测试数据生成

### 合成数据生成器
```python
class SyntheticDataGenerator:
    def __init__(self, seed=42):
        self.rng = np.random.default_rng(seed)

    def generate_liquidations(self, price_series, n=1000):
        """生成清算数据"""
        # 清算与价格冲击相关
        # 真突破：清算密集
        # 过度反应：清算稀少
        pass

    def generate_market_data(self, days=30, frequency='1min'):
        """生成完整市场数据"""
        # 价格序列
        # 成交量
        # 资金费率
        # OI变化
        # 清算数据
        pass
```

## 实施优先级

### Phase 1：基础回测框架
1. 事件驱动引擎
2. 基础数据加载
3. 成本模型实现
4. 基础绩效指标

### Phase 2：各层验证
1. 第1层环境过滤测试
2. 第2层冲击分类测试
3. 第3层执行测试

### Phase 3：Walk-Forward验证
1. 时间序列分割
2. 参数稳定性测试
3. 极端行情测试

### Phase 4：实盘模拟
1. 实时数据接口
2. 订单执行模拟
3. 监控和报告