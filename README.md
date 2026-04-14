# Claude Skills 仓库

个人 Claude Code / OpenClaw 技能集合，包含多个实用技能。

## 技能列表

### douyin-text
处理抖音分享链接，支持无水印下载链接解析和视频文字转录。

| 功能 | 脚本 | 是否需要 API Key |
|------|------|----------------|
| 解析视频标题、ID | `parse_video.py` | 否 |
| 获取无水印下载链接 | `parse_video.py` | 否 |
| 提取视频文案 / 字幕 | `transcribe_video.py` | **是**（阿里云百炼）|

**触发词：** 抖音、douyin、分享链接、无水印、下载视频、视频文案、转录

### feishu-doc-maker
创建和更新飞书云文档，将内容转换为符合飞书格式规范的文档。

**触发词：** 帮我写/创建一篇飞书文档、生成文档、写一个 xxx 文档、帮我制作飞书文档

**核心功能：**
- 从零创建新的飞书文档
- 根据现有内容生成文档
- 修改、更新现有文档
- 支持 Callout、分栏、表格、Mermaid 图表等丰富格式

## 安装

### 作为 Claude Code / OpenClaw Skill 使用

**方式一：直接复制 SKILL.md**

将 `skills/<skill-name>/SKILL.md` 复制到 `~/.claude/skills/` 目录。

**方式二：整仓库安装**

```bash
# 克隆仓库
git clone https://github.com/MULTIPLIA/dylan_skill.git
cd dylan_skill

# 安装所有技能
cp -r skills/* ~/.claude/skills/
```

## 目录结构

```
dylan_skill/
├── README.md
├── SKILL.md                    # douyin-text 技能（向后兼容）
├── skills/
│   ├── douyin-text/
│   │   └── SKILL.md
│   └── feishu-doc-maker/
│       ├── SKILL.md
│       └── references/
│           └── lark-markdown.md
└── scripts/
    ├── parse_video.py
    └── transcribe_video.py
```

## License

MIT
