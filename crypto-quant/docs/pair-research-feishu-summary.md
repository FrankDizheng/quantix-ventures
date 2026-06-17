# 小币 Pair Trading：Pair 池构建与验证框架

## 研究范围

这一阶段只处理 pair 池的构建与验证，不讨论具体开仓、平仓或实盘执行。

小币 pair trading 的前提是先找到一批关系足够稳定、成本上可执行、并且能够持续维护的币对。只有 pair 本身成立，后续的 z-score 交易、做多做空组合、资金费率处理和组合回测才有研究意义。

本轮研究对象限定在 OKX 上有永续合约、可获取 1h 行情、funding 和 order book 的 altcoin perpetual。这里的“小币”不是完全没有流动性的微盘币，而是 micro/small cap 中仍具备基础交易深度的标的。

市值分层采用以下口径：

| Bucket | Market Cap |
|---|---:|
| micro | < $100M |
| small | $100M - $500M |
| mid | $500M - $2B |
| large | > $2B |

当前核心 pair 主要落在 micro/small cap，没有纳入 BTC、ETH、SOL 等主流大币。

## 基本假设

Pair trading 研究的不是单个币的方向，而是两个币之间的相对关系。

一个值得进入研究池的 pair，通常需要同时满足几类条件：

- 有共同驱动，例如同一赛道、同一交易叙事、同一风险偏好暴露；
- 价格关系在当前周期内相对稳定，而不是只在全样本中偶然相关；
- spread 偏离后存在一定均值回归特征；
- funding、交易成本和盘口深度没有直接吞掉潜在收益；
- pair 关系允许动态失效，不能假设长期固定。

这与上次讨论中的方向一致：先区分赛道，再在候选币种里寻找满足统计假说的稳定价差。小币的生命周期和叙事切换都比较快，候选池需要按天级或数天级更新。

## Universe 构建

Universe 采用 group-first 逻辑，而不是全市场两两组合。

先按赛道和交易行为构建分组，再只在组内计算 pair：

| Group | 示例标的 |
|---|---|
| infra_l1 | BERA, IP, EGLD, STRK, KSM, VANA |
| market_infra | AUCTION, BERA, IP, ENS, ENSO |
| gaming_metaverse | APE, AXS, ENJ, YGG, GMT |
| ai_data | GRASS, KAITO, EDEN, LPT, VIRTUAL |
| defi_yield | GMX, UMA, COMP, SSV |
| attention_beta | APE, AXS, AUCTION, WIF |

这样做是为了减少伪相关。小币全市场两两组合很容易挖到短期共振，但如果没有共同驱动，后续很难稳定维护，也难以解释关系失效的原因。

当前 OKX grouped universe 的规模如下：

| 项目 | 数量 |
|---|---:|
| 配置标的 | 41 |
| 组内候选 pair | 161 |
| 最新快照 active_research | 18 |
| 最新快照 watchlist | 9 |
| rejected | 134 |

这里的 active_research 是单次诊断结果，只表示该 pair 在最新快照中通过了研究门槛，不等同于交易候选。

## 诊断框架

候选 pair 会经过三层验证：关系稳定性、交易可执行性、跨快照 persistence。

### 关系稳定性

| 指标 | 用途 |
|---|---|
| full-sample correlation | 判断两者是否有整体同向关系 |
| rolling correlation | 检查关系是否在近期窗口持续存在 |
| hedge beta | 估计多空两条腿的对冲比例 |
| beta CV / beta drift | 检查 hedge ratio 是否漂移过大 |
| half-life | 判断 spread 回归速度是否适合研究 |
| z-score opportunities | 检查历史上是否出现足够多的偏离 |
| convergence rate | 检查偏离后是否有收敛倾向 |

历史回归只作为起点，不作为最终判断。小币容易受到外部事件、BTC/ETH regime、交易所流动性变化和叙事切换影响，因此框架里加入了 rolling metrics 和 persistence，避免只依赖一次全样本相关性。

### 交易可执行性

