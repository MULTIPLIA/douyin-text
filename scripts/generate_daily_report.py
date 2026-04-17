from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time as time_module
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


SHANGHAI = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
PINGWEST_STATUS_URL = "https://www.pingwest.com/status"
PINGWEST_STATUS_API_URL = "https://www.pingwest.com/api/state/list"
SSPAI_MORNING_PAPER_TAG_URL = "https://sspai.com/tag/%E6%B4%BE%E6%97%A9%E6%8A%A5"
IFANR_NEWS_CATEGORY_URL = "https://www.ifanr.com/category/ifanrnews"

SOURCE_SUFFIX_RE = re.compile(r"\((?:AI资讯|AI资讯日报)\)")
KAOMOJI_RE = re.compile(
    r"ᕦ\([^)]*\)ᕤ|¯\\?\s*\(ツ\)\s*/¯|\([^)]*[•ᴗ́̀ㅂ][^)]*\)و|T[_＿]T"
)
SLASH_NOISE_RE = re.compile(r"(?<=\s)[\\/](?:\s+[\\/])+")
CJK_CHAR_RE = r"[\u4e00-\u9fff]"
CJK_PUNCT_RE = r"[，。！？；：、】【（）《》、]"
STDOUT_MESSAGE_BREAK = "<<<MESSAGE_BREAK>>>"
TARGET_OFFER_TAG_IDS = {4, 9, 19, 12}
OFFERSHOW_AUTH_ERROR_PREFIX = "offershow_auth:"
OFFERSHOW_TOKEN_EXPIRY_WARN_DAYS = 2


class OfferShowError(Exception):
    """OfferShow 相关错误的基类。"""
    pass


class OfferShowTokenMissing(OfferShowError):
    """未配置 access token。"""
    pass


class OfferShowTokenExpired(OfferShowError):
    """Token 已过期。"""
    pass


class OfferShowTokenExpiringSoon(OfferShowError):
    """Token 即将过期（剩余天数不足）。"""
    pass


class OfferShowNotVip(OfferShowError):
    """当前账号不是招聘会员。"""
    pass


class OfferShowAuthFailed(OfferShowError):
    """Token 失效或未登录。"""
    pass


class OfferShowApiError(OfferShowError):
    """OfferShow API 请求失败。"""
    pass


