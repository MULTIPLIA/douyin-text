# Claude Skills 仓库

个人 Claude Code / OpenClaw 技能集合。

这个仓库现在统一采用一种结构：

- 仓库根目录只放仓库级文件
- 每个 skill 的 `SKILL.md`、脚本、测试、依赖、参考文档都放在 `skills/<skill-name>/`
- 不再在根目录保留某个 skill 的第二套实现，避免分叉和误装

## 技能列表

### `douyin-text`
处理抖音分享链接，支持无水印下载链接解析和视频文字转录。

| 功能 | 脚本 | 是否需要 API Key |
|------|------|----------------|
| 解析视频标题、ID | `parse_video.py` | 否 |
| 获取无水印下载链接 | `parse_video.py` | 否 |
| 提取视频文案 / 字幕 | `transcribe_video.py` | 是（阿里云百炼） |

触发词：抖音、douyin、分享链接、无水印、下载视频、视频文案、转录

### `feishu-doc-maker`
创建和更新飞书云文档，将内容转换为符合飞书格式规范的文档。

核心功能：
- 从零创建新的飞书文档
- 根据现有内容生成文档
- 修改、更新现有文档
- 支持 Callout、分栏、表格、Mermaid 图表等丰富格式

### `wechat-daily-report`
生成微信群可直接发送的智能日报，包含行业新闻和 OfferShow 职场速递。

核心特性：
- `collection_mode`：抓取候选发现源，输出样本池与原始转储
- `report_mode`：从统一候选池里选出 Top 10 行业新闻
- 支持 `OFFERSHOW_ACCESS_TOKEN` 会员态岗位抓取
- 输出两段消息，兼容微信发送场景

## 安装

### 作为 Claude Code / OpenClaw Skill 使用

方式一：复制单个 skill

```bash
cp -r skills/<skill-name> ~/.claude/skills/
```

方式二：整仓库安装

```bash
git clone https://github.com/MULTIPLIA/dylan_skill.git
cd dylan_skill
cp -r skills/* ~/.claude/skills/
```

## 目录结构

```text
dylan_skill/
├── README.md
└── skills/
    ├── douyin-text/
    │   ├── SKILL.md
    │   └── scripts/
    ├── feishu-doc-maker/
    │   ├── SKILL.md
    │   └── references/
    └── wechat-daily-report/
        ├── SKILL.md
        ├── PRD.md
        ├── requirements.txt
        ├── scripts/
        └── tests/
```

## License

MIT