| 指标 | 用途 |
|---|---|
| cost edge ratio | spread 机会是否覆盖手续费和保守滑点 |
| funding diff | 多空两条腿的 funding 差是否侵蚀收益 |
| order book spread | 当前盘口价差是否过宽 |
| 25bps / 50bps depth | 小仓位是否具备基础成交深度 |
| liquidity cost edge ratio | 用当前盘口成本重新评估 edge |

当前 liquidity gate：

- pair spread 不超过 20 bps；
- 25bps 内最弱侧深度至少 $10k；
- liquidity-adjusted cost edge 至少 1.5x。

这一层主要解决“小币看起来有波动，但实际无法交易”的问题。目前已经接入 order book spread 和深度；后续还需要把 24h volume、open interest、波动率分桶加入 universe 标签。

### Persistence

单次通过不直接升级为核心池。

当前维护四类状态：

| 状态 | 含义 |
|---|---|
| persistent_active | 多次快照均为 active，是后续交易研究的主池 |
| persistent_watchlist | 多次通过，但稳定性或分数低于 active |
| new_candidate | 最新快照通过，但缺少跨快照验证 |
| rejected_or_decayed | 被拒绝、隔离或关系退化 |

Persistence 规则：

- 至少 2 次 passing snapshot；
- pass rate 不低于 60%；
- persistent_active 需要至少 2 次 active snapshot；
- 当前核心 pair 均已通过 6/6 次 active 快照。

这使 pair 池不是一个静态名单，而是可以随着新快照升级、降级和隔离的动态候选池。

## 当前池状态

经过 persistence 之后，动态池分布如下：

| 状态 | 数量 |
|---|---:|
| persistent_active | 2 |
| persistent_watchlist | 4 |
| new_candidate | 21 |
| rejected_or_decayed | 171 |

扩展 universe 后，最新快照中出现了 18 个 active_research 和 9 个 watchlist。经过 persistence 过滤后，目前只有 2 个 pair 留在 persistent_active。这个结果偏保守，但更符合小币 pair 研究的要求：先确认关系稳定，再进入交易信号研究。

## Persistent Active

### BERA / IP

| 指标 | 数值 |
|---|---:|
| persistence | 6/6 active |
| latest score | 0.8972 |
| stability score | 0.7586 |
| correlation | 0.9835 |
| rolling corr p20 | 0.8119 |
| hedge beta | 约 0.8835 |
| cost edge ratio | 3.11x |
| pair spread | 7.39 bps |
| weakest 25bps depth | $31.4k |
| liquidity cost edge | 10.09x |
| 市值分层 | BERA: micro, IP: small |

`BERA / IP` 在过去 30 天呈现明显共同趋势。归一化价格显示两者同向下跌，但 IP 跌幅更大，导致 BERA 相对 IP 变强。

该 pair 的 hedge beta 约为 0.88，说明不能按 1:1 简单配对，而需要使用 hedge-adjusted spread。最新 z-score 约为 1.35，spread 偏高但未到极端区间，因此当前更适合作为持续观察对象，而不是直接交易信号。

### AUCTION / IP

| 指标 | 数值 |
|---|---:|
| persistence | 6/6 active |
| latest score | 0.8921 |
| stability score | 0.8063 |
| correlation | 0.9895 |
| rolling corr p20 | 0.8312 |
| cost edge ratio | 1.66x |
| pair spread | 8.79 bps |
| weakest 25bps depth | $31.4k |
| liquidity cost edge | 5.10x |
| 市值分层 | AUCTION: micro, IP: small |

`AUCTION / IP` 的相关性和 rolling stability 都较强，连续多次进入 active。相对 `BERA / IP`，它的 cost edge ratio 较低，但 liquidity-adjusted edge 仍然过关。

## Persistent Watchlist

