#!/usr/bin/env python3
"""解析抖音分享链接，获取视频信息和下载链接。无需 API Key。"""
import re
import json
import sys
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
}


def parse_share_url(share_text: str) -> dict:
    """从分享文本中提取无水印视频链接"""
    urls = re.findall(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+',
        share_text
    )
    if not urls:
        raise ValueError("未找到有效的分享链接")

    share_url = urls[0]
    share_response = requests.get(share_url, headers=HEADERS, allow_redirects=True)
    video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
    share_url = f'https://www.iesdouyin.com/share/video/{video_id}'

    response = requests.get(share_url, headers=HEADERS)
    response.raise_for_status()

    pattern = re.compile(r'window\._ROUTER_DATA\s*=\s*(.*?)</script>', re.DOTALL)
    find_res = pattern.search(response.text)
    if not find_res or not find_res.group(1):
        raise ValueError("无法从页面解析视频信息，可能视频已下架或链接无效")

    json_data = json.loads(find_res.group(1).strip())

    video_page_key = "video_(id)/page"
    note_page_key = "note_(id)/page"

    if video_page_key in json_data["loaderData"]:
        original_video_info = json_data["loaderData"][video_page_key]["videoInfoRes"]
    elif note_page_key in json_data["loaderData"]:
        original_video_info = json_data["loaderData"][note_page_key]["videoInfoRes"]
    else:
        raise Exception("无法识别视频类型（非视频或图集内容）")

    data = original_video_info["item_list"][0]
    video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
    desc = data.get("desc", "").strip() or f"douyin_{video_id}"

    return {
        "video_id": video_id,
        "title": desc,
        "download_url": video_url,
        "share_url": share_url,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python parse_video.py '<抖音分享文本或链接>'")
        sys.exit(1)

    share_text = " ".join(sys.argv[1:])
    try:
        result = parse_share_url(share_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
