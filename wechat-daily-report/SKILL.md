---
name: wechat-daily-report
description: Compatibility wrapper for the root-level wechat-daily-report skill. Historical installs may still reference this path, but the real implementation now lives at the repository root.
---

# WeChat Daily Report Compatibility Wrapper

这个目录只保留兼容壳，避免历史安装路径失效。

唯一正式实现位于仓库根目录：

- `SKILL.md`
- `scripts/generate_daily_report.py`

如果你的运行环境仍引用当前目录，兼容壳会自动转发到根目录新版脚本，因此行为应与根目录保持一致。

## 依赖

```bash
python3 -m pip install -r requirements.txt
```

## 运行

历史命令仍可继续使用：

```bash
python3 scripts/generate_daily_report.py
```

但它现在会转发到根目录新版实现，不再保留旧版 AI 资讯日报 / 少数派 / OfferShow 老逻辑。

## OfferShow 配置

当前版本只使用：

```bash
export OFFERSHOW_ACCESS_TOKEN="你的 offershow token"
```

或写入 `.env`：

```bash
OFFERSHOW_ACCESS_TOKEN="你的 offershow token"
```

不再依赖额外 cookie。
