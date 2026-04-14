---
name: douyin-text
description: >
  处理抖音分享链接的技能。支持：(1) 获取无水印下载链接，(2) 解析视频基本信息（标题、视频ID），
  (3) 提取视频文字转录/文案。当用户分享抖音链接、要求下载无水印视频、或想获取视频文案/字幕/文字内容时触发。
  无需 MCP，通过内置 Python 脚本直接调用 API。
---

# Douyin Text

通过内置脚本处理抖音分享链接，无需任何外部 MCP 或服务器配置。

## 依赖

- `requests`（解析链接和获取下载链接）
- `dashscope`（文字转录，pip install dashscope）
- 无需 ffmpeg，直接通过视频直链转录

首次使用转录功能前，确认 `dashscope` 已安装：
```bash
pip install dashscope
```

## 工具选择

| 用户需求 | 脚本 | 需要 API_KEY |
|---------|------|------------|
| 视频标题、ID、基本信息 | `parse_video.py` | 否 |
| 无水印下载链接 | `parse_video.py` | 否 |
| 视频文案 / 字幕 / 转录文字 | `transcribe_video.py` | **是**（阿里云百炼）|

## 执行流程

1. 从用户消息中识别抖音分享链接（支持完整链接或包含链接的分享文本）
2. 调用 `parse_video.py` 解析链接，获取视频信息和下载链接
3. 若需要转录，用 `transcribe_video.py` 对视频 URL 进行文字提取
4. 转录完成后执行文本清理（见下方）

## 脚本用法

**解析视频（无需 API Key）：**
```bash
SKILL_DIR=~/.claude/skills/douyin-text
python3 "$SKILL_DIR/scripts/parse_video.py" '<抖音分享文本或链接>'
```
输出：`{"video_id": "...", "title": "...", "download_url": "...", "share_url": "..."}`

**转录视频（需要阿里云百炼 API Key）：**
```bash
SKILL_DIR=~/.claude/skills/douyin-text
python3 "$SKILL_DIR/scripts/transcribe_video.py" '<video_url>' '<api_key>' [model]
```
输出：`{"text": "转录内容..."}`

**注意**：转录用视频直链（`download_url`），不是分享链接。

## 转录文本清理

转录成功后，返回前做一次可读性优化：

- 修正明显 ASR 识别错误和断句问题
- 删除重复语气词（如"那个那个"、"就是就是"）
- 补全缺失标点和句子边界
- **保留原意、语气和关键信息，不改写、不总结**
- 数字、名称、事实类信息保守处理，有歧义时保留原文

返回格式：
- 默认返回**整理后文案**
- 若转录置信度较低，额外附上`原始转写摘录`（前 200 字）
