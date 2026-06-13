# 系统化策略研究备忘:小市值加密永续

> Strategy research memo. 在 OKX 小市值 USDT 永续合约上研究系统化交易策略。
> 基准 (benchmark) 为同篮子买入持有 (buy-and-hold)。所有数字均来自
> `crypto-quant/data/` 下的真实回测产物。
>
> **📌 前期工作 (prior work):** 本文记录已回测的三类**方向性**策略及其结论。
> 下一阶段转向**市场中性的配对交易** —— 设计见 [`pairs-trading-design.md`](./pairs-trading-design.md)。

---

## 摘要 (Executive Summary)

- 实现并评估了 3 类策略:**Ignition**(动量突破趋势跟随)、**MeanReversion**(逆势均值回归)、**FundingCarry**(资金费 carry)。
- 经样本外 walk-forward 验证,**仅 Ignition 存在正向、可重复的超额收益 (edge)**:6 个 OOS 窗口中 4 个跑赢基准,月均超额 ≈ **+4.9%**。
- 但其表现**方差大、稳定性不足**(edge 最高 +22%、最低 −6.8%;胜率 8%~50%;最大回撤一度 −26%)。**评级:borderline,尚不可上实盘。**
- MeanReversion 与裸 FundingCarry 均被否定,但给出了明确的失败归因。

---

## 1. 交易标的与数据 (Universe & Data)

| 项 | 取值 |
|---|---|
| 标的 | OKX 上 USDT 本位永续合约,小市值段 |
| 频率 | 1 小时 K 线 (OHLCV) + 资金费率 (funding rate) |
| 数据源 | CCXT(公开 REST),本地缓存至 `data/` |
| 历史 | 最长 365 天 |
| 基准 | 同篮子买入持有 (buy-and-hold, B&H) |

---

## 2. 标的池构建 (Universe Construction)

逐时点 (point-in-time) 筛选,由 `cq build-pool` 三级收窄,对应 `src/crypto_quant/pool.py`:

| 级别 | 规则 | 结果 | 设计意图 |
|---|---|---|---|
| ① 全市场 | OKX 全部 USDT 永续,**剔除 BTC/ETH 等大市值** | 数百 | 动量溢价集中在小市值,大盘缺乏爆发力 |
| ② 流动性过滤 | 24h 名义成交额 **$1M ~ $80M**,取成交额 Top 30 | 30 | 下限控滑点/可成交性;上限避开已充分定价的大盘 |
| ③ 成本区打分 | 按 `score` 排序取 Top | **12** | 偏好临近成本、尚未启动;剔除已过度拉伸 |

打分函数 (`pool._pool_score`):

```
score = 0.5 × 阶段分 + 0.3 × 贴近成本分 + 0.2 × 流动性分
```

- **阶段分**:`ignition`(正在启动)= 1.0,`extended`(已过度拉伸)= 0.1
- **贴近成本分**:距 7 日 VWAP(成本代理)越近越高
- **流动性分**:区间内成交额对数归一

> **幸存者偏差提示 (survivorship caveat):** 标的池为当前时点快照,回测在各标的的完整历史上回放;长期评估需引入逐时点重建。

**最终池 12 个,实际产生成交 11 个**(`SPACEX` 历史不足被跳过):

```
ASTER · LIT · EDEN · BERA · BEAT · APE · GRASS · AXS · AUCTION · IP · KAITO
```

画像一致:中等流动性的小市值叙事币(成交额 $6.6M ~ $76M)。

---

## 3. 策略一:Ignition — 动量突破趋势跟随

### 3.1 Thesis · 经济逻辑

小市值币种存在动量效应:价格**放量突破前高**后,趋势具正自相关、倾向延续。策略意在**捕获右尾大行情 (right-tail / convexity)**,而非追求高胜率。方向:仅做多。

### 3.2 入场信号 (Signal)

| 条件 | 含义 |
|---|---|
| Donchian 突破 N 小时最高点 | 动量触发 |
| 成交量 > 均量 × 倍数 | 放量确认,过滤假突破 |
| 高周期趋势过滤 (HTF SMA 在上) | 顺大势 |
| 距成本区不过远 | 不追高、控回撤 |

参数见 `config/default.yaml` 的 `strategy.params`(`breakout_hours=36`、`vol_mult=1.3`、`htf_trend_hours=96` 等)。

### 3.3 组合构建 (Portfolio Construction)

- **单一账户**,最多同时持有 **5 仓**(有限风险预算)。
- 单笔仓位 = **25% 权益**(`position_fraction=0.25`)。
- 机会超额时按**突破强度排序**择优:`entry_strength =(现价 − 突破位)/ ATR`。

### 3.4 风控 / 出场 (Risk & Exit)

| 机制 | 取值 | 作用 |
|---|---|---|
| ATR 初始止损 | 3.5 × ATR | 限制单笔下行 |
| 追踪止损 (trailing) | 20% | 让利润奔跑,锁定趋势利润 |
| 时间止损 | 48h | 回收滞涨头寸资金 |
| 止盈 (take-profit) | **无** | 右尾收益靠极少数大赢家,不提前了结 |

### 3.5 执行假设 (Cost Model)

- taker 手续费 **0.04% / 边**,滑点 **15 bps / 边**(小市值偏保守)。
- 资金费按持仓累计;**多头在牛市通常支付资金费,对收益是拖累**(见第 5 节 OOS 表 `funding_pnl` 列均为负)。

---

## 4. 收益分布:强正偏 (Positive Skew)

趋势跟随的典型特征 —— 命中率中等,收益高度集中在右尾,少数标的贡献绝大部分盈亏。

**各标的净盈亏(Sprint-3 组合回测 @25% 仓位,约 4/27–5/26,n=59 笔):**