def _parse_jwt_exp(token: str) -> int | None:
    """从 JWT token 中提取 exp 时间戳。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except Exception:
        return None


def check_offershow_token_expiry(token: str) -> OfferShowError | None:
    """检查 token 是否缺失、即将过期或已过期。返回具体错误类型或 None。"""
    if not token:
        return OfferShowTokenMissing(
            "未配置 OFFERSHOW_ACCESS_TOKEN，请在环境变量或 .env 中设置。"
        )
    exp_ts = _parse_jwt_exp(token)
    if exp_ts is None:
        return None
    now_ts = time_module.time()
    if now_ts > exp_ts:
        return OfferShowTokenExpired(
            f"OFFERSHOW_ACCESS_TOKEN 已于 {datetime.fromtimestamp(exp_ts, tz=SHANGHAI):%Y-%m-%d %H:%M} 到期，请重新获取。"
        )
    expiry_date = datetime.fromtimestamp(exp_ts, tz=SHANGHAI).date()
    warn_date = date.today() + timedelta(days=OFFERSHOW_TOKEN_EXPIRY_WARN_DAYS)
    if expiry_date <= warn_date:
        remaining = (expiry_date - date.today()).days
        return OfferShowTokenExpiringSoon(
            f"OFFERSHOW_ACCESS_TOKEN 将在 {expiry_date:%Y-%m-%d} 到期（剩余 {remaining} 天），请尽快续期。"
        )
    return None
WORKPLACE_SIGNAL_KEYWORDS = (
    "Agent",
    "智能体",
    "企业",
    "办公",
    "飞书",
    "钉钉",
    "微信",
    "视频号",
    "待办",
    "浏览器",
    "控制",
    "开源",
    "代码",
    "开发",
    "职场",
    "工人",
    "工作",
    "模型",
    "监管",
    "安全",
    "漏洞",
    "后门",
    "合规",
    "平台",
    "合作",
    "电脑操作",
)
LOW_SIGNAL_PATTERNS = (
    "DOA",
    "继上次讨论后",
    "门槛是否改变",
    "再起争议",
    "单纯 CRUD",
    "线下服务行业",
    "扎克伯格",
    "分身",
    "3D 影像",
    "虚拟扎克伯格",
    "电视",
    "显示器",
    "音频外设",
    "Micro RGB",
    "X2D",
    "Inzone",
    "Surface 全线产品定价",
)
HYPE_PATTERNS = ("项目突破万星", "登顶", "天价估值", "必修课", "角色编排")
VALUE_WORKPLACE_WEIGHTS = {
    "工作流": 2.0,
    "职场": 2.0,
    "办公": 2.0,
    "企业": 1.8,
    "工具": 1.2,
    "平台": 1.2,
    "规则": 1.8,
    "监管": 2.0,
    "整治": 1.8,
    "广告": 1.4,
    "策略": 1.4,
    "浏览器": 1.8,
    "插件": 1.0,
    "微信": 1.8,
    "视频号": 1.4,
    "飞书": 1.8,
    "钉钉": 1.8,
    "OpenAI": 0.8,
    "ChatGPT": 1.0,
    "Copilot": 1.0,
    "Siri": 0.8,
    "Google": 0.6,
    "微软": 1.2,
    "苹果": 0.6,
    "智能体": 1.0,
    "Agent": 1.0,
    "AI": 0.2,
    "训练营": 0.8,
    "安全": 2.4,
    "漏洞": 2.8,
    "后门": 2.8,
    "风险": 1.4,
    "iOS": 1.4,
    "桌面应用": 1.6,
    "Chrome": 1.2,
    "Skills": 1.0,
}
VALUE_NEW_INFO_WEIGHTS = {
    "发布": 1.6,
    "上线": 1.6,
    "推出": 1.6,
    "宣布": 1.4,
    "更新": 1.4,
    "开源": 1.4,
    "测试": 1.4,
    "计划": 1.2,
    "开售": 1.1,
    "整治": 1.8,
    "定性": 1.6,
    "被曝": 1.6,
    "发现": 1.2,
    "下架": 1.5,
    "训练营": 1.0,
    "广告策略": 1.4,
}
VALUE_DISTRIBUTION_WEIGHTS = {
    "微信": 1.2,
    "浏览器": 1.6,
    "广告": 1.0,
    "规则": 1.4,
    "平台": 1.0,
    "安全": 1.6,
    "漏洞": 1.8,
    "后门": 1.8,
    "工具": 0.8,
    "眼镜": 0.5,
    "开售": 0.4,
    "桌面应用": 1.2,
    "插件": 1.2,
    "iOS": 1.0,
}
READABILITY_BAD_PATTERNS = (
    "基准",
    "流水线",
    "具身模型",
    "夺冠",
    "百倍",
    "SOTA",
    "物理直觉",
)
LOW_SIGNAL_RESEARCH_PATTERNS = (
    "MMLab",
    "世界模型",
    "手眼",
    "第一人称交互视频",
    "弱到强监督",
    "物理AI",
)
REPORTING_DETAIL_MARKERS = (
    "发布",
    "宣布",
    "上线",
    "更新",
    "整治",
    "影响",
    "支持",
    "开放",
    "试点",
    "计划",
    "推出",
    "月",
    "日",
    "%",
    "：",
    ":",
    "，",
    "。",
)
BACKCHECK_ATTRIBUTION_PATTERNS = (
    "据报道",
    "消息称",
    "The Information",
    "外媒",
    "被曝",
    "传出",
    "发现源称",
)
BACKCHECK_HIGH_IMPACT_KEYWORDS = (
    "OpenAI",
    "ChatGPT",
    "微软",
    "Copilot",
    "苹果",
    "Siri",
    "Google",
    "广告",
    "规则",
    "策略",
    "训练营",
)
EVENT_SPECIAL_TERMS = (
    "MiniMax",
    "返回键劫持",
    "广告策略",
    "训练营",
    "网络安全",
    "后门",
    "漏洞",
    "智能体",
    "AI 眼镜",
    "企业微信",
    "视频号",
    "浏览器",
    "插件",
    "WordPress",
    "Copilot",
    "OpenClaw",
    "GPT-5.4-Cyber",
    "ChatGPT",
    "OpenAI",
    "Siri",
    "Google",
    "微软",
    "苹果",
)
KNOWN_NEWS_ENTITIES = (
    "MiniMax",
    "OpenAI",
    "ChatGPT",
    "Google",
    "微软",
    "Copilot",
    "苹果",
    "Siri",
    "Adobe",
    "Meta",
    "百度",
    "荣耀",
    "阿里",
    "Gemini",
    "WordPress",
)
EVENT_FAMILY_PATTERNS = {
    "desktop_agent_update": (
        "桌面端",
        "操作电脑",
        "Computer Use",
        "Pocket 功能",
        "Pocket",
        "直接操作电脑",
        "图形界面",
        "智能体更新",
        "Agent 更新",
    ),
    "security_model": (
        "网络安全",
        "GPT-5.4-Cyber",
        "可信访问",
        "防御",
    ),
    "ad_strategy": (
        "广告策略",
        "按点击付费",
        "CPC",
        "行动转化",
    ),
    "search_rule": (
        "返回键劫持",
        "恶意行为",
        "专项整治",
        "搜索排名",
    ),
    "training_camp": (
        "训练营",
        "编程训练营",
        "辅助编写代码",
    ),
}
GENERIC_AI_LAUNCH_PATTERNS = (
    "文生图模型",
    "文生图技术",
    "AI计算加速器",
    "世界模型",
    "电影节",
    "8B参数",
    "开源8B",
    "多代定制AI处理器",
)
HIGH_SIGNAL_CONTEXT_PATTERNS = (
    "安全",
    "漏洞",
    "后门",
    "攻击",
    "整治",
    "规则",
    "浏览器",
    "桌面应用",
    "插件",
    "广告策略",
    "工作流",
    "办公",
    "企业",
    "微信",
    "飞书",
    "钉钉",
    "iOS",
)


@dataclass
class ArticleLink:
    title: str
    url: str


@dataclass
class NewsItem:
    track: str
    section: str
    title: str
    summary: str
    source_url: str
    source_name: str


@dataclass
class DiscoveryCandidate:
    source_name: str
    source_date: date
    title: str
    summary_or_excerpt: str
    url: str
    raw_section: str
    raw_order: int
    notes: str = ""


@dataclass
class RawDocument:
    source_name: str
    document_label: str
    url: str
    content: str


@dataclass
class SourceCollectionResult:
    source_name: str
    entry_url: str
    fetch_status: str
    candidates: list[DiscoveryCandidate]
    raw_documents: list[RawDocument]
    error: str | None = None


@dataclass
class OfferRecommendation:
    company_name: str
    industry: str
    created_at: datetime
    title: str
    positions: str
    city: str
    source_url: str
    score: float


@dataclass
class OfferFetchResult:
    tag_map: dict[int, str]
    plans: list[dict]
    latest_public_date: date | None
    degraded_reason: str | None = None  # 非致命原因，记录但不抛异常
    auth_warning: str | None = None


@dataclass
class RankedNewsCandidate:
    source_name: str
    source_date: date
    title: str
    summary_or_excerpt: str
    url: str
    raw_section: str
    raw_order: int
    notes: str
    dedupe_key: str
    workplace_relevance: float
    new_information_value: float
    distribution_fit: float
    readability_ok: bool
    needs_backcheck: bool
    value_score: float
    render_mode: str


def parse_dotenv_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_offershow_env(name: str) -> str:
    direct = os.getenv(name, "").strip()
    if direct:
        return direct
    for dotenv_path in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        value = parse_dotenv_file(dotenv_path).get(name, "").strip()
        if value:
            return value
    return ""


def resolve_offershow_token() -> str:
    return resolve_offershow_env("OFFERSHOW_ACCESS_TOKEN")


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://offershow.cn",
            "Referer": "https://offershow.cn/jobs/offershow_vip_table",
        }
    )
    offershow_token = resolve_offershow_token()
    if offershow_token:
        session.headers.update({"accesstoken": offershow_token})
    return session


def clean_text(text: str) -> str:
    text = SOURCE_SUFFIX_RE.sub("", text)
    text = KAOMOJI_RE.sub("", text)
    text = SLASH_NOISE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    text = re.sub(r"\s*([，。！？；：、】【（）《》、])\s*", r"\1", text)
    text = re.sub(fr"(?<={CJK_CHAR_RE})\s+(?={CJK_CHAR_RE})", "", text)
    text = re.sub(r"(\d)\s+月", r"\1月", text)
    text = re.sub(r"月\s+(\d)", r"月\1", text)
    text = re.sub(r"(\d)\s+日", r"\1日", text)
    text = re.sub(r"(\d)\s+年", r"\1年", text)
    return text


def normalize_offer_positions(text: str) -> str:
    normalized = (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    parts: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("【") and line.endswith("】"):
            continue
        if line.startswith("*"):
            continue
        line = re.sub(r"^[🔹▪•·\-\s]+", "", line).strip()
        if not line:
            continue
        for chunk in re.split(r"[、，；;]+", line):
            piece = clean_text(chunk)
            if piece:
                parts.append(piece)

    deduped_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped_parts.append(part)
    return "、".join(deduped_parts)


def markdown_slug(text: str) -> str:
    known = {
        "品玩实事要问": "pingwest-status",
        "少数派派早报": "sspai-morning-paper",
        "爱范儿日报": "ifanr-daily",
    }
    if text in known:
        return known[text]
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "source"


def dump_text_from_html(html: str) -> str:
    return clean_text(BeautifulSoup(html, "html.parser").get_text("\n", strip=True))


def extract_iso_datetime(html: str) -> datetime | None:
    match = re.search(r'datePublished":"([^"]+)"', html)
    if not match:
        return None
    return datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).astimezone(SHANGHAI)


def parse_ifanr_timestamp(raw_value: str) -> datetime | None:
    if not raw_value or not raw_value.isdigit():
        return None
    return datetime.fromtimestamp(int(raw_value), tz=SHANGHAI)


def append_candidate(
    bucket: list[DiscoveryCandidate],
    *,
    source_name: str,
    source_date: date,
    title: str,
    summary_or_excerpt: str,
    url: str,
    raw_section: str,
    raw_order: int,
) -> None:
    bucket.append(
        DiscoveryCandidate(
            source_name=source_name,
            source_date=source_date,
            title=clean_text(title),
            summary_or_excerpt=clean_text(summary_or_excerpt),
            url=url,
            raw_section=clean_text(raw_section),
            raw_order=raw_order,
        )
    )


def extract_pingwest_status_candidates(fragment_html: str) -> tuple[list[DiscoveryCandidate], str | None]:
    soup = BeautifulSoup(f"<div>{fragment_html}</div>", "html.parser")
    root = soup.div
    current_date: date | None = None
    candidates: list[DiscoveryCandidate] = []
    last_id: str | None = None
    raw_order = 0

    for child in root.find_all("section", recursive=False):
        classes = child.get("class", [])
        if "date-wrap" in classes:
            raw_date = child.get("data-d")
            if raw_date:
                current_date = date.fromisoformat(raw_date)
            continue
        if "item" not in classes or current_date is None:
            continue

        raw_order += 1
        last_id = child.get("data-id") or last_id
        title_anchor = child.select_one("p.title a[href]")
        if title_anchor is None:
            continue
        description_anchor = child.select_one("p.description a[href]")
        tag = child.select_one(".item-tag-list .tag span")
        append_candidate(
            candidates,
            source_name="品玩实事要问",
            source_date=current_date,
            title=title_anchor.get_text(" ", strip=True),
            summary_or_excerpt=(
                description_anchor.get_text(" ", strip=True)
                if description_anchor is not None
                else title_anchor.get_text(" ", strip=True)
            ),
            url=urljoin(PINGWEST_STATUS_URL, title_anchor["href"]),
            raw_section=tag.get_text(" ", strip=True) if tag is not None else "实时要闻",
            raw_order=raw_order,
        )

    return candidates, last_id


def extract_ifanr_article_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    for order, card in enumerate(soup.select("div.article-item.article-item--list"), start=1):
        label = card.select_one("a.article-label")
        title_anchor = card.select_one("h3 a[href]")
        summary = card.select_one(".article-summary")
        time_node = card.select_one(".article-meta time[data-timestamp]")
        if title_anchor is None or label is None:
            continue
        published_at = parse_ifanr_timestamp(time_node.get("data-timestamp", "")) if time_node else None
        cards.append(
            {
                "title": clean_text(title_anchor.get_text(" ", strip=True)),
                "summary": clean_text(summary.get_text(" ", strip=True)) if summary else "",
                "url": urljoin(IFANR_NEWS_CATEGORY_URL, title_anchor["href"]),
                "section": clean_text(label.get_text(" ", strip=True)),
                "published_at": published_at,
                "raw_order": order,
            }
        )
    return cards


def parse_ifanr_article(html: str, page_url: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup
    items: list[NewsItem] = []
    current_title: str | None = None
    current_parts: list[str] = []

    def flush_current() -> None:
        nonlocal current_title, current_parts
        if current_title and current_parts:
            items.append(
                NewsItem(
                    track="general",
                    section="爱范儿早报",
                    title=current_title,
                    summary=" ".join(current_parts[:3]),
                    source_url=page_url,
                    source_name="爱范儿日报",
                )
            )
        current_title = None
        current_parts = []

    for node in article.find_all(["h3", "p", "ul"]):
        if node.name == "h3":
            flush_current()
            current_title = clean_text(node.get_text(" ", strip=True))
            continue

        if current_title is None:
            continue

        if node.name == "p":
            text = clean_text(node.get_text(" ", strip=True))
            if not text or re.fullmatch(r"[^\w\u4e00-\u9fff]+", text):
                continue
            current_parts.append(text)
            continue

        bullet_items = [
            clean_text(li.get_text(" ", strip=True))
            for li in node.find_all("li")
            if clean_text(li.get_text(" ", strip=True))
        ]
        current_parts.extend(bullet_items[:3])

    flush_current()
    return items


def first_sentence(text: str) -> str:
    parts = re.split(r"[。！？!?\n]", clean_text(text))
    return next((part.strip() for part in parts if part.strip()), clean_text(text))


def parse_hubtoday_article(html: str, page_url: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.content")
    if content is None:
        raise ValueError("hubtoday page content not found")

    items: list[NewsItem] = []
    for heading in content.find_all("h3"):
        section = clean_text(heading.get_text(" ", strip=True))
        sibling = heading.find_next_sibling()
        while sibling is not None and sibling.name != "ol":
            sibling = sibling.find_next_sibling()
        if sibling is None:
            continue

        for li in sibling.find_all("li", recursive=False):
            text = clean_text(li.get_text(" ", strip=True))
            if not text:
                continue
            links = [a for a in li.find_all("a", href=True)]
            link = urljoin(page_url, links[0]["href"]) if links else page_url
            items.append(
                NewsItem(
                    track="ai",
                    section=section,
                    title=first_sentence(text),
                    summary=text,
                    source_url=link,
                    source_name="AI资讯日报",
                )
            )
    return items


def extract_sspai_paper_links(home_html: str, base_url: str) -> list[ArticleLink]:
    soup = BeautifulSoup(home_html, "html.parser")
    seen: set[str] = set()
    links: list[ArticleLink] = []
    for anchor in soup.find_all("a", href=True):
        title = clean_text(anchor.get_text(" ", strip=True))
        if "派早报" not in title:
            continue
        url = urljoin(base_url, anchor["href"])
        if url in seen:
            continue
        seen.add(url)
        links.append(ArticleLink(title=title, url=url))

    links.sort(key=lambda item: extract_post_id(item.url), reverse=True)
    return links


def extract_post_id(url: str) -> int:
    match = re.search(r"/post/(\d+)", url)
    return int(match.group(1)) if match else 0


def parse_sspai_article(html: str, page_url: str) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup

    items: list[NewsItem] = []
    current_title: str | None = None
    current_paragraphs: list[str] = []

    def flush_current() -> None:
        nonlocal current_title, current_paragraphs
        if current_title and current_paragraphs:
            items.append(
                NewsItem(
                    track="general",
                    section="互联网综合",
                    title=current_title,
                    summary=" ".join(current_paragraphs),
                    source_url=page_url,
                    source_name="少数派",
                )
            )
        current_title = None
        current_paragraphs = []

    for node in article.find_all(["h2", "p"]):
        if node.name == "h2":
            flush_current()
            title = clean_text(node.get_text(" ", strip=True))
            if title in {"少数派的近期动态", "你可能错过的文章", "不妨一看的简讯"}:
                current_title = None
                current_paragraphs = []
                continue
            current_title = title
            current_paragraphs = []
            continue

        if current_title:
            text = clean_text(node.get_text(" ", strip=True))
            if text and text != "来源":
                current_paragraphs.append(text)

    flush_current()
    return items


def collect_pingwest_candidates(
    session: requests.Session,
    anchor_date: date,
    *,
    lookback_days: int = 2,
) -> SourceCollectionResult:
    cutoff_date = anchor_date - timedelta(days=lookback_days)
    last_id = ""
    candidates: list[DiscoveryCandidate] = []
    raw_documents: list[RawDocument] = []
    page = 1
    seen_urls: set[str] = set()

    while True:
        response = session.get(
            PINGWEST_STATUS_API_URL,
            params={"last_id": last_id},
            headers={
                "Referer": PINGWEST_STATUS_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        fragment = payload.get("data", {}).get("list", "")
        if not fragment:
            break

        page_candidates, next_last_id = extract_pingwest_status_candidates(fragment)
        raw_documents.append(
            RawDocument(
                source_name="品玩实事要问",
                document_label=f"status-page-{page}",
                url=f"{PINGWEST_STATUS_API_URL}?last_id={last_id}",
                content=dump_text_from_html(fragment),
            )
        )

        min_page_date: date | None = None
        for candidate in page_candidates:
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            min_page_date = (
                candidate.source_date
                if min_page_date is None or candidate.source_date < min_page_date
                else min_page_date
            )
            if cutoff_date <= candidate.source_date <= anchor_date:
                candidates.append(candidate)

        if not next_last_id or (min_page_date is not None and min_page_date < cutoff_date):
            break
        last_id = next_last_id
        page += 1

    return SourceCollectionResult(
        source_name="品玩实事要问",
        entry_url=PINGWEST_STATUS_URL,
        fetch_status="ok",
        candidates=sorted(candidates, key=lambda item: (item.source_date, item.raw_order), reverse=True),
        raw_documents=raw_documents,
    )


def collect_sspai_candidates(
    session: requests.Session,
    anchor_date: date,
    *,
    lookback_days: int = 2,
) -> SourceCollectionResult:
    cutoff_date = anchor_date - timedelta(days=lookback_days)
    tag_response = session.get(SSPAI_MORNING_PAPER_TAG_URL, timeout=20)
    tag_response.raise_for_status()
    links = extract_sspai_paper_links(tag_response.text, "https://sspai.com/")

    raw_documents = [
        RawDocument(
            source_name="少数派派早报",
            document_label="tag-page",
            url=SSPAI_MORNING_PAPER_TAG_URL,
            content=dump_text_from_html(tag_response.text),
        )
    ]
    candidates: list[DiscoveryCandidate] = []

    for link in links:
        article_response = session.get(link.url, timeout=20)
        article_response.raise_for_status()
        published_at = extract_iso_datetime(article_response.text)
        if published_at is None:
            continue
        article_date = published_at.date()
        if article_date > anchor_date:
            continue
        if article_date < cutoff_date:
            break

        raw_documents.append(
            RawDocument(
                source_name="少数派派早报",
                document_label=link.title,
                url=link.url,
                content=dump_text_from_html(article_response.text),
            )
        )
        for order, item in enumerate(parse_sspai_article(article_response.text, link.url), start=1):
            append_candidate(
                candidates,
                source_name="少数派派早报",
                source_date=article_date,
                title=item.title,
                summary_or_excerpt=item.summary,
                url=link.url,
                raw_section=item.section,
                raw_order=order,
            )

    return SourceCollectionResult(
        source_name="少数派派早报",
        entry_url=SSPAI_MORNING_PAPER_TAG_URL,
        fetch_status="ok",
        candidates=candidates,
        raw_documents=raw_documents,
    )


def collect_ifanr_candidates(
    session: requests.Session,
    anchor_date: date,
    *,
    lookback_days: int = 2,
) -> SourceCollectionResult:
    cutoff_date = anchor_date - timedelta(days=lookback_days)
    category_response = session.get(IFANR_NEWS_CATEGORY_URL, timeout=20)
    category_response.raise_for_status()
    cards = extract_ifanr_article_cards(category_response.text)

    raw_documents = [
        RawDocument(
            source_name="爱范儿日报",
            document_label="category-page",
            url=IFANR_NEWS_CATEGORY_URL,
            content=dump_text_from_html(category_response.text),
        )
    ]
    candidates: list[DiscoveryCandidate] = []

    for card in cards:
        published_at = card["published_at"]
        if published_at is None:
            continue
        article_date = published_at.date()
        if article_date > anchor_date:
            continue
        if article_date < cutoff_date:
            break

        article_response = session.get(card["url"], timeout=20)
        article_response.raise_for_status()
        raw_documents.append(
            RawDocument(
                source_name="爱范儿日报",
                document_label=card["title"],
                url=card["url"],
                content=dump_text_from_html(article_response.text),
            )
        )
        for order, item in enumerate(parse_ifanr_article(article_response.text, card["url"]), start=1):
            append_candidate(
                candidates,
                source_name="爱范儿日报",
                source_date=article_date,
                title=item.title,
                summary_or_excerpt=item.summary,
                url=card["url"],
                raw_section=card["section"],
                raw_order=order,
            )

    return SourceCollectionResult(
        source_name="爱范儿日报",
        entry_url=IFANR_NEWS_CATEGORY_URL,
        fetch_status="ok",
        candidates=candidates,
        raw_documents=raw_documents,
    )


def collect_candidate_pool(
    anchor_date: date,
    *,
    lookback_days: int = 2,
) -> tuple[list[SourceCollectionResult], dict[str, str]]:
    session = build_session()
    results: list[SourceCollectionResult] = []
    source_errors: dict[str, str] = {}

    for source_name, entry_url, collector in (
        ("品玩实事要问", PINGWEST_STATUS_URL, collect_pingwest_candidates),
        ("少数派派早报", SSPAI_MORNING_PAPER_TAG_URL, collect_sspai_candidates),
        ("爱范儿日报", IFANR_NEWS_CATEGORY_URL, collect_ifanr_candidates),
    ):
        try:
            results.append(collector(session, anchor_date, lookback_days=lookback_days))
        except (requests.RequestException, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            source_errors[source_name] = str(exc)
            results.append(
                SourceCollectionResult(
                    source_name=source_name,
                    entry_url=entry_url,
                    fetch_status="error",
                    candidates=[],
                    raw_documents=[],
                    error=str(exc),
                )
            )

    return results, source_errors


def collect_report_mode_candidate_pool(
    session: requests.Session,
    report_date: date,
) -> tuple[list[SourceCollectionResult], dict[str, str], dict[str, date]]:
    source_windows = {
        "品玩实事要问": report_date - timedelta(days=1),
        "少数派派早报": report_date,
        "爱范儿日报": report_date,
    }
    results: list[SourceCollectionResult] = []
    source_errors: dict[str, str] = {}

    for source_name, entry_url, collector in (
        ("品玩实事要问", PINGWEST_STATUS_URL, collect_pingwest_candidates),
        ("少数派派早报", SSPAI_MORNING_PAPER_TAG_URL, collect_sspai_candidates),
        ("爱范儿日报", IFANR_NEWS_CATEGORY_URL, collect_ifanr_candidates),
    ):
        target_date = source_windows[source_name]
        try:
            results.append(collector(session, target_date, lookback_days=0))
        except (requests.RequestException, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            source_errors[source_name] = str(exc)
            results.append(
                SourceCollectionResult(
                    source_name=source_name,
                    entry_url=entry_url,
                    fetch_status="error",
                    candidates=[],
                    raw_documents=[],
                    error=str(exc),
                )
            )

    return results, source_errors, source_windows


def render_source_inventory(results: list[SourceCollectionResult], anchor_date: date, lookback_days: int) -> str:
    lines = [
        f"# 来源摸底（{anchor_date.isoformat()}）",
        "",
        f"- 样本窗口：近 {lookback_days + 1} 天",
        "- 采集策略：仅抓候选发现源及其站内发现页，不展开外部原始信源",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result.source_name}",
                "",
                f"- 入口：{result.entry_url or '抓取失败'}",
                f"- 状态：{result.fetch_status}",
                f"- 候选数：{len(result.candidates)}",
                f"- 原始转储数：{len(result.raw_documents)}",
            ]
        )
        if result.error:
            lines.append(f"- 错误：{result.error}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_candidate_pool_markdown(results: list[SourceCollectionResult], anchor_date: date, lookback_days: int) -> str:
    lines = [
        f"# 候选样本池（{anchor_date.isoformat()}）",
        "",
        f"- 时间窗口：近 {lookback_days + 1} 天",
        "- 口径：仅抓发现源页面本身及站内发现页，不展开外部原文",
        "",
    ]
    for result in results:
        lines.extend([f"## {result.source_name}", ""])
        if not result.candidates:
            lines.append("- 本来源当前无候选或抓取失败。")
            lines.append("")
            continue
        grouped = sorted(result.candidates, key=lambda item: (item.source_date, item.raw_order), reverse=True)
        for item in grouped:
            lines.extend(
                [
                    f"- [{item.source_date.isoformat()}] {item.title}",
                    f"  摘要：{item.summary_or_excerpt}",
                    f"  链接：{item.url}",
                    f"  栏目：{item.raw_section}",
                    f"  标注：{item.notes}",
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def write_collection_outputs(
    anchor_date: date,
    *,
    lookback_days: int,
    results: list[SourceCollectionResult],
    source_errors: dict[str, str],
    output_dir: Path,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dump_dir = output_dir / "raw_dump"
    raw_dump_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        source_slug = markdown_slug(result.source_name)
        raw_path = raw_dump_dir / f"{source_slug}.md"
        if not result.raw_documents:
            raw_path.write_text(f"# {result.source_name}\n\n暂无原始转储。\n", encoding="utf-8")
            continue
        parts = [f"# {result.source_name}", ""]
        for doc in result.raw_documents:
            parts.extend(
                [
                    f"## {doc.document_label}",
                    "",
                    f"- URL: {doc.url}",
                    "",
                    doc.content,
                    "",
                ]
            )
        raw_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")

    source_inventory = output_dir / "source_inventory.md"
    candidate_pool_md = output_dir / "candidate_pool.md"
    candidate_pool_json = output_dir / "candidate_pool.json"

    source_inventory.write_text(
        render_source_inventory(results, anchor_date, lookback_days),
        encoding="utf-8",
    )
    candidate_pool_md.write_text(
        render_candidate_pool_markdown(results, anchor_date, lookback_days),
        encoding="utf-8",
    )

    json_payload = {
        "collection_date": anchor_date.isoformat(),
        "lookback_days": lookback_days + 1,
        "source_errors": source_errors,
        "sources": [
            {
                "source_name": result.source_name,
                "entry_url": result.entry_url,
                "fetch_status": result.fetch_status,
                "error": result.error,
                "candidate_count": len(result.candidates),
                "raw_document_count": len(result.raw_documents),
            }
            for result in results
        ],
        "candidates": [
            {
                **asdict(candidate),
                "source_date": candidate.source_date.isoformat(),
            }
            for result in results
            for candidate in result.candidates
        ],
    }
    candidate_pool_json.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "output_dir": str(output_dir),
        "source_inventory": str(source_inventory),
        "candidate_pool_markdown": str(candidate_pool_md),
        "candidate_pool_json": str(candidate_pool_json),
        "raw_dump_dir": str(raw_dump_dir),
        "source_errors": source_errors,
    }


def parse_tag_ids(raw_value: str) -> set[int]:
    parts = re.split(r"[,\s|/]+", raw_value or "")
    return {int(part) for part in parts if part.isdigit()}


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(SHANGHAI)


def select_offer_recommendations(
    plans: list[dict],
    tag_map: dict[int, str],
    target_tag_ids: set[int],
    target_date: date,
    limit: int,
) -> list[OfferRecommendation]:
    picks: list[OfferRecommendation] = []

    for plan in plans:
        tag_ids = parse_tag_ids(plan.get("company_many_tags", ""))
        if not tag_ids.intersection(target_tag_ids):
            continue

        created_at = parse_datetime(plan["create_time"])
        if created_at.date() != target_date:
            continue

        industry = next((tag_map[tag_id] for tag_id in tag_ids if tag_id in tag_map), "未知")
        score = (
            float(plan.get("view_cnt", 0)) / 100.0
            + float(plan.get("is_recommend", 0)) * 2.0
        )
        picks.append(
            OfferRecommendation(
                company_name=clean_text(plan.get("company_name", "")),
                industry=industry,
                created_at=created_at,
                title=clean_text(plan.get("recruit_title", "")),
                positions=normalize_offer_positions(plan.get("positions", "")),
                city=clean_text(plan.get("recruit_city", "")),
                source_url=plan.get("notice_url", "https://offershow.cn/jobs/offershow_vip_table"),
                score=score,
            )
        )

    picks.sort(key=lambda item: (item.score, item.created_at), reverse=True)
    deduped: list[OfferRecommendation] = []
    seen: set[str] = set()
    for offer in picks:
        if offer.company_name in seen:
            continue
        seen.add(offer.company_name)
        deduped.append(offer)
        if len(deduped) >= limit:
            break
    return deduped


def candidate_text(candidate: DiscoveryCandidate) -> str:
    return clean_text(f"{candidate.title} {candidate.summary_or_excerpt}")


def is_low_signal_research_text(text: str) -> bool:
    return any(pattern in text for pattern in LOW_SIGNAL_RESEARCH_PATTERNS) and not any(
        signal in text for signal in HIGH_SIGNAL_CONTEXT_PATTERNS
    )


def score_keyword_weights(text: str, weights: dict[str, float]) -> float:
    score = 0.0
    for keyword, weight in weights.items():
        if keyword in text:
            score += weight
    return score


def dedupe_key_for_candidate(candidate: DiscoveryCandidate) -> str:
    normalized = clean_text(candidate.title).lower()
    normalized = re.sub(r"^早报[｜|:：-]\s*", "", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", normalized).strip("-")
    if normalized:
        return normalized
    fallback = clean_text(candidate.summary_or_excerpt)[:24].lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", fallback).strip("-") or "candidate"


def significant_terms(text: str) -> set[str]:
    cleaned = clean_text(text)
    terms = {
        token.lower()
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9.+-]{2,}\b", cleaned)
        if token.lower() not in {"beta", "today", "android", "apple"}
    }
    for entity in KNOWN_NEWS_ENTITIES:
        if entity in cleaned:
            terms.add(entity.lower())
    for phrase in EVENT_SPECIAL_TERMS:
        if phrase in cleaned:
            terms.add(phrase.lower())
    return terms


def event_families(text: str) -> set[str]:
    cleaned = clean_text(text)
    families: set[str] = set()
    for family, patterns in EVENT_FAMILY_PATTERNS.items():
        if any(pattern in cleaned for pattern in patterns):
            families.add(family)
    return families


def is_same_news_event(left: RankedNewsCandidate, right: RankedNewsCandidate) -> bool:
    if left.url == right.url:
        return True
    if left.dedupe_key == right.dedupe_key:
        return True
    left_terms = significant_terms(f"{left.title} {left.summary_or_excerpt}")
    right_terms = significant_terms(f"{right.title} {right.summary_or_excerpt}")
    shared_terms = left_terms.intersection(right_terms)
    if len(shared_terms) >= 2:
        return True
    shared_ascii_terms = {
        term for term in shared_terms if re.fullmatch(r"[a-z0-9.+-]+", term)
    }
    shared_special_terms = {
        term for term in shared_terms if not re.fullmatch(r"[a-z0-9.+-]+", term)
    }
    if shared_ascii_terms and shared_special_terms:
        return True
    left_entities = significant_terms(left.title).intersection({entity.lower() for entity in KNOWN_NEWS_ENTITIES})
    right_entities = significant_terms(right.title).intersection({entity.lower() for entity in KNOWN_NEWS_ENTITIES})
    shared_entities = left_entities.intersection(right_entities)
    if shared_entities:
        left_families = event_families(f"{left.title} {left.summary_or_excerpt}")
        right_families = event_families(f"{right.title} {right.summary_or_excerpt}")
        if left_families.intersection(right_families):
            return True
    return False


def score_candidate_workplace_relevance(candidate: DiscoveryCandidate) -> float:
    text = candidate_text(candidate)
    score = score_keyword_weights(text, VALUE_WORKPLACE_WEIGHTS)
    if any(keyword in text for keyword in ("安全", "漏洞", "后门", "整治")):
        score += 1.0
    if any(keyword in text for keyword in ("iOS", "桌面应用", "Chrome", "Skills")):
        score += 0.8
    if any(pattern in text for pattern in GENERIC_AI_LAUNCH_PATTERNS) and not any(
        signal in text for signal in HIGH_SIGNAL_CONTEXT_PATTERNS
    ):
        score -= 1.4
    if is_low_signal_research_text(text):
        score -= 1.6
    if any(keyword in text for keyword in READABILITY_BAD_PATTERNS):
        score -= 0.8
    return score


def score_candidate_new_information_value(candidate: DiscoveryCandidate) -> float:
    text = candidate_text(candidate)
    score = score_keyword_weights(text, VALUE_NEW_INFO_WEIGHTS)
    if any(keyword in text for keyword in ("今日", "正式", "开始", "启动", "升级")):
        score += 0.8
    if any(keyword in text for keyword in ("漏洞", "后门", "下架")):
        score += 1.0
    return score


def score_candidate_distribution_fit(candidate: DiscoveryCandidate) -> float:
    text = candidate_text(candidate)
    score = score_keyword_weights(text, VALUE_DISTRIBUTION_WEIGHTS)
    title_length = len(clean_text(candidate.title))
    if 8 <= title_length <= 28:
        score += 1.0
    elif title_length <= 40:
        score += 0.5
    else:
        score -= 0.5
    if any(pattern in text for pattern in HYPE_PATTERNS):
        score -= 1.5
    if any(pattern in text for pattern in READABILITY_BAD_PATTERNS):
        score -= 1.0
    if any(pattern in text for pattern in GENERIC_AI_LAUNCH_PATTERNS) and not any(
        signal in text for signal in HIGH_SIGNAL_CONTEXT_PATTERNS
    ):
        score -= 0.8
    if is_low_signal_research_text(text):
        score -= 1.2
    return score


def candidate_has_reporting_depth(candidate: DiscoveryCandidate) -> bool:
    title_compact = normalize_title(candidate.title).replace(" ", "")
    summary = clean_text(candidate.summary_or_excerpt)
    if not summary:
        return False
    summary_compact = summary.replace(" ", "")
    if summary_compact == title_compact:
        return False
    if len(summary) < 26 and not any(marker in summary for marker in REPORTING_DETAIL_MARKERS):
        return False
    return True


def candidate_readability_ok(candidate: DiscoveryCandidate) -> bool:
    text = candidate_text(candidate)
    if any(pattern in text for pattern in READABILITY_BAD_PATTERNS):
        return False
    if is_low_signal_research_text(text):
        return False
    if not candidate_has_reporting_depth(candidate):
        return False
    title = clean_text(candidate.title)
    if not title or len(title) > 42:
        return False
    return True


def candidate_needs_backcheck(candidate: DiscoveryCandidate, value_score: float) -> bool:
    text = candidate_text(candidate)
    has_uncertain_signal = any(pattern in text for pattern in BACKCHECK_ATTRIBUTION_PATTERNS) or any(
        keyword in text for keyword in ("计划", "测试", "传", "调整")
    )
    has_high_impact_topic = any(keyword in text for keyword in BACKCHECK_HIGH_IMPACT_KEYWORDS)
    return has_uncertain_signal and has_high_impact_topic and value_score >= 5.0


def evaluate_news_candidate(candidate: DiscoveryCandidate) -> RankedNewsCandidate:
    workplace_relevance = score_candidate_workplace_relevance(candidate)
    new_information_value = score_candidate_new_information_value(candidate)
    distribution_fit = score_candidate_distribution_fit(candidate)
    value_score = round(workplace_relevance + new_information_value + distribution_fit, 2)
    readability_ok = candidate_readability_ok(candidate)
    needs_backcheck = candidate_needs_backcheck(candidate, value_score)
    render_mode = "downgraded" if needs_backcheck else "normal"
    return RankedNewsCandidate(
        source_name=candidate.source_name,
        source_date=candidate.source_date,
        title=clean_text(candidate.title),
        summary_or_excerpt=clean_text(candidate.summary_or_excerpt),
        url=candidate.url,
        raw_section=candidate.raw_section,
        raw_order=candidate.raw_order,
        notes=candidate.notes,
        dedupe_key=dedupe_key_for_candidate(candidate),
        workplace_relevance=round(workplace_relevance, 2),
        new_information_value=round(new_information_value, 2),
        distribution_fit=round(distribution_fit, 2),
        readability_ok=readability_ok,
        needs_backcheck=needs_backcheck,
        value_score=value_score,
        render_mode=render_mode,
    )


def rank_news_candidates(
    candidates: Iterable[DiscoveryCandidate],
    *,
    limit: int = 10,
) -> list[RankedNewsCandidate]:
    ranked = [evaluate_news_candidate(candidate) for candidate in candidates]
    ranked.sort(
        key=lambda item: (
            item.value_score,
            item.new_information_value,
            item.distribution_fit,
            item.source_date.toordinal(),
        ),
        reverse=True,
    )

    picks: list[RankedNewsCandidate] = []
    for item in ranked:
        if any(is_same_news_event(item, picked) for picked in picks):
            continue
        if not item.readability_ok and item.value_score < 5.5:
            continue
        picks.append(item)
        if len(picks) >= limit:
            break
    return picks


def choose_supporting_sentences(
    summary: str,
    title: str,
    *,
    max_sentences: int = 2,
    max_total_length: int = 140,
) -> list[str]:
    sentences = split_sentences(summary)
    title_compact = normalize_title(title).replace(" ", "")
    picked: list[str] = []
    total_length = 0

    for sentence in sentences:
        normalized = sentence.strip().strip("。；; ")
        if not normalized:
            continue
        compact = normalized.replace(" ", "")
        if compact == title_compact:
            continue
        if title_compact in compact and len(compact) <= len(title_compact) + 8:
            continue
        projected_length = total_length + len(normalized)
        if picked and projected_length > max_total_length:
            break
        picked.append(normalized)
        total_length = projected_length
        if len(picked) >= max_sentences:
            break
    return picked


def apply_conservative_attribution(sentence: str) -> str:
    if any(pattern in sentence for pattern in BACKCHECK_ATTRIBUTION_PATTERNS):
        return sentence
    return f"发现源称，{sentence}"


def render_ranked_news_item(item: RankedNewsCandidate) -> str:
    title = normalize_title(item.title)
    supporting = choose_supporting_sentences(item.summary_or_excerpt, title)
    if item.render_mode == "downgraded" and supporting:
        supporting[0] = apply_conservative_attribution(supporting[0])
    if not supporting:
        return title
    return f"{title}。{'。 '.join(supporting)}"


def score_workplace_relevance(item: NewsItem) -> float:
    text = f"{item.title} {item.summary}"
    score = 0.0
    weighted_keywords = {
        "浏览器": 2.0,
        "控制": 1.5,
        "钉钉": 2.0,
        "飞书": 2.0,
        "微信": 2.0,
        "表情": 1.0,
        "视频号": 1.0,
        "办公": 2.0,
        "待办": 2.0,
        "企业": 1.5,
        "开源": 1.0,
        "Agent": 2.0,
        "智能体": 2.0,
        "开发": 1.2,
        "代码": 1.2,
        "工人": 2.0,
        "工作": 1.5,
        "创业": 1.5,
        "职场": 2.0,
        "营销": 1.2,
        "销售": 1.2,
        "会计": 1.2,
        "模型": 1.0,
        "CLI": 0.8,
        "部署": 1.0,
        "生产力": 1.5,
        "平台": 1.0,
        "合作": 0.8,
        "监管": 1.2,
        "安全": 1.5,
        "后门": 2.0,
        "漏洞": 1.8,
        "合规": 1.2,
    }
    for keyword, weight in weighted_keywords.items():
        if keyword in text:
            score += weight

    if item.track == "ai" and item.section in {"产品与功能更新", "行业展望与社会影响"}:
        score += 1.0
    if item.track == "general" and ("AI" in text or "Copilot" in text or "Meta" in text):
        score += 1.0
    if any(keyword in text for keyword in LOW_SIGNAL_PATTERNS):
        score -= 6.0
    if any(keyword in text for keyword in ("Linux 内核", "Nova Lake", "Crescent Island", "TSX")):
        score -= 2.5
    if any(keyword in text for keyword in HYPE_PATTERNS):
        score -= 2.5
    if item.track == "general" and not any(keyword in text for keyword in WORKPLACE_SIGNAL_KEYWORDS):
        score -= 2.0
    return score


def select_top_news(items: Iterable[NewsItem], limit: int) -> list[NewsItem]:
    ranked = sorted(items, key=score_workplace_relevance, reverse=True)
    result: list[NewsItem] = []
    seen: set[str] = set()
    for item in ranked:
        combined_text = f"{item.title} {item.summary}"
        if any(keyword in combined_text for keyword in LOW_SIGNAL_PATTERNS):
            continue
        if item.track == "general" and not any(
            keyword in combined_text for keyword in WORKPLACE_SIGNAL_KEYWORDS
        ):
            continue
        if score_workplace_relevance(item) <= 0:
            continue
        if item.title in seen:
            continue
        seen.add(item.title)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", clean_text(text))
    return [part.strip() for part in parts if part.strip()]


def compact_summary(text: str, limit: int = 42) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    sentences = split_sentences(text)
    if not sentences:
        return text

    selected: list[str] = []
    current_length = 0
    for sentence in sentences:
        sentence_length = len(sentence)
        if not selected:
            selected.append(sentence)
            current_length = sentence_length
            if sentence_length >= limit:
                break
            continue
        if current_length + 1 + sentence_length > limit:
            break
        selected.append(sentence)
        current_length += 1 + sentence_length

    return " ".join(selected).rstrip("，；。 ")


def normalize_title(title: str) -> str:
    return clean_text(re.sub(r"\s+", " ", title)).strip("。")


def sentence_workplace_value(sentence: str) -> float:
    score = 0.0
    for keyword in WORKPLACE_SIGNAL_KEYWORDS:
        if keyword in sentence:
            score += 1.0
    for keyword in HYPE_PATTERNS:
        if keyword in sentence:
            score -= 1.5
    for keyword in LOW_SIGNAL_PATTERNS:
        if keyword in sentence:
            score -= 3.0
    if len(sentence) > 80:
        score -= 1.5
    return score


def render_news_item(item: NewsItem) -> str:
    title = normalize_title(item.title)
    sentences = split_sentences(item.summary)
    if not sentences:
        return title

    normalized_title = title.replace(" ", "")
    picked: list[str] = []
    total_length = len(title)

    for sentence in sentences:
        normalized_sentence = sentence.strip().strip("。；; ")
        if not normalized_sentence:
            continue
        compact_sentence = normalized_sentence.replace(" ", "")
        if compact_sentence == normalized_title:
            continue
        if normalized_title in compact_sentence and len(compact_sentence) <= len(normalized_title) + 6:
            continue
        next_length = total_length + 1 + len(normalized_sentence)
        if picked and next_length > 180:
            break
        picked.append(normalized_sentence)
        total_length = next_length
        if len(picked) >= 3:
            break

    if not picked:
        return title
    return f"{title}。{'。 '.join(picked)}"


def format_offershow_error_message(error: str | None) -> str:
    if not error:
        return "OfferShow 抓取异常，本期未更新昨日新增投递。"
    if error.startswith("token_expired:"):
        return f"❌ {error.removeprefix('token_expired:')} 请重新获取 token 后再试。"
    if error.startswith("token_expiring:"):
        return f"⚠️ {error.removeprefix('token_expiring:')} 可继续抓取，但建议尽快续期。"
    if error.startswith("degraded_token_not_login"):
        return "⚠️ Token 有效，但 OfferShow API 返回账号未登录状态（is_login=false）。以下岗位若有展示，仅基于公开数据，可能不完整。"
    if error.startswith("degraded_not_vip"):
        return "⚠️ 当前账号不是招聘会员（is_recruit_vip=false）。以下岗位若有展示，仅基于公开数据，可能不完整。"
    if error.startswith("not_vip:"):
        return f"⚠️ {error.removeprefix('not_vip:')} 昨日新增投递暂不可用。"
    if error.startswith("auth_failed:"):
        return f"❌ {error.removeprefix('auth_failed:')} 请重新获取 token 后再试。"
    if error.startswith("token_missing:"):
        return f"⚠️ {error.removeprefix('token_missing:')} 昨日新增投递暂不可用。"
    if error.startswith("request_error:"):
        return f"OfferShow 网络请求失败：{error.removeprefix('request_error:')}。本期未更新昨日新增投递。"
    if error.startswith(OFFERSHOW_AUTH_ERROR_PREFIX):
        return error.removeprefix(OFFERSHOW_AUTH_ERROR_PREFIX)
    return "OfferShow 抓取异常，本期未更新昨日新增投递。"


def build_wechat_report(
    report_date: date,
    ranked_news_candidates: list[RankedNewsCandidate],
    offers: list[OfferRecommendation],
    target_offer_date: date,
    latest_public_offer_date: date | None = None,
    source_errors: dict[str, str] | None = None,
    offershow_diagnostics: dict[str, int | str] | None = None,
) -> tuple[str, str, str]:
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    news_lines = [f"{report_date.isoformat()} 行业日报", "", "📰 行业新闻"]
    if not ranked_news_candidates:
        news_lines.extend(["今日新闻源抓取异常，暂未生成行业新闻。", ""])
    else:
        for index, item in enumerate(ranked_news_candidates, start=1):
            marker = number_emojis[index - 1] if index <= len(number_emojis) else f"{index}."
            news_lines.extend(
                [
                    f"{marker} {render_ranked_news_item(item).rstrip('。')}。",
                    "",
                ]
            )

    jobs_lines = ["💼 职场速递｜昨日新增投递"]
    offershow_hint = (
        format_offershow_error_message(source_errors.get("offershow"))
        if source_errors and source_errors.get("offershow")
        else None
    )
    if not offers:
        if offershow_hint:
            jobs_lines.append(offershow_hint)
        elif offershow_diagnostics and int(offershow_diagnostics.get("total_plans", 0)) > 0:
            matched_count = int(offershow_diagnostics.get("matched_plan_count", 0))
            date_count = int(offershow_diagnostics.get("target_date_plan_count", 0))
            tag_count = int(offershow_diagnostics.get("target_tag_plan_count", 0))
            total_count = int(offershow_diagnostics.get("total_plans", 0))
            if matched_count == 0:
                jobs_lines.append(
                    "OfferShow 已返回岗位数据，但未命中当前筛选条件："
                    f"目标日期 {target_offer_date.isoformat()} 命中 {date_count} 条，"
                    f"四个目标方向命中 {tag_count} 条，同时满足两者的为 0 条。"
                    f"本次共读取 {total_count} 条岗位记录。"
                )
            else:
                jobs_lines.append(
                    "OfferShow 已返回岗位数据，但最终推荐列表为空。"
                    f"目标日期 {target_offer_date.isoformat()} 与四个目标方向同时命中 {matched_count} 条，"
                    "请检查后续推荐去重或展示逻辑。"
                )
        elif latest_public_offer_date and latest_public_offer_date < target_offer_date:
            jobs_lines.append(
                "OfferShow 公开接口当前最新招聘日期停留在 "
                f"{latest_public_offer_date.isoformat()}，暂未返回昨日 "
                "IT/互联网、广告传媒、游戏、消费生活 这四个方向的数据。"
            )
        else:
            jobs_lines.append("昨日 IT/互联网、广告传媒、游戏、消费生活 这四个方向暂无新增投递。")
    else:
        if offershow_hint:
            jobs_lines.extend([offershow_hint, ""])
        for index, offer in enumerate(offers, start=1):
            marker = number_emojis[index - 1] if index <= len(number_emojis) else f"{index}."
            jobs_lines.extend(
                [
                    f"{marker} {offer.company_name} | {offer.industry}",
                    f"   岗位：{offer.title}",
                    f"   城市：{offer.city}",
                    f"   方向：{compact_summary(offer.positions, limit=48)}",
                    f"   链接：{offer.source_url}",
                    "",
                ]
            )
    news_report = "\n".join(line for line in news_lines).strip()
    jobs_report = "\n".join(line for line in jobs_lines).strip()
    full_report = f"{news_report}\n\n{jobs_report}".strip()
    return full_report, news_report, jobs_report


def discover_latest_hubtoday_url(
    session: requests.Session, anchor_date: date, max_lookback_days: int = 3
) -> str:
    last_error: Exception | None = None
    for offset in range(max_lookback_days + 1):
        current = anchor_date - timedelta(days=offset)
        url = f"https://ai.hubtoday.app/{current:%Y-%m}/{current:%Y-%m-%d}/"
        try:
            response = session.get(url, timeout=12)
        except requests.RequestException as exc:
            last_error = exc
            continue
        if response.ok:
            return url
    if last_error is not None:
        raise RuntimeError("unable to find latest hubtoday page") from last_error
    raise RuntimeError("unable to find latest hubtoday page")


def discover_latest_sspai_paper_url(session: requests.Session) -> ArticleLink:
    response = session.get("https://sspai.com/", timeout=20)
    response.raise_for_status()
    links = extract_sspai_paper_links(response.text, "https://sspai.com/")
    if not links:
        raise RuntimeError("unable to discover latest 少数派派早报")
    return links[0]


def count_matching_offer_plans(
    plans: Iterable[dict],
    target_tag_ids: set[int],
    target_date: date,
) -> int:
    matched = 0
    for plan in plans:
        if not parse_tag_ids(plan.get("company_many_tags", "")).intersection(target_tag_ids):
            continue
        create_time = plan.get("create_time")
        if not create_time:
            continue
        if parse_datetime(create_time).date() == target_date:
            matched += 1
    return matched


def compute_offershow_diagnostics(
    plans: Iterable[dict],
    target_tag_ids: set[int],
    target_date: date,
) -> dict[str, int | str]:
    total_plans = 0
    target_date_plan_count = 0
    target_tag_plan_count = 0
    matched_plan_count = 0

    for plan in plans:
        total_plans += 1
        tag_match = bool(parse_tag_ids(plan.get("company_many_tags", "")).intersection(target_tag_ids))
        create_time = plan.get("create_time")
        date_match = False
        if create_time:
            date_match = parse_datetime(create_time).date() == target_date
        if date_match:
            target_date_plan_count += 1
        if tag_match:
            target_tag_plan_count += 1
        if date_match and tag_match:
            matched_plan_count += 1

    return {
        "target_date": target_date.isoformat(),
        "total_plans": total_plans,
        "target_date_plan_count": target_date_plan_count,
        "target_tag_plan_count": target_tag_plan_count,
        "matched_plan_count": matched_plan_count,
    }


def offershow_token_present(session: requests.Session) -> bool:
    headers = getattr(session, "headers", {}) or {}
    return bool(str(headers.get("accesstoken", "")).strip())


def offershow_auth_error(message: str) -> RuntimeError:
    return RuntimeError(f"{OFFERSHOW_AUTH_ERROR_PREFIX}{message}")


def unwrap_offershow_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def validate_offershow_auth_state(session: requests.Session) -> str | None:
    token = resolve_offershow_token()
    expiry_error = check_offershow_token_expiry(token)
    if isinstance(expiry_error, OfferShowTokenExpiringSoon):
        expiry_warning = str(expiry_error)
    elif expiry_error is not None:
        raise expiry_error
    else:
        expiry_warning = None
    if not offershow_token_present(session):
        raise OfferShowTokenMissing(
            "未配置会员 token，请在环境变量或 .env 中设置 OFFERSHOW_ACCESS_TOKEN。"
        )
    return expiry_warning


def fetch_offershow_data(
    session: requests.Session,
    *,
    target_date: date | None = None,
    target_tag_ids: set[int] | None = None,
    desired_count: int = 5,
    page_size: int = 50,
    max_pages: int = 6,
) -> OfferFetchResult:
    auth_warning = validate_offershow_auth_state(session)
    tags_response = session.get("https://offershow.cn/api/od/get_company_tags", timeout=20)
    tags_response.raise_for_status()
    tag_payload = tags_response.json()
    tag_map = {
        int(item["id"]): item["content"]
        for item in tag_payload["data"]["company_tags"]
    }

    payload = {
        "object_id": 0,
        "column_type": 0,
        "title": "最新招聘",
        "size": page_size,
        "page": 1,
        "total": 0,
        "search_content": "",
        "city": "",
        "recruit_type": 0,
        "company_many_tags": (
            ",".join(str(tag_id) for tag_id in sorted(target_tag_ids))
            if target_tag_ids
            else ""
        ),
        "company_character": 0,
        "progress_status": 0,
        "is_recommend": 0,
    }
    all_plans: list[dict] = []
    seen_plan_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        payload["page"] = page
        plans_response = session.post(
            f"https://offershow.cn/api/od/plan_table?page={page}&size={page_size}",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        plans_response.raise_for_status()
        plan_payload = plans_response.json()
        page_data = unwrap_offershow_data(plan_payload)
        page_plans = page_data.get("plans") or []
        if page_data.get("is_login") is False:
            page_dates: list[date] = []
            for plan in page_plans:
                plan_id = str(plan.get("uuid") or plan.get("company_name") or id(plan))
                if plan_id not in seen_plan_ids:
                    seen_plan_ids.add(plan_id)
                    all_plans.append(plan)
                    create_time = plan.get("create_time")
                    if create_time:
                        page_dates.append(parse_datetime(create_time).date())
            return OfferFetchResult(
                tag_map=tag_map,
                plans=all_plans,
                latest_public_date=latest_public_date_from_plans(all_plans),
                degraded_reason="token_not_login",
                auth_warning=auth_warning,
            )
        if page_data.get("is_recruit_vip") is False:
            page_dates: list[date] = []
            for plan in page_plans:
                plan_id = str(plan.get("uuid") or plan.get("company_name") or id(plan))
                if plan_id not in seen_plan_ids:
                    seen_plan_ids.add(plan_id)
                    all_plans.append(plan)
                    create_time = plan.get("create_time")
                    if create_time:
                        page_dates.append(parse_datetime(create_time).date())
            return OfferFetchResult(
                tag_map=tag_map,
                plans=all_plans,
                latest_public_date=latest_public_date_from_plans(all_plans),
                degraded_reason="not_vip_member",
                auth_warning=auth_warning,
            )
        if not page_plans:
            break

        new_count = 0
        page_dates: list[date] = []
        for plan in page_plans:
            plan_id = str(plan.get("uuid") or plan.get("company_name") or id(plan))
            if plan_id in seen_plan_ids:
                continue
            seen_plan_ids.add(plan_id)
            all_plans.append(plan)
            new_count += 1
            create_time = plan.get("create_time")
            if create_time:
                page_dates.append(parse_datetime(create_time).date())

        if len(page_plans) < page_size or new_count == 0:
            break
        if target_date and page_dates and min(page_dates) < target_date:
            break
        if (
            target_date
            and target_tag_ids
            and page_dates
            and min(page_dates) <= target_date
            and count_matching_offer_plans(all_plans, target_tag_ids, target_date) >= desired_count
        ):
            break

    latest_public_date = None
    for plan in all_plans:
        create_time = plan.get("create_time")
        if not create_time:
            continue
        plan_date = parse_datetime(create_time).date()
        if latest_public_date is None or plan_date > latest_public_date:
            latest_public_date = plan_date

    return OfferFetchResult(
        tag_map=tag_map,
        plans=all_plans,
        latest_public_date=latest_public_date,
        degraded_reason=None,
        auth_warning=auth_warning,
    )


def latest_public_date_from_plans(plans: list[dict]) -> date | None:
    latest: date | None = None
    for plan in plans:
        create_time = plan.get("create_time")
        if not create_time:
            continue
        plan_date = parse_datetime(create_time).date()
        if latest is None or plan_date > latest:
            latest = plan_date
    return latest


def generate_daily_report(anchor_date: date) -> tuple[str, str, str, dict]:
    session = build_session()
    previous_day = anchor_date - timedelta(days=1)
    source_errors: dict[str, str] = {}
    offershow_diagnostics: dict[str, int | str] | None = None
    collection_results, collection_errors, source_windows = collect_report_mode_candidate_pool(
        session, anchor_date
    )
    source_errors.update(collection_errors)
    unified_candidates = [
        candidate
        for result in collection_results
        if result.fetch_status == "ok"
        for candidate in result.candidates
    ]
    ranked_news_candidates = rank_news_candidates(unified_candidates, limit=10)
    if not ranked_news_candidates and collection_errors:
        source_errors["news_pool"] = " / ".join(
            f"{source}:{error}" for source, error in sorted(collection_errors.items())
        )

    try:
        offershow = fetch_offershow_data(
            session,
            target_date=previous_day,
            target_tag_ids=TARGET_OFFER_TAG_IDS,
            desired_count=5,
        )
        # 非致命原因（账号未登录/非会员），静默降级
        if offershow.degraded_reason == "token_not_login":
            source_errors["offershow"] = "degraded_token_not_login"
        elif offershow.degraded_reason == "not_vip_member":
            source_errors["offershow"] = "degraded_not_vip"
        elif offershow.auth_warning:
            source_errors["offershow"] = f"token_expiring:{offershow.auth_warning}"
        offers = select_offer_recommendations(
            plans=offershow.plans,
            tag_map=offershow.tag_map,
            target_tag_ids=TARGET_OFFER_TAG_IDS,
            target_date=previous_day,
            limit=5,
        )
        offershow_diagnostics = compute_offershow_diagnostics(
            offershow.plans,
            TARGET_OFFER_TAG_IDS,
            previous_day,
        )
    except OfferShowError as exc:
        offershow = OfferFetchResult(tag_map={}, plans=[], latest_public_date=None)
        offers = []
        if isinstance(exc, OfferShowTokenExpired):
            source_errors["offershow"] = f"token_expired:{exc}"
        elif isinstance(exc, OfferShowTokenExpiringSoon):
            source_errors["offershow"] = f"token_expiring:{exc}"
        elif isinstance(exc, OfferShowTokenMissing):
            source_errors["offershow"] = f"token_missing:{exc}"
        else:
            source_errors["offershow"] = f"api_error:{exc}"
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        offershow = OfferFetchResult(tag_map={}, plans=[], latest_public_date=None)
        offers = []
        source_errors["offershow"] = f"request_error:{exc}"

    report, news_report, jobs_report = build_wechat_report(
        anchor_date,
        ranked_news_candidates,
        offers,
        target_offer_date=previous_day,
        latest_public_offer_date=offershow.latest_public_date,
        source_errors=source_errors,
        offershow_diagnostics=offershow_diagnostics,
    )
    metadata = {
        "report_date": anchor_date.isoformat(),
        "news_candidate_date": anchor_date.isoformat(),
        "latest_public_offer_date": (
            offershow.latest_public_date.isoformat()
            if offershow.latest_public_date
            else None
        ),
        "source_errors": source_errors,
        "offershow_diagnostics": offershow_diagnostics,
        "candidate_source_windows": {
            source_name: target_date.isoformat()
            for source_name, target_date in source_windows.items()
        },
        "candidate_sources": [
            {
                "source_name": result.source_name,
                "entry_url": result.entry_url,
                "fetch_status": result.fetch_status,
                "candidate_count": len(result.candidates),
                "error": result.error,
                "target_date": source_windows[result.source_name].isoformat(),
            }
            for result in collection_results
        ],
        "messages": [news_report, jobs_report],
        "news_report": news_report,
        "jobs_report": jobs_report,
        "source_errors": source_errors,
        "ranked_news_candidates": [
            {
                **asdict(item),
                "source_date": item.source_date.isoformat(),
            }
            for item in ranked_news_candidates
        ],
        "offers": [
            {
                **asdict(offer),
                "created_at": offer.created_at.isoformat(),
            }
            for offer in offers
        ],
    }
    return report, news_report, jobs_report, metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成微信群可直接发送的智能日报。")
    parser.add_argument(
        "--mode",
        choices=("report", "collection"),
        default="report",
        help="运行模式：report 生成正式日报，collection 采集近 3 天候选样本池。",
    )
    parser.add_argument(
        "--date",
        dest="report_date",
        default=datetime.now(SHANGHAI).date().isoformat(),
        help="日报日期，格式 YYYY-MM-DD，默认使用 Asia/Shanghai 今天。",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=3,
        help="collection 模式的采样窗口天数，默认 3。",
    )
    parser.add_argument(
        "--collection-output-dir",
        help="collection 模式输出目录；不传则默认写到 output/research/YYYY-MM-DD。",
    )
    parser.add_argument(
        "--output",
        help="把日报正文写入文件；不传则打印到 stdout。",
    )
    parser.add_argument(
        "--json-output",
        help="把结构化元数据写入 JSON 文件，方便后续自动化接微信群机器人。",
    )
    parser.add_argument(
        "--news-output",
        help="把行业新闻单独写入文件，方便分段发送。",
    )
    parser.add_argument(
        "--jobs-output",
        help="把职场速递单独写入文件，方便分段发送。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_date = date.fromisoformat(args.report_date)
    if args.mode == "collection":
        lookback_days = max(args.window_days - 1, 0)
        results, source_errors = collect_candidate_pool(report_date, lookback_days=lookback_days)
        default_output_dir = (
            Path(__file__).resolve().parents[1] / "output" / "research" / report_date.isoformat()
        )
        manifest = write_collection_outputs(
            report_date,
            lookback_days=lookback_days,
            results=results,
            source_errors=source_errors,
            output_dir=Path(args.collection_output_dir) if args.collection_output_dir else default_output_dir,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    report, news_report, jobs_report, metadata = generate_daily_report(report_date)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        print(news_report)
        print()
        print(STDOUT_MESSAGE_BREAK)
        print()
        print(jobs_report)

    if args.news_output:
        Path(args.news_output).write_text(news_report, encoding="utf-8")

    if args.jobs_output:
        Path(args.jobs_output).write_text(jobs_report, encoding="utf-8")

    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