| Pair | persistence | latest score | stability | 判断 |
|---|---:|---:|---:|---|
| AUCTION / BERA | 6/6 watchlist | 0.8955 | 0.6410 | 分数高，但 stability 略低于 active 门槛 |
| APE / AXS | 6/6 watchlist | 0.8095 | 0.5266 | 赛道关系清晰，但 rolling stability 不够 |
| APE / AUCTION | 6/6 watchlist | 0.7875 | 0.4892 | 关系存在，但稳定性偏弱 |
| AUCTION / AXS | 6/6 watchlist | 0.7707 | 0.4334 | 相关性高，但 rolling corr p20 偏低 |

这些 pair 说明当前框架能够持续找到有相对关系的小币组合，但它们还不适合进入交易研究主池。

## New Candidate

扩展 universe 后出现 21 个 new_candidate。它们通过了最新一次诊断，但尚未跨快照验证。

| Pair | Group | latest score | stability | correlation | 判断 |
|---|---|---:|---:|---:|---|
| BERA / ENS | market_infra | 0.9160 | 0.8149 | 0.9797 | 单次结果强，需继续观察 |
| EGLD / VANA | infra_l1 | 0.9157 | 0.7575 | 0.9814 | micro/micro，值得跟踪 |
| AUCTION / ENS | market_infra | 0.9059 | 0.7850 | 0.9850 | market infra 候选 |
| EGLD / IP | infra_l1 | 0.9034 | 0.7936 | 0.9819 | infra beta 明显 |
| GMX / UMA | defi_yield | 0.8985 | 0.7317 | 0.9566 | DeFi 方向新增候选 |
| LPT / VIRTUAL | ai_data | 0.8654 | 0.7113 | 0.9512 | AI/data 方向新增候选 |

这些 pair 是下一轮 persistence 验证的重点，而不是已经确认的核心池。

## 可视化解读

本轮对 `BERA / IP` 做了三类可视化：normalized price、ratio、hedge-adjusted spread / z-score。

### Normalized price

将 BERA 和 IP 过去 30 天 1h close price 都归一化到 100。

观察结果：

- 两者整体同向下跌；
- BERA 约 -35.9%，IP 约 -45.7%；
- 两者存在共同 beta，但 IP 更弱。

这个图主要用于 sanity check：确认 pair 是否大体同涨同跌，以及是否存在明显结构性脱钩。

### Ratio

ratio = BERA price / IP price。

观察结果：

- 6 月 8 日后 ratio 明显上升；
- BERA 相对 IP 变强；
- 如果后续 ratio 回落，说明相对关系开始收敛。

Ratio 图比简单叠加两条价格线更接近 pair trading 的研究对象，因为 pair trading 交易的是相对强弱。

### Hedge-adjusted spread / z-score

当前使用：

```text
spread = log(BERA) - alpha - beta * log(IP)
```

其中 beta 约为 0.8835。

观察结果：

- 当前 spread 偏高；
- 最新 z-score 约 1.35；
- 尚未达到 ±2 的研究警戒区。

因此，当前状态可以理解为：pair 关系仍在，spread 有一定偏离，但还没有进入更强的信号研究区间。

## 与会议讨论的对应关系

| 会议讨论点 | 当前进展 | 说明 |
|---|---|---|
| 小币 pair trading 包含做多和做空 | 已纳入框架 | 通过 hedge beta 和 spread 定义，为后续多空组合准备输入 |
| Track whale 需要链上数据，当前不可得 | 暂未作为依赖 | 本阶段不依赖 whale/on-chain；后续可接 Dune 或其他链上数据 |
| 选币篮子更新速度快，考虑天级更新 | 已有基础机制 | persistence 和 latest pool 已完成，后续需要定时任务 |
| 选赛道 | 已实现 | 使用 group-first universe |
| BTC/ETH 对小币影响 | 尚未接入 | 后续应加入 BTC/ETH regime filter |
| 历史回归 pattern 不足，外部影响大 | 已部分处理 | 使用 rolling corr、beta drift、persistence 降低单次回归依赖 |
| 区分赛道，在候选币种里挑 pair | 已实现 | 默认只在组内计算 pair |
| 满足统计性假说：稳定价差 | 已实现 | 使用 spread、half-life、convergence、z-score opportunities |
| pair 不长期固定，只在一个周期有效 | 已纳入设计 | 通过 persistence 动态升级/降级 |
| 小币看成交量 | 部分完成 | 已接 order book depth/spread；后续补 24h volume 和 OI |
| Sharpe ratio、max drawdown | 后续阶段 | 这是交易回测指标，不属于 pair finding 的第一阶段 |
| 长周期稳定性，月度 variance | 部分完成 | 当前有 30d 窗口和 rolling 指标；后续补月度稳定性报表 |
| 多空组合实现稳定性 | 后续阶段 | 当前完成 pair 发现，尚未进入组合层回测 |
| AI、多对多关系 | 后续阶段 | 当前是 pair-level；后续可研究 cluster / graph / many-to-many basket |

