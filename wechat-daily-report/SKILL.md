---
name: wechat-daily-report
description: Use when generating a daily WeChat-ready briefing from AI news, broad internet events, and recent job opportunities, especially when the workflow must auto-discover the latest source pages and output a Chinese report that can be posted directly into a group chat.
---

# WeChat Daily Report

生成一个偏职场认知的微信群日报，固定覆盖三块内容：

- AI 新闻里最值得职场人关注的 5 条
- 互联网综合事件里最值得职场人关注的 5 条
- OfferShow 近 5 日内、互联网/广告方向的 5 个岗位推荐

## 入口

先安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

直接生成当天日报。脚本会直接在终端输出两段可发送正文：

```bash
python3 scripts/generate_daily_report.py
```

指定日期重跑：

```bash
python3 scripts/generate_daily_report.py --date 2026-04-14
```

## 默认抓取逻辑

1. `AI资讯日报`：按日期 URL 从当天向前回退，直到找到最新可用页面。
2. `少数派`：从首页发现最新一篇 `派早报`，再提取每个小节的正文。
3. `OfferShow`：调用公开接口读取最新招聘表，并筛选互联网/广告两个方向、最近 5 日的岗位。

## 输出风格

- 面向微信群，默认输出短段落和明确动作建议
- 每条都保留原始来源链接
- “认知”不是复述新闻，而是把新闻转成职场判断

## 自动化建议

如果要挂到定时任务，直接每天运行并把 stdout 接给后续发送器：

```bash
python3 /绝对路径/wechat-daily-report-skill/scripts/generate_daily_report.py
```

后续若要接企业微信或微信群机器人，优先直接消费脚本 stdout，不再依赖本地落文件。
