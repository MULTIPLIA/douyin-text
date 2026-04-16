---
name: wechat-daily-report
description: Use when researching or generating a WeChat-ready industry briefing. Supports two modes: collection_mode for building a near-3-day candidate sample pool from PingWest、少数派派早报、爱范儿日报, and report_mode for a unified-candidate-pool Top 10 daily report pipeline.
---

# WeChat Daily Report

这个 skill 现在分成两条工作线：

- `collection_mode`
  - 用于正式 PRD 之前的项目调研
  - 近 3 天抓取 `品玩实事要问`、`少数派派早报`、`爱范儿日报` 的全量候选
  - 只抓发现源页面本身及站内发现页，不展开外部原始信源
  - 同时输出：
    - 结构化清单
    - 原始抓取转储
    - 来源摸底文档

- `report_mode`
  - 走正式日报链路
  - 从 `品玩实事要问`、`少数派派早报`、`爱范儿日报` 统一混池抓候选
  - `品玩实事要问` 取前一天快讯，`少数派派早报` 和 `爱范儿日报` 取当天日报
  - 不做来源配额，按内容价值直接选出 `Top 10`
  - 对高价值但仍需回源确认的条目，使用更保守的归因表述
  - 继续生成微信群可直接发送的两段消息

## 依赖

```bash
python3 -m pip install -r requirements.txt
```

## Collection Mode

默认会把调研产物写到：

```bash
output/research/YYYY-MM-DD/
```

运行方式：

```bash
python3 scripts/generate_daily_report.py --mode collection
```

指定日期：

```bash
python3 scripts/generate_daily_report.py --mode collection --date 2026-04-16
```

指定采样窗口天数：

```bash
python3 scripts/generate_daily_report.py --mode collection --window-days 3
```

指定输出目录：

```bash
python3 scripts/generate_daily_report.py --mode collection --collection-output-dir /绝对路径/目录
```

collection_mode 会生成：

- `source_inventory.md`
- `candidate_pool.md`
- `candidate_pool.json`
- `raw_dump/`

## Report Mode

正式日报仍然直接输出两段消息，中间用 `<<<MESSAGE_BREAK>>>` 分隔：

```bash
python3 scripts/generate_daily_report.py --mode report
```

或简写：

```bash
python3 scripts/generate_daily_report.py
```

## OfferShow Token 配置

如果要抓会员态 OfferShow 岗位，运行前注入：

```bash
export OFFERSHOW_ACCESS_TOKEN="你的 offershow token"
```

或者写到项目根目录 `.env`（项目已有 .gitignore，不会提交到 GitHub）：

```bash
OFFERSHOW_ACCESS_TOKEN="你的 offershow token"
```

**Token 必须满足以下条件才能正常抓取会员岗位：**
- `is_login: true` — 账号处于登录状态
- `is_recruit_vip: true` — 账号拥有招聘会员身份

**常见报错及含义：**

| 职场速递提示 | 含义 | 解决方法 |
|------------|------|---------|
| `❌ Token 已过期` | JWT exp 时间戳已到期 | 重新获取 OFFERSHOW_ACCESS_TOKEN |
| `⚠️ Token 即将过期（剩余 N 天）` | Token 不足 2 天到期，但当前仍可继续抓取 | 尽快续期 |
| `⚠️ Token 有效，但 OfferShow API 返回账号未登录状态（is_login=false）` | 服务端认为未登录，脚本会降级为公开数据模式 | 确认 token 已正确配置，参考上方配置方式 |
| `⚠️ 当前账号不是招聘会员` | 账号无招聘会员权限，脚本会降级为公开数据模式 | 如需完整数据，需购买/申请招聘会员 |
| `❌ 当前 token 已失效或未登录` | Token 被服务端拒绝 | 重新获取 OFFERSHOW_ACCESS_TOKEN |
| 暂无新增投递 | 无岗位或数据源问题 | 正常提示，无需处理 |

report_mode 当前口径：

- 行业新闻使用 `品玩实事要问`、`少数派派早报`、`爱范儿日报` 统一混池
- `品玩实事要问` 取前一天，`少数派派早报` 和 `爱范儿日报` 取当天日报
- 评分只看内容本身价值，不看来源，不做来源配额
- `Top 10` 允许单一来源占多数
- 消费硬件不预先排除，先进入候选池统一评分
- 高价值但未完成回源确认的条目允许入榜，但会降级成更保守的归因表述

## 当前调研口径

collection_mode 的候选发现源固定为：

- `https://www.pingwest.com/status`
- `https://sspai.com/tag/%E6%B4%BE%E6%97%A9%E6%8A%A5`
- `https://www.ifanr.com/category/ifanrnews`

当前阶段不预设"消费硬件发布"必须排除，先保留进样本池，待人工审样后再定规则。