| 标的 | 净盈亏 (USD) | | 标的 | 净盈亏 (USD) |
|---|---:|---|---|---:|
| EDEN | **+2,125** | | AUCTION | −33 |
| BEAT | **+770** | | BERA | −48 |
| LIT | **+474** | | ASTER | −80 |
| GRASS | **+456** | | APE | −231 |
| KAITO | +57 | | AXS | −248 |
| | | | IP | −272 |

**交易级统计 (trade-level stats):**

| 指标 | 数值 |
|---|---|
| 样本数 | 59 笔 |
| 命中率 (hit rate) | **49.2%** |
| 平均盈利 / 平均亏损 | +$169 / −$65 |
| 盈亏比 (payoff ratio) | **2.6×** |
| 盈利因子 (profit factor) | **2.5×** |
| 该区间净收益 | **+29.7%**(初始 $10k) |

> **解读:** 约 49% 的命中率配 2.6× 的盈亏比即可获得正期望 (positive expectancy)。EDEN 单标的 +$2,125(含一笔 +100%),与 BEAT/LIT/GRASS 共同贡献几乎全部利润。这正是**刻意不设止盈、用追踪止损保留右尾**的依据。

---

## 5. 样本外验证 (Out-of-Sample Walk-Forward)

**方法:** 近 6 个月切为 6 段、各 30 天、**互不重叠**;参数**不逐窗重拟合**,杜绝前视偏差 (look-ahead bias)。核心指标 **edge = 策略收益 − 同期买入持有收益**。

| 窗口 | 区间 | 策略 % | 买入持有 % | **超额 edge %** | 最大回撤 % | 胜率 % |
|---|---|---:|---:|---:|---:|---:|
| W0 | 11/27–12/27 | −23.13 | −24.01 | **+0.88** | −26.32 | 8.3 |
| W1 | 12/27–01/26 | +40.34 | +26.82 | **+13.52** | −13.55 | 50.0 |
| W2 | 01/26–02/25 | −8.93 | −31.19 | **+22.27** | −14.46 | 14.3 |
| W3 | 02/25–03/27 | −1.76 | −0.65 | **−1.11** | −10.28 | 36.8 |
| W4 | 03/27–04/26 | +13.53 | +12.96 | **+0.58** | −11.29 | 34.9 |
| W5 | 04/26–05/26 | −3.01 | +3.77 | **−6.78** | −7.96 | 42.5 |

**结论:**
- **4/6 窗口跑赢基准**,平均超额 ≈ **+4.9%/月** → 存在真实 alpha。
- **下行抗跌**:W2 基准 −31%,策略仅 −9%(趋势过滤 + 止损起效)。
- 但**跨窗极不稳定**:edge 区间 [−6.8%, +22.3%],胜率 [8%, 50%],最大回撤一度 −26%。

> **评级:borderline。** 有 alpha,但稳定性与回撤控制尚不足以直接上实盘。

---

## 6. 三策略对照 (Benchmarked Alternatives)

| 策略 | 类型 / 方向 | 结论 | 失败/采用归因 |
|---|---|---|---|
| **Ignition** | 动量突破趋势 · 多 | ✅ 采用 | 正向 OOS edge,右尾收益;borderline |
| **MeanReversion** | 逆势均值回归 · 空 | ❌ 淘汰 | 逆动量做空小市值,趋势延续时被反复止损 |
| **FundingCarry** | 资金费 carry · 双向 | ❌ 淘汰 | 仅做裸头寸未对冲;收 $9 费 vs 价格亏 $1,011 |

> **FundingCarry 关键澄清:** 我们只实现了合约单腿的「裸 carry」。完整做法是
> **delta 中性基差套利 (delta-neutral basis trade)**:现货多 + 永续空,对冲价格风险,
> 仅留资金费收益。诚实结论是 **"裸 carry 不可行,要做必须补现货对冲腿"** —— 而非该 edge 不存在。

---

## 7. 局限与风险 (Limitations & Risks)

- **样本外历史短**:仅 6 个月,且偏牛市制度 (regime);跨制度稳健性未验证。
- **幸存者偏差**:标的池为时点快照,未做逐时点重建。
- **单一交易所**;小市值对滑点/流动性敏感,真实成交可能劣于假设。
- **未做超出假设的交易成本压力测试**;未估算策略容量 (capacity)。

---

## 8. 结论与下一步 (Conclusion & Next Steps)

仅 **Ignition** 在样本外呈现正向、可重复的超额收益,依靠强正偏的收益分布取胜,故采用「不止盈 + 追踪止损」的趋势出场。当前评级 **borderline:有 alpha、不稳定**。

**下一步:**
1. 延长 OOS 历史并做**制度标注 (regime tagging)**,检验跨牛熊稳健性。
2. **波动率目标化仓位 (vol-targeting sizing)**,平滑回撤、降低跨窗方差。
3. 构建 **delta 中性 carry**(补现货腿),把 FundingCarry 做成完整策略。
4. **滑点 / 资金费压力测试**,估算容量上限。

---

## 附:关键代码 / 数据位置

| 内容 | 路径 |
|---|---|
| 标的池构建 | `src/crypto_quant/pool.py` |
| 策略实现 | `src/crypto_quant/strategy/{ignition,mean_reversion,funding_carry}.py` |
| 组合回测引擎 | `src/crypto_quant/backtest/portfolio.py` |
| 样本外 walk-forward | `src/crypto_quant/backtest/walk_forward.py` |
| 参数配置 | `config/default.yaml` |
| 最终标的池 | `data/pools/pool_okx_latest.csv` |
| OOS 验证结果 | `data/backtests/validate_*_windows.csv` |
| 组合回测成交 | `data/backtests/portfolio_ignition_*_trades.csv` |