## 后续需要补齐的部分

当前 pair 池已经具备研究基础，但还不是完整交易策略。后续需要补齐：

- BTC/ETH regime 对小币 pair 的影响；
- market cap、FDV、24h volume、open interest、volatility bucket；
- 更长周期下的稳定性方差；
- pair-level signal backtest；
- portfolio-level long/short allocation；
- Sharpe ratio、max drawdown、turnover、capacity；
- funding 和滑点在真实交易中的动态影响；
- pair 失效后的降级和恢复机制。

## 基于当前 pair 池的 trading strategy research

下一阶段可以从 `persistent_active` 开始做 signal-level trading research。这里仍然不是直接进入实盘，而是把已经筛出的 pair 转化为可回测、可解释的多空信号。

### 信号构建

每个 pair 的基础信号来自 hedge-adjusted spread：

```text
spread = log(price_A) - alpha - beta * log(price_B)
z_score = (spread - rolling_mean(spread)) / rolling_std(spread)
```

其中 beta 来自 rolling hedge regression，不使用简单 1:1 配对。

初始研究可以采用一套朴素规则：

| 条件 | 动作 |
|---|---|
| z_score > entry_z | A 相对 B 偏贵，short A / long B |
| z_score < -entry_z | A 相对 B 偏便宜，long A / short B |
| abs(z_score) < exit_z | spread 回到正常区间，平仓 |
| abs(z_score) > stop_z | 关系可能失效，止损或强制退出 |
| holding_hours > max_hold | 回归太慢，时间止损 |

这套规则的作用不是直接寻找最终参数，而是建立一条清晰的研究基线。后续再比较不同 z-window、entry_z、exit_z、stop_z 和 max_hold 是否稳定。

### 仓位与对冲

仓位不应按名义金额 1:1 简单配置，而应按 hedge beta 调整。

例如 `BERA / IP` 的 beta 约为 0.8835，说明 BERA 和 IP 的相对波动比例不是 1:1。后续策略中应使用 beta-adjusted notional，让组合尽量暴露在 spread 上，而不是暴露在单边小币 beta 上。

初始研究建议保持小规模、固定风险预算：

- 每个 pair 独立限制最大仓位；
- 每条腿使用 beta-adjusted notional；
- 限制单个币重复出现在多个 pair 中造成的隐性集中度；
- funding、手续费、滑点和盘口 spread 全部计入回测成本。

### 回测评估

pair-level 回测至少需要输出：

| 指标 | 目的 |
|---|---|
| trade count | 判断机会数量是否足够 |
| win rate | 判断收敛交易是否有效 |
| average pnl / median pnl | 判断收益是否被少数异常值驱动 |
| Sharpe ratio | 评估收益稳定性 |
| max drawdown | 评估极端回撤 |
| average holding time | 判断资金占用 |
| turnover | 判断交易频率和成本敏感性 |
| funding drag | 判断多空资金费率影响 |
| capacity | 结合 order book depth 评估可交易规模 |

只有 pair-level 结果稳定之后，才进入 portfolio-level long/short allocation。

### 组合层研究

组合层需要解决多个 pair 同时交易的问题：

