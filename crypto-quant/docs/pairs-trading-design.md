# 配对交易策略设计:统计套利 (Pairs Trading / Statistical Arbitrage)

> **状态:设计稿 (design spec),尚未回测。** 本文所有阈值、配对、参数均为**待验证假设**,
> 不含回测结果。真实的前期工作(Ignition 等三策略)见 [`strategy-overview.md`](./strategy-overview.md)。

---

## 摘要 (Executive Summary)

- 方向:从方向性 (directional) 策略转向**市场中性 (market-neutral)** 的配对交易。
- 做法:做多被低估的一腿、**做空 (short)** 被高估的一腿,从两者**价差 (spread) 的均值回归**中获利,与大盘涨跌无关。
- 动机:此前三类策略都携带**市场 beta**,下行环境回撤大;配对交易用「一多一空」对冲掉 beta,只保留相对错价。

---

## 1. 为什么转向:方向性策略的共同缺陷

| 策略 | 类型 / 方向 | 结论 | 核心问题 |
|---|---|---|---|
| Ignition | 动量突破趋势 · 多 | borderline | 有 alpha 但带满市场 beta,下行窗口回撤大(最差 −26%)、跨窗不稳 |
| MeanReversion | 逆势均值回归 · 空 | 淘汰 | 逆动量做空单一小币,趋势延续时被反复止损 |
| FundingCarry | 资金费 carry · 双向 | 淘汰 | 裸头寸未对冲 → 实为裸赌价格 |

**共同病根:市场 beta。** 三者盈亏都高度依赖大盘方向。配对交易把方向性敞口对冲掉,
只保留两标的之间的相对表现差 —— 这正是 FundingCarry 缺失的「对冲腿」思想的推广。

---

## 2. 核心逻辑 (Thesis)

经济相关的两个币(同板块 / 同叙事)长期同涨同跌。即使各自波动剧烈,其**价差通常是平稳
(stationary)、均值回归**的。当价差显著偏离均值时入场,等其**收敛 (convergence)** 获利。

- **盈亏来自相对表现差,而非绝对方向** → 对 BTC 的 beta 目标 ≈ 0。
- **相对单标的的优势**:单个小币价格几乎不可预测;但同板块两币的价差远比单边价格平稳,可统计建模。
- **相对方向性策略的优势**:市场中性,大盘暴跌时不直接吃回撤,收益曲线与 Ignition 的 beta 暴露互补。

---

## 3. 配对选择 (Pair Selection)

三级流程:

| 级别 | 方法 | 产出 |
|---|---|---|
| ① 同板块分组 | 复用现有小市值池,按板块/叙事组内两两配对 | 候选对 |
| ② 协整检验 | Engle-Granger / ADF (Augmented Dickey-Fuller) 检验价差平稳性,估计对冲比 β | 通过检验的对 |
| ③ 按半衰期排序 | 按均值回归半衰期 (half-life)、ADF 显著性排序择优 | 可交易配对清单 |

**待协整检验的候选配对**(来自现有 11 个标的,按板块分组):

| 板块 | 成员 | 候选配对(待检验) |
|---|---|---|
| AI / 数据 | GRASS · KAITO · EDEN | GRASS–KAITO, KAITO–EDEN, GRASS–EDEN |
| 游戏 / 元宇宙 | APE · AXS | APE–AXS |
| 新公链 / 基础设施 | ASTER · BERA · LIT · IP | ASTER–BERA, LIT–IP, BERA–LIT |

---

## 4. 信号:价差 Z-Score (Spread Z-Score)

```
spread = log(P_A) − β · log(P_B)
z      = (spread − rolling_mean) / rolling_std
```

| 动作 | 触发条件(设计初值) | 操作 |
|---|---|---|
| 入场 | `|z| > 2.0`(价差显著偏离) | **做空 (short)** 高估腿 + **做多 (long)** 低估腿 |
| 出场 | `z` 回归至 ≈ 0(±0.5) | 价差收敛,双腿平仓获利 |
| 止损 | `|z| > 3.5`(价差继续扩大) | 协整可能破裂,强制平仓 |
| 时间止损 | 持仓 > k × 半衰期 | 回归未发生,回收资金 |

> 阈值为设计初值,需在样本内标定 (in-sample calibration)、样本外验证 (OOS)。

---

## 5. 头寸构建与风控 (Construction & Risk)

**头寸构建:**
- 两条腿一多一空,**净 delta ≈ 0**。
- **美元中性 (dollar-neutral)** 或 **β 中性 (beta-neutral)**。
- 单配对仓位上限 + 组合内多配对分散;复用现有组合引擎的多仓位与做空能力。

**主要风险:**
- **协整破裂 (structural breakdown)** —— 关系失效,价差不再回归。
- **两腿资金费不对称** → carry 成本拖累。
- **双腿 → 手续费 / 滑点翻倍。**
- **空头腿强平 / 单币退市** 风险。

**缓释:** 滚动重估 β 与协整;失去平稳性的配对及时剔除;成本模型需覆盖**双腿**费用,
确认价差幅度能覆盖成本后才入场。

---

## 6. 回测计划 (Backtest Plan)

| 维度 | 方案 |
|---|---|
| 样本外 | 沿用 walk-forward 多窗口,参数不逐窗重拟合(防前视偏差 look-ahead bias) |
| 基准 | 市场中性 → 基准为现金/零,**edge 即绝对收益** |
| 核心指标 | **Sharpe(此时有意义)**、命中率、回归半衰期 vs 实际持仓、最大回撤 |
| 中性校验 | 对 BTC 的 beta / 相关性应 **≈ 0**(否则未真正中性) |
| 成本 | 双腿手续费 + 滑点 + 两腿净资金费 |

---

## 7. 下一步 (Next Steps)

1. 实现**协整检验 + 半衰期估计**,产出可交易配对清单。
2. 在组合引擎上实现**价差 z-score 信号**(复用做空能力)。
3. **walk-forward OOS 验证**,核对 BTC beta ≈ 0 与 Sharpe。
4. 与 Ignition 做**收益相关性分析** —— 中性策略应能分散其 beta,二者或可组合。

---

## 附:术语对照 (Glossary)

| 中文 | English |
|---|---|
| 做空 | short / short-selling / go short / short position |
| 做多 | long / go long / long position |
| 配对交易 | pairs trading |
| 统计套利 | statistical arbitrage (stat-arb) |
| 市场中性 | market-neutral |
| 价差 | spread |
| 协整 | cointegration |
| 对冲比 | hedge ratio (β) |
| 均值回归 | mean reversion |
| 半衰期 | half-life |
| 收敛 | convergence |
