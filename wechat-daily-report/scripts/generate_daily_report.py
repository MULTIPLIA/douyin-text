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
OFFERSHOW_TOKEN_EXPIRY_WARN_DAYS = 2

SOURCE_SUFFIX_RE = re.compile(r"\((?:AI资讯|AI资讯日报)\)")
KAOMOJI_RE = re.compile(
    r"[ᕦᕤ¯ツﾟ･ㅂ•ᴗ́̀و✧◡]+|\([^)]*[ᕦᕤ¯ツﾟ･ㅂ•ᴗ́̀و✧◡][^)]*\)"
)
SLASH_NOISE_RE = re.compile(r"(?<=\s)[\\/](?:\s+[\\/])+")
CJK_CHAR_RE = r"[\u4e00-\u9fff]"
CJK_PUNCT_RE = r"[，。！？；：、】【（）《》、]"
STDOUT_MESSAGE_BREAK = "<<<MESSAGE_BREAK>>>"


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
        return None  # 旧版脚本不在此处拦截，保留空的静默行为
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


def resolve_offershow_token() -> str:
    direct = os.getenv("OFFERSHOW_ACCESS_TOKEN", "").strip()
    if direct:
        return direct
    for dotenv_path in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        token = parse_dotenv_file(dotenv_path).get("OFFERSHOW_ACCESS_TOKEN", "").strip()
        if token:
            return token
    return ""


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    offershow_token = resolve_offershow_token()
    if offershow_token:
        session.headers.update({"accesstoken": offershow_token})
    return session


def clean_text(text: str) -> str:
    text = SOURCE_SUFFIX_RE.sub("", text)
    text = KAOMOJI_RE.sub("", text)
    text = SLASH_NOISE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    spacing_rules = (
        (fr"({CJK_CHAR_RE})\s+({CJK_CHAR_RE})", r"\1\2"),
        (fr"({CJK_PUNCT_RE})\s+({CJK_CHAR_RE})", r"\1\2"),
        (fr"({CJK_CHAR_RE})\s+({CJK_PUNCT_RE})", r"\1\2"),
        (r"(\d)\s+月", r"\1月"),
        (r"月\s+(\d)", r"月\1"),
        (r"(\d)\s+日", r"\1日"),
        (r"(\d)\s+年", r"\1年"),
    )
    previous = None
    while text != previous:
        previous = text
        for pattern, replacement in spacing_rules:
            text = re.sub(pattern, replacement, text)
    return text


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
                positions=clean_text(plan.get("positions", "").replace("\\n", "、")),
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
    }
    for keyword, weight in weighted_keywords.items():
        if keyword in text:
            score += weight

    if item.track == "ai" and item.section in {"产品与功能更新", "行业展望与社会影响"}:
        score += 1.0
    if item.track == "general" and ("AI" in text or "Copilot" in text or "Meta" in text):
        score += 1.0
    if any(keyword in text for keyword in ("扎克伯格", "分身", "3D 影像", "虚拟扎克伯格")):
        score -= 4.0
    if any(keyword in text for keyword in ("Linux 内核", "Nova Lake", "Crescent Island", "TSX")):
        score -= 2.5
    if any(
        keyword in text
        for keyword in (
            "DOA",
            "继上次讨论后",
            "门槛是否改变",
            "再起争议",
            "单纯 CRUD",
            "线下服务行业",
        )
    ):
        score -= 6.0
    return score


def select_top_news(items: Iterable[NewsItem], limit: int) -> list[NewsItem]:
    ranked = sorted(items, key=score_workplace_relevance, reverse=True)
    result: list[NewsItem] = []
    seen: set[str] = set()
    for item in ranked:
        if any(keyword in item.title for keyword in ("扎克伯格", "分身", "3D 影像")):
            continue
        if any(
            keyword in f"{item.title} {item.summary}"
            for keyword in ("DOA", "继上次讨论后", "门槛是否改变", "再起争议")
        ):
            continue
        if item.title in seen:
            continue
        seen.add(item.title)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*", clean_text(text))
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


def build_wechat_report(
    report_date: date,
    ai_items: list[NewsItem],
    general_items: list[NewsItem],
    offers: list[OfferRecommendation],
    target_offer_date: date,
    latest_public_offer_date: date | None = None,
    offershow_error: str | None = None,
) -> tuple[str, str, str]:
    all_news = select_top_news([*ai_items, *general_items], limit=10)
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    news_lines = [f"{report_date.isoformat()} 行业日报", "", "📰 行业新闻"]
    for index, item in enumerate(all_news, start=1):
        marker = number_emojis[index - 1] if index <= len(number_emojis) else f"{index}."
        news_lines.extend(
            [
                f"{marker} {compact_summary(item.summary, limit=140).rstrip('。') if item.summary else normalize_title(item.title)}。",
                "",
            ]
        )

    jobs_lines = ["💼 职场速递｜昨日新增投递"]
    if not offers:
        if offershow_error:
            if offershow_error.startswith("token_expired:"):
                jobs_lines.append(f"❌ {offershow_error.removeprefix('token_expired:')} 请重新获取 token 后再试。")
            elif offershow_error.startswith("token_expiring:"):
                jobs_lines.append(f"⚠️ {offershow_error.removeprefix('token_expiring:')} 昨日新增投递暂不可用。")
            elif offershow_error.startswith("auth_failed:"):
                jobs_lines.append(f"❌ {offershow_error.removeprefix('auth_failed:')} 请重新获取 token 后再试。")
            elif offershow_error.startswith("not_vip:"):
                jobs_lines.append(f"⚠️ {offershow_error.removeprefix('not_vip:')} 昨日新增投递暂不可用。")
            elif offershow_error.startswith("token_missing:"):
                jobs_lines.append(f"⚠️ {offershow_error.removeprefix('token_missing:')} 昨日新增投递暂不可用。")
            else:
                jobs_lines.append(f"OfferShow 抓取异常：{offershow_error}。昨日新增投递暂不可用。")
        elif latest_public_offer_date and latest_public_offer_date < target_offer_date:
            jobs_lines.append(
                "OfferShow 公开接口当前最新招聘日期停留在 "
                f"{latest_public_offer_date.isoformat()}，暂未返回昨日 "
                "IT/互联网、广告传媒、游戏、消费生活 这四个方向的数据。"
            )
        else:
            jobs_lines.append("昨日 IT/互联网、广告传媒、游戏、消费生活 这四个方向暂无新增投递。")
    else:
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
    for offset in range(max_lookback_days + 1):
        current = anchor_date - timedelta(days=offset)
        url = f"https://ai.hubtoday.app/{current:%Y-%m}/{current:%Y-%m-%d}/"
        response = session.get(url, timeout=20)
        if response.ok:
            return url
    raise RuntimeError("unable to find latest hubtoday page")


