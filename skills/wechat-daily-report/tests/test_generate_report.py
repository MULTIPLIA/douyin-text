from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo
from unittest import mock

import requests


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "generate_daily_report.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("generate_daily_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateReportTests(unittest.TestCase):
    def test_resolve_offershow_token_reads_project_env_file(self):
        module = load_module()
        env_path = module.PROJECT_ROOT / ".env"
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else None
        if env_path.exists():
            env_path.unlink()

        try:
            env_path.write_text('OFFERSHOW_ACCESS_TOKEN="env-token"\n', encoding="utf-8")
            with mock.patch.dict(module.os.environ, {}, clear=True):
                token = module.resolve_offershow_token()
            self.assertEqual(token, "env-token")
        finally:
            if original is None:
                env_path.unlink(missing_ok=True)
            else:
                env_path.write_text(original, encoding="utf-8")

    def test_build_session_includes_verified_offershow_headers(self):
        module = load_module()

        with mock.patch.dict(
            module.os.environ,
            {
                "OFFERSHOW_ACCESS_TOKEN": "token-123",
            },
            clear=True,
        ):
            session = module.build_session()

        self.assertEqual(session.headers["accesstoken"], "token-123")
        self.assertEqual(session.headers["Origin"], "https://offershow.cn")
        self.assertEqual(session.headers["Referer"], "https://offershow.cn/jobs/offershow_vip_table")

    def test_extract_pingwest_status_candidates_parses_fragment(self):
        module = load_module()
        fragment = """
        <section class="date-wrap" data-d="2026-04-16"><p class="date">今天 (4月16日, 周四)</p></section>
        <section data-id="312966" class="item w clearboth">
          <section class="time"><span>14:59</span></section>
          <section class="news-info">
            <section class="item-tag-list bg clearboth"><a class="tag"><span>Siri</span></a></section>
            <p class="title"><a href="//www.pingwest.com/w/312966">苹果安排近200名Siri工程师参加AI编程训练营，备战WWDC26</a></p>
            <p class="description"><a href="//www.pingwest.com/w/312966">据 The Information 报道，苹果公司正安排近200名Siri工程师参加AI编程训练营。</a></p>
          </section>
        </section>
        <section class="date-wrap" data-d="2026-04-15"><p class="date">昨天 (4月15日, 周三)</p></section>
        <section data-id="312916" class="item w clearboth">
          <section class="news-info">
            <section class="item-tag-list bg clearboth"><a class="tag"><span>MiniMax</span></a></section>
            <p class="title"><a href="//www.pingwest.com/w/312916">MiniMax发布桌面端智能体更新，拓展多模态操作边界</a></p>
          </section>
        </section>
        """

        items, last_id = module.extract_pingwest_status_candidates(fragment)

        self.assertEqual(last_id, "312916")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source_name, "品玩实事要问")
        self.assertEqual(items[0].source_date, date(2026, 4, 16))
        self.assertEqual(items[0].raw_section, "Siri")
        self.assertIn("AI编程训练营", items[0].summary_or_excerpt)
        self.assertEqual(items[1].summary_or_excerpt, "MiniMax发布桌面端智能体更新，拓展多模态操作边界")

    def test_summarize_pingwest_detail_prefers_detail_paragraphs(self):
        module = load_module()
        html = """
        <article>
          <h1>苹果安排近200名Siri工程师参加AI编程训练营，备战WWDC26</h1>
          <p>苹果公司正安排近200名Siri工程师参加为期数周的AI编程训练营，以强化团队在生成式AI领域的研发能力。</p>
          <p>这次训练营将覆盖代码生成、调试流程和内部工具协作，目标是在WWDC前提升Siri团队的交付效率。</p>
          <p>知情人士称，部分成果会优先用于语音助手和系统级AI体验优化。</p>
        </article>
        """

        summary = module.summarize_pingwest_detail(
            html,
            "苹果安排近200名Siri工程师参加AI编程训练营，备战WWDC26",
        )

        self.assertIn("为期数周的AI编程训练营", summary)
        self.assertIn("生成式AI领域的研发能力", summary)
        self.assertNotEqual(summary, "苹果安排近200名Siri工程师参加AI编程训练营，备战WWDC26")

    def test_collect_pingwest_candidates_uses_detail_page_summary(self):
        module = load_module()

        fragment = """
        <section class="date-wrap" data-d="2026-04-16"><p class="date">今天 (4月16日, 周四)</p></section>
        <section data-id="312966" class="item w clearboth">
          <section class="news-info">
            <section class="item-tag-list bg clearboth"><a class="tag"><span>Siri</span></a></section>
            <p class="title"><a href="//www.pingwest.com/w/312966">苹果安排近200名Siri工程师参加AI编程训练营，备战WWDC26</a></p>
            <p class="description"><a href="//www.pingwest.com/w/312966">据 The Information 报道，苹果公司正安排近200名Siri工程师参加AI编程训练营。</a></p>
          </section>
        </section>
        """
        detail_html = """
        <article>
          <p>苹果公司正安排近200名Siri工程师参加为期数周的AI编程训练营，以强化团队在生成式AI领域的研发能力。</p>
          <p>这次训练营将覆盖代码生成、调试流程和内部工具协作，目标是在WWDC前提升Siri团队的交付效率。</p>
        </article>
        """

        class FakeResponse:
            def __init__(self, *, text="", payload=None):
                self.text = text
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.calls = []
                self.status_calls = 0

            def get(self, url, params=None, headers=None, timeout=None):
                self.calls.append(url)
                if url == module.PINGWEST_STATUS_API_URL:
                    self.status_calls += 1
                    if self.status_calls == 1:
                        return FakeResponse(payload={"data": {"list": fragment}})
                    return FakeResponse(payload={"data": {"list": ""}})
                return FakeResponse(text=detail_html)

        result = module.collect_pingwest_candidates(
            FakeSession(),
            anchor_date=date(2026, 4, 16),
            lookback_days=0,
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertIn("为期数周的AI编程训练营", result.candidates[0].summary_or_excerpt)
        self.assertIn("生成式AI领域的研发能力", result.candidates[0].summary_or_excerpt)

    def test_extract_ifanr_article_cards_reads_summary_and_timestamp(self):
        module = load_module()
        html = """
        <div class="article-item article-item--list">
          <div class="article-image cover-image">
            <a class="article-label" href="https://www.ifanr.com/category/ifanrnews">早报</a>
            <a class="article-link cover-block" href="https://www.ifanr.com/1662386"></a>
          </div>
          <div class="article-info">
            <h3><a href="https://www.ifanr.com/1662386">早报｜手机销量十季度首降，三星苹果成唯二赢家</a></h3>
            <div class="article-summary">· CEO 亲自上阵，小扎被曝搬工位与 Meta AI 团队一起写代码</div>
            <div class="article-meta" data-post-id="1662386">
              <time data-timestamp="1776299348">7 小时前</time>
            </div>
          </div>
        </div>
        """

        cards = module.extract_ifanr_article_cards(html)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["section"], "早报")
        self.assertEqual(cards[0]["title"], "早报｜手机销量十季度首降，三星苹果成唯二赢家")
        self.assertIn("小扎", cards[0]["summary"])
        self.assertEqual(cards[0]["published_at"].date(), date(2026, 4, 16))

    def test_parse_ifanr_article_extracts_heading_blocks(self):
        module = load_module()
        html = """
        <article>
          <p>🤖</p>
          <h3>苹果将 Siri 程序员送进 AI「训练营」</h3>
          <p>苹果公司正安排近200名Siri工程师参加为期数周的AI编程训练营。</p>
          <p>此次培训旨在提升团队在生成式AI领域的工程能力。</p>
          <h3>李想：不招非原生 AI 人才</h3>
          <p>李想表示，未来团队会优先考虑具备原生 AI 工作方式的人才。</p>
          <ul>
            <li>内部协作流程正在围绕 AI 重构</li>
          </ul>
        </article>
        """

        items = module.parse_ifanr_article(html, "https://www.ifanr.com/1662386")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "苹果将 Siri 程序员送进 AI「训练营」")
        self.assertIn("生成式AI领域", items[0].summary)
        self.assertIn("内部协作流程", items[1].summary)

    def test_parse_hubtoday_article_extracts_sections_and_links(self):
        module = load_module()
        html = """
        <html>
          <body>
            <main>
              <div class="content">
                <h1>AI资讯日报 2026/4/14</h1>
                <h3>产品与功能更新</h3>
                <ol>
                  <li>Claude 获得真实浏览器控制权。开发者可利用 <a href="https://example.com/claude">dev-browser 插件</a>，AI 能直接编写真实代码。</li>
                  <li>阿里桌面智能体更名 QwenPaw。原生接入钉钉与飞书，支持长程复杂任务处理。</li>
                </ol>
                <h3>行业展望与社会影响</h3>
                <ol>
                  <li>两成美国工人部分工作被替代。劳动力市场模型面临重构。</li>
                </ol>
              </div>
            </main>
          </body>
        </html>
        """

        items = module.parse_hubtoday_article(
            html, "https://ai.hubtoday.app/2026-04/2026-04-14/"
        )

        self.assertEqual(
            [item.section for item in items],
            ["产品与功能更新", "产品与功能更新", "行业展望与社会影响"],
        )
        self.assertTrue(items[0].title.startswith("Claude 获得真实浏览器控制权"))
        self.assertEqual(items[0].source_url, "https://example.com/claude")
        self.assertIn("钉钉与飞书", items[1].summary)

    def test_extract_sspai_paper_links_prefers_latest_morning_paper(self):
        module = load_module()
        home_html = """
        <html><body>
          <a href="/post/108568">派早报：微软宣布改进和简化 Windows 预览体验计划</a>
          <a href="/post/108614">派早报：稳定版 Linux 7.0 内核发布等</a>
          <a href="/post/108000">别的文章</a>
        </body></html>
        """

        links = module.extract_sspai_paper_links(home_html, "https://sspai.com/")

        self.assertEqual(links[0].title, "派早报：稳定版 Linux 7.0 内核发布等")
        self.assertEqual(links[0].url, "https://sspai.com/post/108614")

    def test_parse_sspai_article_extracts_heading_paragraph_pairs(self):
        module = load_module()
        html = """
        <html><body>
          <article>
            <h2>MiniMax 开源 M2.7 模型</h2>
            <p>MiniMax 于 4 月 12 日宣布开源 M2.7 模型，支持复杂 Agent 框架与高度复杂的生产力任务。</p>
            <p>该模型具备自我进化能力。</p>
            <h2>微软开始测试类 OpenClaw 的 Copilot 服务</h2>
            <p>微软正在测试类似 OpenClaw 的 AI 服务，可从邮箱和日历信息中自动生成每日待办事项。</p>
          </article>
        </body></html>
        """

        items = module.parse_sspai_article(html, "https://sspai.com/post/108614")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "MiniMax 开源 M2.7 模型")
        self.assertIn("自我进化能力", items[0].summary)
        self.assertIn("每日待办事项", items[1].summary)

    def test_clean_text_removes_source_suffix_and_kaomoji(self):
        module = load_module()
        text = "阿里桌面智能体更名 QwenPaw。 原生接入 ᕦ(･ㅂ･)ᕤ。 来自(AI资讯日报) 自动驾驶办公 (•̀ᴗ•́)و \\ /"
        cleaned = module.clean_text(text)
        self.assertNotIn("ᕦ", cleaned)
        self.assertNotIn("ㅂ", cleaned)
        self.assertNotIn("AI资讯日报", cleaned)
        self.assertNotIn("•̀ᴗ•́", cleaned)
        self.assertNotIn("\\ /", cleaned)

    def test_clean_text_keeps_normal_english_words(self):
        module = load_module()
        text = "据 The Information 报道，OpenAI 正计划调整 ChatGPT 广告策略，TechCrunch 也进行了跟进。"
        cleaned = module.clean_text(text)
        self.assertIn("The Information", cleaned)
        self.assertIn("ChatGPT", cleaned)
        self.assertIn("TechCrunch", cleaned)

    def test_clean_text_removes_cjk_spacing_noise(self):
        module = load_module()
        text = "劳动力市场模型 面临重构 。政策响应窗口 正在快速关闭 。 4 月 13 日"
        cleaned = module.clean_text(text)
        self.assertIn("劳动力市场模型面临重构。政策响应窗口正在快速关闭。", cleaned)
        self.assertIn("4月13日", cleaned)

    def test_compact_summary_prefers_complete_sentences(self):
        module = load_module()
        text = (
            "据 The Information 报道，微软目前正在测试类似 OpenClaw 的 AI 服务，"
            "旨在令 Microsoft 365 Copilot 也拥有替用户在后台自动处理事务的能力。"
            "这项能力会首先落在邮件和日历场景。"
        )
        summary = module.compact_summary(text, limit=40)
        self.assertTrue(summary.endswith("能力"))
        self.assertIn("Microsoft 365 Copilot", summary)

    def test_should_exclude_offer_company_for_consumer_industry_with_engineering_roles(self):
        module = load_module()

        excluded = module.should_exclude_offer_company(
            "消费生活",
            "服装设计师、工艺工程师、产品经理、鞋工程开发",
        )

        self.assertTrue(excluded)

    def test_select_offer_recommendations_excludes_consumer_company_with_engineering_roles(self):
        module = load_module()

        picks = module.select_offer_recommendations(
            plans=[
                {
                    "uuid": "consumer-a",
                    "company_name": "消费公司A",
                    "create_time": "2026-04-15T10:00:13+08:00",
                    "company_many_tags": "12",
                    "recruit_title": "消费公司A2026春招",
                    "positions": "服装设计师、工艺工程师、商品运营",
                    "recruit_city": "上海",
                    "notice_url": "https://example.com/a",
                    "view_cnt": 300,
                    "is_recommend": 1,
                },
                {
                    "uuid": "consumer-c",
                    "company_name": "消费公司C",
                    "create_time": "2026-04-15T11:00:13+08:00",
                    "company_many_tags": "12",
                    "recruit_title": "消费公司C2026春招",
                    "positions": "品牌营销、商品运营",
                    "recruit_city": "广州",
                    "notice_url": "https://example.com/c",
                    "view_cnt": 200,
                    "is_recommend": 0,
                },
            ],
            tag_map={12: "消费生活"},
            target_tag_ids={12},
            target_date=date(2026, 4, 15),
            limit=10,
        )

        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0].company_name, "消费公司C")
        self.assertEqual(picks[0].positions, "品牌营销、商品运营")

    def test_select_offer_recommendations_excludes_multi_tag_consumer_company_with_engineering_roles(self):
        module = load_module()

        picks = module.select_offer_recommendations(
            plans=[
                {
                    "uuid": "consumer-multi-tag",
                    "company_name": "多标签消费公司",
                    "create_time": "2026-04-15T10:00:13+08:00",
                    "company_many_tags": "8,12",
                    "recruit_title": "多标签消费公司2026春招",
                    "positions": "品牌运营、AI安全工程师",
                    "recruit_city": "上海",
                    "notice_url": "https://example.com/multi",
                    "view_cnt": 300,
                    "is_recommend": 1,
                }
            ],
            tag_map={8: "IT/互联网", 12: "消费生活"},
            target_tag_ids={8, 12},
            target_date=date(2026, 4, 15),
            limit=10,
        )

        self.assertEqual(picks, [])

    def test_select_offer_recommendations_supports_limit_of_ten(self):
        module = load_module()

        plans = []
        for idx in range(12):
            plans.append(
                {
                    "uuid": f"job-{idx}",
                    "company_name": f"公司{idx}",
                    "create_time": "2026-04-15T10:00:13+08:00",
                    "company_many_tags": "4",
                    "recruit_title": f"公司{idx}2026春招",
                    "positions": f"岗位{idx}",
                    "recruit_city": "北京",
                    "notice_url": f"https://example.com/{idx}",
                    "view_cnt": 100 + idx,
                    "is_recommend": 0,
                }
            )

        picks = module.select_offer_recommendations(
            plans=plans,
            tag_map={4: "IT/互联网"},
            target_tag_ids={4},
            target_date=date(2026, 4, 15),
            limit=10,
        )

        self.assertEqual(len(picks), 10)
        self.assertEqual(picks[0].company_name, "公司11")
        self.assertEqual(picks[-1].company_name, "公司2")

    def test_render_news_item_keeps_original_sentence_order(self):
        module = load_module()
        item = module.NewsItem(
            track="general",
            section="互联网综合",
            title="Google 宣布上线 Chrome Skills 功能",
            summary=(
                "Google 宣布上线 Chrome Skills 功能。"
                "新功能允许用户在桌面版 Chrome 中调用浏览器级 AI 技能。"
                "该更新已经在部分用户中灰度发布。"
            ),
            source_url="https://example.com/chrome",
            source_name="少数派",
        )

        rendered = module.render_news_item(item)

        self.assertEqual(
            rendered,
            "Google 宣布上线 Chrome Skills 功能。新功能允许用户在桌面版 Chrome 中调用浏览器级 AI 技能。 该更新已经在部分用户中灰度发布",
        )

    def test_render_news_item_limits_but_preserves_first_sentences(self):
        module = load_module()
        item = module.NewsItem(
            track="general",
            section="互联网综合",
            title="Blackmagic 发布 DaVinci Resolve 21 更新",
            summary=(
                "Blackmagic 发布 DaVinci Resolve 21 更新。"
                "新版本加入 Photo 页面并深度集成 AI 搜索、焦点重排、面部优化等能力；"
                "功能升级涵盖关键帧系统、Krokodove 图形工具库、Lottie 动画与音频修改器；"
                "技术底层更新至 USD SDK 25.11。"
            ),
            source_url="https://example.com/resolve",
            source_name="少数派",
        )

        rendered = module.render_news_item(item)

        self.assertTrue(rendered.startswith("Blackmagic 发布 DaVinci Resolve 21 更新。"))
        self.assertIn("新版本加入 Photo 页面并深度集成 AI 搜索、焦点重排、面部优化等能力", rendered)

    def test_discover_latest_hubtoday_url_skips_timeout_and_falls_back(self):
        module = load_module()

        class FakeSession:
            def get(self, url, timeout):
                if url.endswith("/2026-04-15/"):
                    raise requests.exceptions.ConnectTimeout("timeout")

                class Response:
                    ok = url.endswith("/2026-04-14/")

                return Response()

        result = module.discover_latest_hubtoday_url(
            FakeSession(),
            date(2026, 4, 15),
            max_lookback_days=2,
        )

        self.assertTrue(result.endswith("/2026-04-14/"))

    def test_rank_news_candidates_uses_unified_pool_and_dedupes_same_event(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="少数派派早报",
                source_date=date(2026, 4, 16),
                title="Google 宣布整治返回键劫持行为",
                summary_or_excerpt="Google 宣布将从 6月15日起将返回键劫持正式定性为恶意行为并开展专项整治。",
                url="https://sspai.com/post/1",
                raw_section="互联网综合",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="爱范儿日报",
                source_date=date(2026, 4, 16),
                title="Google 宣布整治返回键劫持行为",
                summary_or_excerpt="Google 将把返回键劫持纳入恶意行为治理范围，违规站点可能面临搜索排名处罚。",
                url="https://ifanr.com/1",
                raw_section="早报",
                raw_order=2,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="大量 WordPress 插件遭后门植入",
                summary_or_excerpt="大量 WordPress 插件被发现植入后门并导致大规模下架，影响超过 40 万次安装。",
                url="https://pingwest.com/1",
                raw_section="安全",
                raw_order=3,
            ),
        ]

        picks = module.rank_news_candidates(candidates, limit=10)

        self.assertEqual(len(picks), 2)
        self.assertEqual(
            {item.title for item in picks},
            {"Google 宣布整治返回键劫持行为", "大量 WordPress 插件遭后门植入"},
        )

    def test_rank_news_candidates_dedupes_same_event_with_different_titles(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="爱范儿日报",
                source_date=date(2026, 4, 15),
                title="MiniMax Agent 更新：能像人一样操作电脑",
                summary_or_excerpt="MiniMax Agent 桌面端推出两项更新，让 AI Agent 像人一样直接操作电脑，并支持接入飞书、微信、企业微信。",
                url="https://ifanr.com/minimax-agent",
                raw_section="早报",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 15),
                title="MiniMax发布桌面端智能体更新，拓展多模态操作边界",
                summary_or_excerpt="MiniMax 宣布推出桌面端智能体两项重要更新：Pocket 功能与 Computer Use 功能，支持操作系统图形界面任务。",
                url="https://pingwest.com/minimax-desktop",
                raw_section="MiniMax",
                raw_order=2,
            ),
        ]

        picks = module.rank_news_candidates(candidates, limit=10)

        self.assertEqual(len(picks), 1)
        self.assertIn("MiniMax", picks[0].title)

    def test_rank_news_candidates_allows_single_source_to_dominate(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="苹果将 Siri 程序员送进 AI 训练营",
                summary_or_excerpt="苹果正安排近200名 Siri 工程师参加 AI 编程训练营，提升生成式 AI 工程能力。",
                url="https://pingwest.com/apple",
                raw_section="Apple",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="大量 WordPress 插件遭后门植入",
                summary_or_excerpt="大量 WordPress 插件被发现植入后门并导致大规模下架，影响超过 40 万次安装。",
                url="https://pingwest.com/wp",
                raw_section="安全",
                raw_order=2,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="Google 宣布整治返回键劫持行为",
                summary_or_excerpt="Google 将从 6月15日起将返回键劫持正式定性为恶意行为并开展专项整治。",
                url="https://pingwest.com/google",
                raw_section="Google",
                raw_order=3,
            ),
            module.DiscoveryCandidate(
                source_name="少数派派早报",
                source_date=date(2026, 4, 15),
                title="普通消费硬件动态",
                summary_or_excerpt="某品牌发布一款新硬件设备，主打影像与设计升级。",
                url="https://sspai.com/post/hw",
                raw_section="互联网综合",
                raw_order=1,
            ),
        ]

        picks = module.rank_news_candidates(candidates, limit=3)

        self.assertEqual(len(picks), 3)
        self.assertTrue(all(item.source_name == "品玩实事要问" for item in picks))

    def test_consumer_hardware_candidate_is_scored_not_force_filtered(self):
        module = load_module()
        hardware = module.DiscoveryCandidate(
            source_name="爱范儿日报",
            source_date=date(2026, 4, 16),
            title="千问 AI 眼镜 S1 开售",
            summary_or_excerpt="千问 AI 眼镜 S1 今日开售，主打语音交互、拍摄和随身信息入口。",
            url="https://ifanr.com/glasses",
            raw_section="早报",
            raw_order=1,
        )

        ranked = module.rank_news_candidates([hardware], limit=10)

        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0].value_score, 0)

    def test_high_value_second_hand_reporting_triggers_backcheck(self):
        module = load_module()
        candidate = module.DiscoveryCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 16),
            title="OpenAI 计划调整 ChatGPT 广告策略",
            summary_or_excerpt="据 The Information 报道，OpenAI 正计划调整 ChatGPT 广告策略，并探索更清晰的商业化路径。",
            url="https://pingwest.com/openai-ads",
            raw_section="OpenAI",
            raw_order=1,
        )

        ranked = module.rank_news_candidates([candidate], limit=10)

        self.assertTrue(ranked[0].needs_backcheck)
        self.assertGreater(ranked[0].value_score, 0)

    def test_security_and_platform_signals_outrank_generic_ai_launches(self):
        module = load_module()
        generic_ai = module.DiscoveryCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 15),
            title="百度发布ERNIE-Image模型，开源8B参数文生图技术",
            summary_or_excerpt="百度文心大模型团队正式开源全新文生图模型 ERNIE-Image。",
            url="https://pingwest.com/ernie-image",
            raw_section="百度",
            raw_order=1,
        )
        security = module.DiscoveryCandidate(
            source_name="少数派派早报",
            source_date=date(2026, 4, 15),
            title="大量 WordPress 插件遭后门植入",
            summary_or_excerpt="大量 WordPress 插件被发现植入后门并导致大规模下架，影响超过 40 万次安装。",
            url="https://sspai.com/wordpress",
            raw_section="互联网综合",
            raw_order=2,
        )
        platform_rule = module.DiscoveryCandidate(
            source_name="少数派派早报",
            source_date=date(2026, 4, 15),
            title="Google 宣布整治返回键劫持行为",
            summary_or_excerpt="Google 将从 6月15日起将返回键劫持正式定性为恶意行为并开展专项整治。",
            url="https://sspai.com/google-rule",
            raw_section="互联网综合",
            raw_order=3,
        )

        ranked = module.rank_news_candidates([generic_ai, security, platform_rule], limit=10)

        self.assertEqual(
            {ranked[0].title, ranked[1].title},
            {"Google 宣布整治返回键劫持行为", "大量 WordPress 插件遭后门植入"},
        )
        self.assertEqual(ranked[2].title, "百度发布ERNIE-Image模型，开源8B参数文生图技术")

    def test_rank_news_candidates_drops_low_information_promo_items(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 15),
                title="WPS for Pad全球上线引发海外权威媒体广泛关注",
                summary_or_excerpt="WPS for Pad全球上线引发海外权威媒体广泛关注",
                url="https://pingwest.com/wps-pad",
                raw_section="WPS",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="少数派派早报",
                source_date=date(2026, 4, 15),
                title="Google 宣布整治返回键劫持行为",
                summary_or_excerpt="Google 将从 6月15日起将返回键劫持正式定性为恶意行为并开展专项整治。",
                url="https://sspai.com/google-rule",
                raw_section="互联网综合",
                raw_order=2,
            ),
        ]

        ranked = module.rank_news_candidates(candidates, limit=10)

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].title, "Google 宣布整治返回键劫持行为")

    def test_rank_news_candidates_drops_low_signal_research_items(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 15),
                title="南洋理工MMLab发布Hand2World，实现世界模型“手眼”协同交互",
                summary_or_excerpt="据CSDN消息，南洋理工大学MMLab团队近日正式推出Hand2World模型。该模型使AI世界模型能够通过空中手势实时生成第一人称交互视频。",
                url="https://pingwest.com/hand2world",
                raw_section="AI",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 15),
                title="滴滴自动驾驶全球化布局加快，年内在阿联酋开展试点",
                summary_or_excerpt="滴滴联合创始人、滴滴自动驾驶公司CEO张博出席论坛，介绍 Robotaxi 年内在阿联酋开展试点的计划。",
                url="https://pingwest.com/didi",
                raw_section="滴滴",
                raw_order=2,
            ),
        ]

        ranked = module.rank_news_candidates(candidates, limit=10)

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].title, "滴滴自动驾驶全球化布局加快，年内在阿联酋开展试点")

    def test_select_offer_recommendations_filters_previous_day_and_target_tags(self):
        module = load_module()
        now = datetime(2026, 4, 14, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        plans = [
            {
                "company_name": "哔哩哔哩",
                "company_many_tags": "4",
                "create_time": "2026-04-11T10:00:13+08:00",
                "recruit_title": "哔哩哔哩2026年招募暑期实习生",
                "positions": "内容制作实习生\\n活动运营实习生",
                "notice_url": "https://example.com/bili",
                "view_cnt": 131,
                "is_recommend": 0,
                "recruit_city": "上海",
            },
            {
                "company_name": "凤凰出版传媒集团",
                "company_many_tags": "9",
                "create_time": "2026-04-10T10:15:30+08:00",
                "recruit_title": "凤凰出版传媒集团2026年春季校园招聘",
                "positions": "新业务类岗位\\n生产经营类岗位",
                "notice_url": "https://example.com/phoenix",
                "view_cnt": 177,
                "is_recommend": 0,
                "recruit_city": "南京",
            },
            {
                "company_name": "游戏公司",
                "company_many_tags": "19",
                "create_time": "2026-04-13T08:30:00+08:00",
                "recruit_title": "游戏公司春招",
                "positions": "策划\\n开发",
                "notice_url": "https://example.com/game",
                "view_cnt": 200,
                "is_recommend": 1,
                "recruit_city": "上海",
            },
            {
                "company_name": "消费公司",
                "company_many_tags": "12",
                "create_time": "2026-04-13T09:15:30+08:00",
                "recruit_title": "消费公司春招",
                "positions": "运营\\n市场",
                "notice_url": "https://example.com/retail",
                "view_cnt": 150,
                "is_recommend": 0,
                "recruit_city": "杭州",
            },
            {
                "company_name": "过期公司",
                "company_many_tags": "4",
                "create_time": "2026-04-01T10:15:30+08:00",
                "recruit_title": "旧岗位",
                "positions": "旧岗位",
                "notice_url": "https://example.com/old",
                "view_cnt": 999,
                "is_recommend": 1,
                "recruit_city": "北京",
            },
            {
                "company_name": "非目标行业",
                "company_many_tags": "6",
                "create_time": "2026-04-13T10:15:30+08:00",
                "recruit_title": "金融岗位",
                "positions": "量化研究",
                "notice_url": "https://example.com/finance",
                "view_cnt": 500,
                "is_recommend": 1,
                "recruit_city": "上海",
            },
            {
                "company_name": "当天公司",
                "company_many_tags": "4",
                "create_time": "2026-04-14T10:15:30+08:00",
                "recruit_title": "当天岗位",
                "positions": "工程师",
                "notice_url": "https://example.com/today",
                "view_cnt": 600,
                "is_recommend": 1,
                "recruit_city": "北京",
            },
        ]
        tag_map = {
            4: "IT/互联网",
            9: "广告传媒",
            6: "金融行业",
            19: "游戏",
            12: "消费生活",
        }

        picks = module.select_offer_recommendations(
            plans=plans,
            tag_map=tag_map,
            target_tag_ids={4, 9, 19, 12},
            target_date=now.date() - module.timedelta(days=1),
            limit=5,
        )

        self.assertEqual(
            [offer.company_name for offer in picks],
            ["游戏公司", "消费公司"],
        )
        self.assertTrue(
            all(offer.industry in {"IT/互联网", "广告传媒", "游戏", "消费生活"} for offer in picks)
        )
        self.assertEqual(picks[0].positions, "策划、开发")
        self.assertEqual(picks[1].positions, "运营、市场")

    def test_normalize_offer_positions_joins_roles_with_separator(self):
        module = load_module()

        positions = module.normalize_offer_positions(
            "【产品序列】\r\n🔹产品策划、产品规划、产品GTM\r\n"
            "🔹产品数据、技术产品、产品VOC（中/日）\r\n"
            "* 每位同学每年最多可投递3个岗位哦"
        )

        self.assertEqual(
            positions,
            "产品策划、产品规划、产品GTM、产品数据、技术产品、产品VOC（中/日）",
        )

    def test_fetch_offershow_data_paginates_and_stops_after_target_date(self):
        module = load_module()

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.headers = {"accesstoken": "vip-token"}
                self.posts = []

            def get(self, url, timeout):
                if "get_my_vip_info" in url:
                    return FakeResponse({"data": {"status": 1, "is_login": True, "is_recruit_vip": True}})
                self.last_get = (url, timeout)
                return FakeResponse(
                    {
                        "data": {
                            "company_tags": [
                                {"id": 4, "content": "IT/互联网"},
                                {"id": 9, "content": "广告传媒"},
                            ]
                        }
                    }
                )

            def post(self, url, data, headers, timeout):
                self.posts.append((url, data["page"], data["size"]))
                plans_by_page = {
                    1: [
                        {
                            "uuid": "a",
                            "company_name": "A公司",
                            "create_time": "2026-04-11T10:00:13+08:00",
                            "company_many_tags": "4",
                        },
                        {
                            "uuid": "b",
                            "company_name": "B公司",
                            "create_time": "2026-04-10T10:00:13+08:00",
                            "company_many_tags": "4",
                        },
                    ],
                    2: [
                        {
                            "uuid": "c",
                            "company_name": "C公司",
                            "create_time": "2026-04-09T10:00:13+08:00",
                            "company_many_tags": "4",
                        }
                    ],
                    3: [
                        {
                            "uuid": "d",
                            "company_name": "D公司",
                            "create_time": "2026-04-08T10:00:13+08:00",
                            "company_many_tags": "4",
                        }
                    ],
                }
                return FakeResponse({"data": {"plans": plans_by_page.get(data["page"], [])}})

        fake_session = FakeSession()
        with mock.patch.object(module, "check_offershow_token_expiry", return_value=None):
            result = module.fetch_offershow_data(
                fake_session,
                target_date=date(2026, 4, 10),
                target_tag_ids={4},
                desired_count=2,
                page_size=2,
                max_pages=5,
            )

        self.assertEqual(result.tag_map[4], "IT/互联网")
        self.assertEqual(len(result.plans), 3)
        self.assertEqual(result.latest_public_date, date(2026, 4, 11))
        self.assertEqual([page for _, page, _ in fake_session.posts], [1, 2])

    def test_fetch_offershow_data_requires_member_token(self):
        module = load_module()

        class FakeSession:
            def __init__(self):
                self.headers = {}

        with self.assertRaisesRegex(module.OfferShowTokenMissing, "OFFERSHOW_ACCESS_TOKEN"):
            module.fetch_offershow_data(FakeSession(), target_date=date(2026, 4, 15))

    def test_fetch_offershow_data_degrades_when_token_not_logged_in(self):
        module = load_module()

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.headers = {"accesstoken": "expired-token"}

            def get(self, url, timeout):
                return FakeResponse(
                    {
                        "data": {
                            "company_tags": [
                                {"id": 4, "content": "IT/互联网"},
                            ]
                        }
                    }
                )

            def post(self, url, data, headers, timeout):
                return FakeResponse({"data": {"is_login": False, "is_recruit_vip": False, "plans": []}})

        with mock.patch.object(module, "check_offershow_token_expiry", return_value=None):
            result = module.fetch_offershow_data(FakeSession(), target_date=date(2026, 4, 15))

        self.assertEqual(result.degraded_reason, "token_not_login")

    def test_fetch_offershow_data_allows_expiring_token_with_warning(self):
        module = load_module()

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.headers = {"accesstoken": "soon-expiring-token"}

            def get(self, url, timeout):
                return FakeResponse(
                    {
                        "data": {
                            "company_tags": [
                                {"id": 4, "content": "IT/互联网"},
                            ]
                        }
                    }
                )

            def post(self, url, data, headers, timeout):
                return FakeResponse(
                    {
                        "data": {
                            "is_login": True,
                            "is_recruit_vip": True,
                            "plans": [
                                {
                                    "uuid": "a",
                                    "company_name": "A公司",
                                    "create_time": "2026-04-15T10:00:13+08:00",
                                    "company_many_tags": "4",
                                }
                            ],
                        }
                    }
                )

        with mock.patch.object(
            module,
            "check_offershow_token_expiry",
            return_value=module.OfferShowTokenExpiringSoon("OFFERSHOW_ACCESS_TOKEN 将在 2026-04-18 到期（剩余 1 天），请尽快续期。"),
        ):
            result = module.fetch_offershow_data(FakeSession(), target_date=date(2026, 4, 15))

        self.assertEqual(len(result.plans), 1)
        self.assertIn("2026-04-18", result.auth_warning)

    def test_fetch_offershow_data_returns_degraded_public_data_when_not_logged_in(self):
        module = load_module()

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self):
                self.headers = {"accesstoken": "vip-token"}

            def get(self, url, timeout):
                return FakeResponse(
                    {
                        "data": {
                            "company_tags": [
                                {"id": 4, "content": "IT/互联网"},
                            ]
                        }
                    }
                )

            def post(self, url, data, headers, timeout):
                return FakeResponse(
                    {
                        "data": {
                            "is_login": False,
                            "is_recruit_vip": False,
                            "plans": [
                                {
                                    "uuid": "a",
                                    "company_name": "公开公司",
                                    "create_time": "2026-04-15T10:00:13+08:00",
                                    "company_many_tags": "4",
                                }
                            ],
                        }
                    }
                )

        with mock.patch.object(module, "check_offershow_token_expiry", return_value=None):
            result = module.fetch_offershow_data(FakeSession(), target_date=date(2026, 4, 15))

        self.assertEqual(result.degraded_reason, "token_not_login")
        self.assertEqual(len(result.plans), 1)
        self.assertEqual(result.latest_public_date, date(2026, 4, 15))

    def test_render_ranked_news_item_uses_conservative_attribution_when_needed(self):
        module = load_module()
        ranked = module.RankedNewsCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 16),
            title="OpenAI 计划调整 ChatGPT 广告策略",
            summary_or_excerpt="OpenAI 正计划调整 ChatGPT 广告策略，并探索更清晰的商业化路径。",
            url="https://pingwest.com/openai-ads",
            raw_section="OpenAI",
            raw_order=1,
            notes="",
            dedupe_key="openai-chatgpt-ads",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=True,
            value_score=9.0,
            render_mode="downgraded",
        )

        rendered = module.render_ranked_news_item(ranked)

        self.assertIn("发现源称", rendered)
        self.assertIn("ChatGPT 广告策略", rendered)

    def test_render_ranked_news_item_trims_repeated_title_prefix(self):
        module = load_module()
        ranked = module.RankedNewsCandidate(
            source_name="爱范儿日报",
            source_date=date(2026, 4, 24),
            title="华为乾崑 ADS 5 正式发布：训练效率提升 10 倍，碰撞风险降低 50%",
            summary_or_excerpt="华为昨日在北京举行乾崑技术大会，以「安全有乾崑安心赴美好」为主题，正式发布乾崑智驾 ADS 5、鸿蒙座舱 HarmonySpace 6 等新一代智能汽车解决方案。",
            url="https://ifanr.com/ads5",
            raw_section="早报",
            raw_order=1,
            notes="",
            dedupe_key="huawei-ads5",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=False,
            value_score=9.0,
            render_mode="normal",
        )

        rendered = module.render_ranked_news_item(ranked)

        self.assertEqual(rendered.count("华为昨日在北京举行乾崑技术大会"), 1)
        self.assertIn("以「安全有乾崑安心赴美好」为主题", rendered)

    def test_rewrite_news_title_prefers_rewritten_summary_line(self):
        module = load_module()
        ranked = module.RankedNewsCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 16),
            title="从“对人开放”到“对AI开放”，飞书项目开放体系迎来系统性升级",
            summary_or_excerpt="4月23日，在上海举办的2026飞书项目生态日上，飞书项目集中发布并升级了一批面向AI时代的全新能力，包括全新的MCP能力、飞书项目CLI，以及面向智能体协作的AI应用体系等。",
            url="https://pingwest.com/feishu",
            raw_section="飞书",
            raw_order=1,
            notes="",
            dedupe_key="feishu-project-open",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=False,
            value_score=9.0,
            render_mode="normal",
        )

        rewritten, _ = module.rewrite_news_title(ranked)

        self.assertNotEqual(rewritten, ranked.title)
        self.assertIn("飞书项目", rewritten)

    def test_render_ranked_news_item_skips_title_source_sentence(self):
        module = load_module()
        ranked = module.RankedNewsCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 24),
            title="千里科技宣布2027年推出Robotaxi综合解决方案",
            summary_or_excerpt="2026年4月22日在北京举行的“行千里、AI 相伴”主题发布会，千里科技公布了其在自动驾驶出行服务领域的明确路线图。公司宣布，计划于2027年正式面向市场推出Robotaxi综合解决方案，并制定了清晰明确的市场目标。",
            url="https://pingwest.com/robotaxi",
            raw_section="千里",
            raw_order=1,
            notes="",
            dedupe_key="robotaxi-plan",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=False,
            value_score=9.0,
            render_mode="normal",
        )

        rendered = module.render_ranked_news_item(ranked)

        self.assertEqual(rendered.count("千里科技公布了其在自动驾驶出行服务领域的明确路线图"), 1)
        self.assertIn("计划于2027年正式面向市场推出Robotaxi综合解决方案", rendered)

    def test_render_ranked_news_item_does_not_split_english_product_name(self):
        module = load_module()
        ranked = module.RankedNewsCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 24),
            title="接入拓竹只是开始，AI 3D 赛道第一个盈利样本长什么样",
            summary_or_excerpt="2026年3月17日，拓竹科技把 Meshy 6 接进了 MakerWorld 的 MakerLab。一张照片上传上去，系统会自动生成可打印的3D模型。",
            url="https://pingwest.com/meshy",
            raw_section="拓竹",
            raw_order=1,
            notes="",
            dedupe_key="meshy-makerlab",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=False,
            value_score=9.0,
            render_mode="normal",
        )

        rendered = module.render_ranked_news_item(ranked)

        self.assertNotIn("Mak。erLab", rendered)
        self.assertNotRegex(rendered, r"\bMak。")
        self.assertIn("MakerLab", rendered)
        self.assertIn("拓竹科技接入 Meshy 6", rendered)
        self.assertNotIn("这件事放在今天看", rendered)

    def test_trim_repeated_title_prefix_drops_tiny_leftover_fragment(self):
        module = load_module()

        trimmed = module.trim_repeated_title_prefix(
            "知乎宣布第十二届新知青年大会将于5月16日至17日在北京798艺术区",
            "知乎宣布第十二届新知青年大会将于5月16日至17日在北京798艺术区举办。",
        )

        self.assertEqual(trimmed, "")

    def test_rank_news_candidates_prefers_multi_source_coverage_when_quality_is_sufficient(self):
        module = load_module()
        candidates = [
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="飞书项目开放体系升级",
                summary_or_excerpt="飞书项目升级 MCP 能力、CLI 与 AI 应用体系，推动人和 Agent 协同。",
                url="https://pingwest.com/feishu-project",
                raw_section="飞书",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="品玩实事要问",
                source_date=date(2026, 4, 16),
                title="千里科技公布 Robotaxi 路线图",
                summary_or_excerpt="千里科技宣布 2027 年推出 Robotaxi 综合解决方案，并给出 2030 年车队规模目标。",
                url="https://pingwest.com/robotaxi",
                raw_section="千里",
                raw_order=2,
            ),
            module.DiscoveryCandidate(
                source_name="少数派派早报",
                source_date=date(2026, 4, 16),
                title="OpenAI 发布 GPT-5.5 系列模型",
                summary_or_excerpt="OpenAI 推出 GPT-5.5，并强化编码、多工具协同和复杂任务执行能力。",
                url="https://sspai.com/post/gpt55",
                raw_section="互联网综合",
                raw_order=1,
            ),
            module.DiscoveryCandidate(
                source_name="爱范儿日报",
                source_date=date(2026, 4, 16),
                title="华为乾崑 ADS 5 正式发布",
                summary_or_excerpt="华为发布乾崑 ADS 5，新架构让训练效率提升 10 倍并降低碰撞风险。",
                url="https://ifanr.com/ads5",
                raw_section="早报",
                raw_order=1,
            ),
        ]

        picks = module.rank_news_candidates(candidates, limit=3)

        self.assertEqual(len(picks), 3)
        self.assertEqual(
            {item.source_name for item in picks},
            {"品玩实事要问", "少数派派早报", "爱范儿日报"},
        )

    def test_build_wechat_report_contains_two_message_sections(self):
        module = load_module()
        ranked_item = module.RankedNewsCandidate(
            source_name="少数派派早报",
            source_date=date(2026, 4, 14),
            title="微软开始测试类 OpenClaw 的 Copilot 服务",
            summary_or_excerpt="Microsoft 365 Copilot 可能支持自动处理待办。",
            url="https://example.com/copilot",
            raw_section="互联网综合",
            raw_order=1,
            notes="",
            dedupe_key="microsoft-copilot-openclaw",
            workplace_relevance=4.0,
            new_information_value=3.0,
            distribution_fit=2.0,
            readability_ok=True,
            needs_backcheck=True,
            value_score=9.0,
            render_mode="downgraded",
        )
        offer = module.OfferRecommendation(
            company_name="哔哩哔哩",
            industry="IT/互联网",
            created_at=datetime(
                2026, 4, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            ),
            title="哔哩哔哩2026年招募暑期实习生",
            positions="内容制作实习生、活动运营实习生",
            city="上海",
            source_url="https://example.com/bili",
            score=10.0,
        )

        report, news_report, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 14),
            ranked_news_candidates=[ranked_item],
            offers=[offer],
            target_offer_date=date(2026, 4, 13),
            latest_public_offer_date=date(2026, 4, 11),
        )

        self.assertIn("2026-04-14 行业日报", report)
        self.assertIn("📰 行业新闻", report)
        self.assertIn("职场速递｜昨日新增投递", report)
        self.assertIn("哔哩哔哩", report)
        self.assertNotIn("来源：", report)
        self.assertNotIn("Claude 获得真实浏览器控制权\n", report)
        self.assertIn("📰 行业新闻", news_report)
        self.assertNotIn("💼 职场速递", news_report)
        self.assertIn("💼 职场速递｜昨日新增投递", jobs_report)
        self.assertNotIn("📰 行业新闻", jobs_report)

    def test_build_wechat_report_surfaces_offershow_auth_hint(self):
        module = load_module()

        _, _, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 16),
            ranked_news_candidates=[],
            offers=[],
            target_offer_date=date(2026, 4, 15),
            source_errors={
                "offershow": "offershow_auth:当前 token 已失效或未登录，请重新获取 OFFERSHOW_ACCESS_TOKEN。"
            },
        )

        self.assertIn("当前 token 已失效或未登录", jobs_report)

    def test_build_wechat_report_surfaces_degraded_offershow_hint_even_with_offers(self):
        module = load_module()
        offers = [
            module.OfferRecommendation(
                company_name="公开公司",
                industry="IT/互联网",
                created_at=datetime(2026, 4, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                title="公开岗位",
                positions="研发工程师",
                city="北京",
                source_url="https://example.com/public",
                score=1.0,
            )
        ]

        _, _, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 16),
            ranked_news_candidates=[],
            offers=offers,
            target_offer_date=date(2026, 4, 15),
            source_errors={"offershow": "degraded_not_vip"},
        )

        self.assertIn("仅基于公开数据", jobs_report)
        self.assertIn("公开公司", jobs_report)

    def test_build_wechat_report_handles_source_failures_gracefully(self):
        module = load_module()

        report, news_report, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 14),
            ranked_news_candidates=[],
            offers=[],
            target_offer_date=date(2026, 4, 13),
            latest_public_offer_date=None,
            source_errors={"news_pool": "timeout", "offershow": "timeout"},
        )

        self.assertIn("暂未生成行业新闻", report)
        self.assertIn("OfferShow 抓取异常", jobs_report)
        self.assertIn("📰 行业新闻", news_report)

    def test_build_wechat_report_explains_public_api_lag_when_no_offers(self):
        module = load_module()

        report, _, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 14),
            ranked_news_candidates=[],
            offers=[],
            target_offer_date=date(2026, 4, 13),
            latest_public_offer_date=date(2026, 4, 11),
        )

        self.assertIn("2026-04-11", report)
        self.assertIn("OfferShow 公开接口当前最新招聘日期停留在 2026-04-11", jobs_report)

    def test_build_wechat_report_explains_filter_miss_when_offershow_has_data(self):
        module = load_module()

        _, _, jobs_report = module.build_wechat_report(
            report_date=date(2026, 4, 16),
            ranked_news_candidates=[],
            offers=[],
            target_offer_date=date(2026, 4, 15),
            offershow_diagnostics={
                "target_date": "2026-04-15",
                "total_plans": 50,
                "target_date_plan_count": 5,
                "target_tag_plan_count": 12,
                "matched_plan_count": 0,
            },
        )

        self.assertIn("OfferShow 已返回岗位数据，但未命中当前筛选条件", jobs_report)
        self.assertIn("目标日期 2026-04-15 命中 5 条", jobs_report)
        self.assertIn("四个目标方向命中 12 条", jobs_report)
        self.assertIn("本次共读取 50 条岗位记录", jobs_report)

    def test_generate_daily_report_includes_offershow_diagnostics_metadata(self):
        module = load_module()

        ranked_item = module.RankedNewsCandidate(
            source_name="品玩实事要问",
            source_date=date(2026, 4, 16),
            title="示例新闻",
            summary_or_excerpt="示例摘要",
            url="https://example.com/news",
            raw_section="互联网综合",
            raw_order=1,
            notes="",
            dedupe_key="example-news",
            workplace_relevance=3.0,
            new_information_value=3.0,
            distribution_fit=3.0,
            readability_ok=True,
            needs_backcheck=False,
            value_score=9.0,
            render_mode="normal",
        )
        offershow_result = module.OfferFetchResult(
            tag_map={4: "IT/互联网"},
            plans=[
                {
                    "uuid": "job-a",
                    "company_name": "示例公司",
                    "create_time": "2026-04-15T10:00:13+08:00",
                    "company_many_tags": "99",
                }
            ],
            latest_public_date=date(2026, 4, 16),
            degraded_reason=None,
            auth_warning=None,
        )

        with mock.patch.object(
            module,
            "collect_report_mode_candidate_pool",
            return_value=(
                [
                    module.SourceCollectionResult(
                        source_name="品玩实事要问",
                        entry_url="https://example.com/pingwest",
                        fetch_status="ok",
                        candidates=[
                            module.DiscoveryCandidate(
                                source_name="品玩实事要问",
                                source_date=date(2026, 4, 16),
                                title="示例新闻",
                                summary_or_excerpt="示例摘要",
                                url="https://example.com/news",
                                raw_section="互联网综合",
                                raw_order=1,
                            )
                        ],
                        raw_documents=[],
                        error=None,
                    )
                ],
                {},
                {"品玩实事要问": date(2026, 4, 16)},
            ),
        ), mock.patch.object(
            module,
            "rank_news_candidates",
            return_value=[ranked_item],
        ), mock.patch.object(
            module,
            "fetch_offershow_data",
            return_value=offershow_result,
        ):
            report, news_report, jobs_report, metadata = module.generate_daily_report(
                date(2026, 4, 16)
            )

        self.assertIn("未命中当前筛选条件", jobs_report)
        self.assertEqual(
            metadata["offershow_diagnostics"],
            {
                "target_date": "2026-04-15",
                "total_plans": 1,
                "target_date_plan_count": 1,
                "target_tag_plan_count": 0,
                "matched_plan_count": 0,
            },
        )
        self.assertEqual(metadata["messages"], [news_report, jobs_report])
        self.assertIn("💼 职场速递｜昨日新增投递", report)

    def test_main_prints_two_messages_with_separator(self):
        module = load_module()
        fake_stdout = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            ["generate_daily_report.py", "--date", "2026-04-14"],
        ), mock.patch.object(
            module,
            "generate_daily_report",
            return_value=(
                "full",
                "news message",
                "jobs message",
                {},
            ),
        ), mock.patch("sys.stdout", fake_stdout):
            result = module.main()

        self.assertEqual(result, 0)
        output = fake_stdout.getvalue()
        self.assertIn("news message", output)
        self.assertIn("jobs message", output)
        self.assertIn(module.STDOUT_MESSAGE_BREAK, output)

    def test_generate_daily_report_uses_unified_candidate_pool_and_preserves_jobs(self):
        module = load_module()
        unified_results = [
            module.SourceCollectionResult(
                source_name="品玩实事要问",
                entry_url="https://www.pingwest.com/status",
                fetch_status="ok",
                candidates=[
                    module.DiscoveryCandidate(
                        source_name="品玩实事要问",
                        source_date=date(2026, 4, 16),
                        title="Google 宣布整治返回键劫持行为",
                        summary_or_excerpt="Google 将从 6月15日起将返回键劫持正式定性为恶意行为并开展专项整治。",
                        url="https://pingwest.com/google",
                        raw_section="Google",
                        raw_order=1,
                    ),
                    module.DiscoveryCandidate(
                        source_name="品玩实事要问",
                        source_date=date(2026, 4, 16),
                        title="OpenAI 计划调整 ChatGPT 广告策略",
                        summary_or_excerpt="据 The Information 报道，OpenAI 正计划调整 ChatGPT 广告策略。",
                        url="https://pingwest.com/openai",
                        raw_section="OpenAI",
                        raw_order=2,
                    ),
                ],
                raw_documents=[],
            ),
            module.SourceCollectionResult(
                source_name="少数派派早报",
                entry_url="https://sspai.com/tag/%E6%B4%BE%E6%97%A9%E6%8A%A5",
                fetch_status="ok",
                candidates=[],
                raw_documents=[],
            ),
            module.SourceCollectionResult(
                source_name="爱范儿日报",
                entry_url="https://www.ifanr.com/category/ifanrnews",
                fetch_status="ok",
                candidates=[],
                raw_documents=[],
            ),
        ]
        offershow = module.OfferFetchResult(
            tag_map={4: "IT/互联网"},
            plans=[],
            latest_public_date=date(2026, 4, 11),
        )
        offers = [
            module.OfferRecommendation(
                company_name="哔哩哔哩",
                industry="IT/互联网",
                created_at=datetime(2026, 4, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                title="哔哩哔哩2026年招募暑期实习生",
                positions="内容制作实习生、活动运营实习生",
                city="上海",
                source_url="https://example.com/bili",
                score=10.0,
            )
        ]

        source_windows = {
            "品玩实事要问": date(2026, 4, 15),
            "少数派派早报": date(2026, 4, 16),
            "爱范儿日报": date(2026, 4, 16),
        }

        with mock.patch.object(
            module,
            "collect_report_mode_candidate_pool",
            return_value=(unified_results, {}, source_windows),
        ) as collect_pool_mock, mock.patch.object(
            module,
            "fetch_offershow_data",
            return_value=offershow,
        ), mock.patch.object(
            module,
            "select_offer_recommendations",
            return_value=offers,
        ):
            report, news_report, jobs_report, metadata = module.generate_daily_report(date(2026, 4, 16))

        self.assertEqual(collect_pool_mock.call_args.args[1], date(2026, 4, 16))
        self.assertIn("返回键劫持正式定性为恶意行为", news_report)
        self.assertIn("哔哩哔哩", jobs_report)
        self.assertIn("ranked_news_candidates", metadata)
        self.assertEqual(metadata["news_candidate_date"], "2026-04-16")
        self.assertEqual(
            metadata["candidate_source_windows"],
            {
                "品玩实事要问": "2026-04-15",
                "少数派派早报": "2026-04-16",
                "爱范儿日报": "2026-04-16",
            },
        )
        self.assertEqual(metadata["candidate_sources"][0]["target_date"], "2026-04-15")
        self.assertLessEqual(len(metadata["ranked_news_candidates"]), 10)
        self.assertEqual(metadata["offers"][0]["company_name"], "哔哩哔哩")

    def test_collect_report_mode_candidate_pool_uses_source_specific_dates(self):
        module = load_module()
        session = mock.Mock()
        pingwest_result = module.SourceCollectionResult(
            source_name="品玩实事要问",
            entry_url=module.PINGWEST_STATUS_URL,
            fetch_status="ok",
            candidates=[],
            raw_documents=[],
        )
        sspai_result = module.SourceCollectionResult(
            source_name="少数派派早报",
            entry_url=module.SSPAI_MORNING_PAPER_TAG_URL,
            fetch_status="ok",
            candidates=[],
            raw_documents=[],
        )
        ifanr_result = module.SourceCollectionResult(
            source_name="爱范儿日报",
            entry_url=module.IFANR_NEWS_CATEGORY_URL,
            fetch_status="ok",
            candidates=[],
            raw_documents=[],
        )

        with mock.patch.object(
            module, "collect_pingwest_candidates", return_value=pingwest_result
        ) as pingwest_mock, mock.patch.object(
            module, "collect_sspai_candidates", return_value=sspai_result
        ) as sspai_mock, mock.patch.object(
            module, "collect_ifanr_candidates", return_value=ifanr_result
        ) as ifanr_mock:
            results, errors, windows = module.collect_report_mode_candidate_pool(
                session, date(2026, 4, 16)
            )

        self.assertEqual(errors, {})
        self.assertEqual([result.source_name for result in results], ["品玩实事要问", "少数派派早报", "爱范儿日报"])
        pingwest_mock.assert_called_once_with(session, date(2026, 4, 15), lookback_days=0)
        sspai_mock.assert_called_once_with(session, date(2026, 4, 16), lookback_days=0)
        ifanr_mock.assert_called_once_with(session, date(2026, 4, 16), lookback_days=0)
        self.assertEqual(
            {name: target.isoformat() for name, target in windows.items()},
            {
                "品玩实事要问": "2026-04-15",
                "少数派派早报": "2026-04-16",
                "爱范儿日报": "2026-04-16",
            },
        )

    def test_write_collection_outputs_creates_inventory_pool_and_raw_dump(self):
        module = load_module()
        results = [
            module.SourceCollectionResult(
                source_name="品玩实事要问",
                entry_url="https://www.pingwest.com/status",
                fetch_status="ok",
                candidates=[
                    module.DiscoveryCandidate(
                        source_name="品玩实事要问",
                        source_date=date(2026, 4, 16),
                        title="Adobe 推出 Firefly AI Assistant",
                        summary_or_excerpt="Adobe 官方宣布推出 Firefly AI Assistant。",
                        url="https://www.pingwest.com/w/312963",
                        raw_section="Adobe",
                        raw_order=1,
                    )
                ],
                raw_documents=[
                    module.RawDocument(
                        source_name="品玩实事要问",
                        document_label="status-page-1",
                        url="https://www.pingwest.com/api/state/list?last_id=",
                        content="今天 (4月16日, 周四)\nAdobe 推出 Firefly AI Assistant",
                    )
                ],
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = module.write_collection_outputs(
                date(2026, 4, 16),
                lookback_days=2,
                results=results,
                source_errors={},
                output_dir=Path(tmpdir),
            )

            self.assertTrue(Path(manifest["source_inventory"]).exists())
            self.assertTrue(Path(manifest["candidate_pool_markdown"]).exists())
            self.assertTrue(Path(manifest["candidate_pool_json"]).exists())
            raw_dump_path = Path(manifest["raw_dump_dir"]) / "pingwest-status.md"
            self.assertTrue(raw_dump_path.exists())
            files = sorted(p.name for p in Path(manifest["raw_dump_dir"]).iterdir())
            self.assertIn("pingwest-status.md", files)

    def test_main_collection_mode_prints_manifest(self):
        module = load_module()
        fake_stdout = io.StringIO()
        fake_results = [
            module.SourceCollectionResult(
                source_name="品玩实事要问",
                entry_url="https://www.pingwest.com/status",
                fetch_status="ok",
                candidates=[],
                raw_documents=[],
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            sys,
            "argv",
            [
                "generate_daily_report.py",
                "--mode",
                "collection",
                "--date",
                "2026-04-16",
                "--collection-output-dir",
                tmpdir,
            ],
        ), mock.patch.object(
            module,
            "collect_candidate_pool",
            return_value=(fake_results, {}),
        ), mock.patch("sys.stdout", fake_stdout):
            result = module.main()
            self.assertEqual(result, 0)
            manifest = json.loads(fake_stdout.getvalue())
            self.assertEqual(manifest["output_dir"], tmpdir)
            self.assertTrue(Path(manifest["candidate_pool_json"]).exists())


if __name__ == "__main__":
    unittest.main()
