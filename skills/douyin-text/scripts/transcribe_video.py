#!/usr/bin/env python3
"""从抖音视频提取文字转录。依赖阿里云百炼 API（dashscope）。"""
import sys
import json
import os
from urllib import request
from http import HTTPStatus

try:
    import dashscope
except ImportError:
    print(json.dumps({"error": "请先安装 dashscope: pip install dashscope"}, ensure_ascii=False))
    sys.exit(1)

DEFAULT_MODEL = "paraformer-v2"


def transcribe(video_url: str, api_key: str, model: str = DEFAULT_MODEL) -> str:
    """通过视频直链提取文字（无需下载视频文件）。"""
    dashscope.api_key = api_key

    # 发起异步转录任务
    task_response = dashscope.audio.asr.Transcription.async_call(
        model=model,
        file_urls=[video_url],
        language_hints=["zh", "en"]
    )

    # 等待转录完成
    transcription_response = dashscope.audio.asr.Transcription.wait(
        task=task_response.output.task_id
    )

    if transcription_response.status_code != HTTPStatus.OK:
        raise Exception(f"转录失败: {transcription_response.output.message}")

    # 获取转录结果
    for transcription in transcription_response.output["results"]:
        url = transcription["transcription_url"]
        result = json.loads(request.urlopen(url).read().decode("utf8"))

        if "transcripts" in result and len(result["transcripts"]) > 0:
            return result["transcripts"][0]["text"]
        else:
            return "未识别到文本内容"

    return "未识别到文本内容"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python transcribe_video.py '<video_url>' '<api_key>' [model]")
        sys.exit(1)

    video_url = sys.argv[1]
    api_key = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_MODEL

    try:
        text = transcribe(video_url, api_key, model)
        print(json.dumps({"text": text}, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