def discover_latest_sspai_paper_url(session: requests.Session) -> ArticleLink:
    response = session.get("https://sspai.com/", timeout=20)
    response.raise_for_status()
    links = extract_sspai_paper_links(response.text, "https://sspai.com/")
    if not links:
        raise RuntimeError("unable to discover latest 少数派派早报")
    return links[0]


def fetch_offershow_data(
    session: requests.Session,
    *,
    page_size: int = 100,
    max_pages: int = 10,
) -> OfferFetchResult:
    token = resolve_offershow_token()
    expiry_error = check_offershow_token_expiry(token)
    if expiry_error is not None:
        raise expiry_error

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
        "company_many_tags": "",
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
        data = plan_payload.get("data", {})
        if isinstance(data, dict):
            if data.get("is_login") is False:
                raise OfferShowAuthFailed("当前 token 已失效或未登录，请重新获取 OFFERSHOW_ACCESS_TOKEN。")
            if data.get("is_recruit_vip") is False:
                raise OfferShowNotVip("当前账号不是 OfferShow 招聘会员，无法抓取昨日会员岗位。")
            page_plans = data.get("plans") or []
        else:
            page_plans = []
        if not page_plans:
            break

        new_count = 0
        for plan in page_plans:
            plan_id = str(plan.get("uuid") or plan.get("company_name") or id(plan))
            if plan_id in seen_plan_ids:
                continue
            seen_plan_ids.add(plan_id)
            all_plans.append(plan)
            new_count += 1

        if len(page_plans) < page_size or new_count == 0:
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
    )


def generate_daily_report(anchor_date: date) -> tuple[str, str, str, dict]:
    session = build_session()

    hubtoday_url = discover_latest_hubtoday_url(session, anchor_date)
    hubtoday_html = session.get(hubtoday_url, timeout=20).text
    ai_items = select_top_news(parse_hubtoday_article(hubtoday_html, hubtoday_url), limit=5)

    sspai_link = discover_latest_sspai_paper_url(session)
    sspai_html = session.get(sspai_link.url, timeout=20).text
    general_items = select_top_news(
        parse_sspai_article(sspai_html, sspai_link.url),
        limit=5,
    )

    offershow_error: str | None = None
    try:
        offershow = fetch_offershow_data(session)
        offers = select_offer_recommendations(
            plans=offershow.plans,
            tag_map=offershow.tag_map,
            target_tag_ids={4, 9, 19, 12},
            target_date=anchor_date - timedelta(days=1),
            limit=5,
        )
        latest_public_offer_date = offershow.latest_public_date
    except OfferShowError as exc:
        offershow = OfferFetchResult(tag_map={}, plans=[], latest_public_date=None)
        offers = []
        latest_public_offer_date = None
        if isinstance(exc, OfferShowTokenExpired):
            offershow_error = f"token_expired:{exc}"
        elif isinstance(exc, OfferShowTokenExpiringSoon):
            offershow_error = f"token_expiring:{exc}"
        elif isinstance(exc, OfferShowNotVip):
            offershow_error = f"not_vip:{exc}"
        elif isinstance(exc, OfferShowAuthFailed):
            offershow_error = f"auth_failed:{exc}"
        elif isinstance(exc, OfferShowTokenMissing):
            offershow_error = f"token_missing:{exc}"
        else:
            offershow_error = f"api_error:{exc}"
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        offershow = OfferFetchResult(tag_map={}, plans=[], latest_public_date=None)
        offers = []
        latest_public_offer_date = None
        offershow_error = f"request_error:{exc}"

    report, news_report, jobs_report = build_wechat_report(
        anchor_date,
        ai_items,
        general_items,
        offers,
        target_offer_date=anchor_date - timedelta(days=1),
        latest_public_offer_date=latest_public_offer_date,
        offershow_error=offershow_error,
    )
    metadata = {
        "report_date": anchor_date.isoformat(),
        "hubtoday_url": hubtoday_url,
        "sspai_url": sspai_link.url,
        "latest_public_offer_date": (
            offershow.latest_public_date.isoformat()
            if offershow.latest_public_date
            else None
        ),
        "messages": [news_report, jobs_report],
        "news_report": news_report,
        "jobs_report": jobs_report,
        "ai_items": [asdict(item) for item in ai_items],
        "general_items": [asdict(item) for item in general_items],
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
        "--date",
        dest="report_date",
        default=datetime.now(SHANGHAI).date().isoformat(),
        help="日报日期，格式 YYYY-MM-DD，默认使用 Asia/Shanghai 今天。",
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