- 一个币可能出现在多个 pair 中，例如 IP 同时出现在 `BERA / IP` 和 `AUCTION / IP`；
- 多个 pair 可能本质上暴露在同一赛道 beta 上；
- 同一时间多个 spread 同向偏离时，组合可能并不市场中性；
- 需要限制 gross exposure、net exposure、单币 concentration 和赛道 concentration。

因此，组合层不应简单把所有 pair 信号相加，而应先做 exposure aggregation，再决定实际下单权重。

## Pair Pool 动态更新机制

Pair 池需要持续刷新。小币关系不应被视为长期固定资产，而是一个会随市场状态、叙事、流动性和资金费率变化而迁移的研究对象。

### 更新频率

建议采用两层更新：

| 更新层级 | 频率 | 内容 |
|---|---|---|
| market data refresh | 数小时级 | 更新 OHLCV、funding、order book snapshot |
| pair pool review | 天级 | 重算 diagnostics、persistence、候选池状态 |

在波动剧烈或 BTC/ETH regime 切换时，可以临时提高刷新频率。

### 状态迁移

候选池采用状态机维护：

| 当前状态 | 触发条件 | 下一状态 |
|---|---|---|
| new_candidate | 连续多次 passing，且 pass_rate 达标 | persistent_watchlist 或 persistent_active |
| persistent_watchlist | 分数和 stability 提升，连续 active | persistent_active |
| persistent_active | rolling corr 破坏、beta drift、弱收敛、流动性恶化 | persistent_watchlist 或 rejected_or_decayed |
| rejected_or_decayed | 后续重新通过多次快照 | new_candidate |

这种机制避免了两个问题：

- 只因为一次快照好看就加入核心池；
- 关系失效后仍然留在交易研究池里。

### 升级规则

一个 pair 从 `new_candidate` 升级，应至少满足：

- 多次快照通过；
- pass_rate 不低于最低要求；
- rolling correlation 没有明显衰退；
- hedge beta 没有大幅漂移；
- convergence rate 仍然有效；
- funding drag 没有恶化；
- order book spread 和 25bps depth 仍然可交易。

`persistent_active` 不应只看 score，而应同时看 stability 和 liquidity。

### 降级规则

一个 pair 应被降级或隔离，如果出现：

- rolling correlation 跌破阈值；
- beta CV 或 beta drift 明显升高；
- half-life 过长，spread 回归太慢；
- z-score 机会减少或不再收敛；
- pair spread 变宽；
- 25bps depth 低于要求；
- funding diff 持续侵蚀收益；
- BTC/ETH regime 切换后关系失效。

降级不是删除。被拒绝或退化的 pair 需要保留 reject reason，避免后续重复挖掘同一类噪音。

### Universe 扩展

后续扩币不应全市场无规则加入，而应继续按赛道和交易行为扩展。

扩展时建议给每个币补充标签：

- market cap bucket；
- FDV bucket；
- 24h volume bucket；
- open interest bucket；
- realized volatility bucket；
- exchange liquidity bucket；
- sector / narrative；
- BTC/ETH beta。

有了这些标签之后，可以更清楚地区分：

- micro cap pair；
- small cap pair；
- mid cap pair；
- 高波动但低容量 pair；
- 成交额较大但波动不足的 pair；
- 赛道 beta 过重的 pair。

这样 pair pool 的更新就不是简单“加币”，而是结构化地维护候选 universe。

## 阶段性判断

当前 pair finding 框架已经可以从小币永续中系统性筛选出有研究价值的 pair。

已经进入核心研究池的 pair：

- BERA / IP
- AUCTION / IP

稳定观察池：

- AUCTION / BERA
- APE / AXS
- APE / AUCTION
- AUCTION / AXS

下一轮重点观察的新候选：

- BERA / ENS
- EGLD / VANA
- AUCTION / ENS
- EGLD / IP
- GMX / UMA
- LPT / VIRTUAL

这套流程的重点不在于一次性给出固定名单，而在于持续维护 pair research pipeline：保留拒绝原因、记录快照状态、动态升级和降级候选关系。
