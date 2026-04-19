"""Tab 3: 文章生成 - 选择热点/输入主题、一键生成（含配图）、预览"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import gradio as gr
from loguru import logger

from agent.content_agent import ContentAgent
from agent.tools import HotTopicTool
from config.settings import PROJECT_ROOT, settings
from image_gen.cartoon_gen import WanxImageGenerator
from image_gen.prompt_builder import CartoonPromptBuilder
from image_gen.scene_extractor import SceneExtractor
from models.generated_store import get_generated_store
from utils.image_cache import image_cache
from utils.publish_tags import append_publish_tags

ARTICLE_IMAGE_COUNT = 1
MAX_BATCH_COUNT = 20

_batch_lock = threading.Lock()
_batch_state = {
    "running": False,
    "cancel": False,
    "scheduled_at": "",
    "started_at": "",
    "finished_at": "",
    "target_count": 0,
    "completed": 0,
    "published": 0,
    "failed": 0,
    "current_topic": "",
    "messages": [],
    "thread": None,
}


def create_generate_tab() -> gr.Blocks:
    """创建文章生成 Tab"""
    with gr.Blocks() as tab:
        gr.Markdown("## 文章生成")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 热点话题")
                refresh_btn = gr.Button("获取最新热点", variant="secondary")
                hot_topics_radio = gr.Radio(
                    choices=["（点击获取热点）"],
                    value="（点击获取热点）",
                    label="选择热点",
                )
                gr.Markdown("### 或自定义主题")
                custom_topic = gr.Textbox(
                    label="自定义主题",
                    placeholder="例如：35岁后如何通过副业实现收入翻倍",
                )
                generate_btn = gr.Button("生成文章+配图", variant="primary", size="lg")

                with gr.Accordion("批量自动发布", open=False):
                    batch_count = gr.Slider(
                        minimum=1,
                        maximum=MAX_BATCH_COUNT,
                        value=3,
                        step=1,
                        label="生成并发布篇数",
                    )
                    batch_start_time = gr.Textbox(
                        label="开始时间",
                        placeholder="留空=立即；支持 2026-04-19 20:00 或 20:00",
                    )
                    with gr.Row():
                        batch_start_btn = gr.Button("启动批量任务", variant="primary")
                        batch_stop_btn = gr.Button("停止任务", variant="stop")
                        batch_refresh_btn = gr.Button("刷新状态", variant="secondary")
                    batch_status = gr.Textbox(
                        label="批量任务状态",
                        interactive=False,
                        lines=10,
                        max_lines=20,
                    )

            with gr.Column(scale=2):
                gr.Markdown("### 生成结果")
                title_output = gr.Textbox(
                    label="爆款标题",
                    interactive=False,
                    lines=2,
                )
                word_count = gr.Textbox(
                    label="字数统计",
                    interactive=False,
                )
                content_output = gr.Textbox(
                    label="文章正文",
                    interactive=False,
                    lines=20,
                )

                gr.Markdown("### 卡通配图")
                with gr.Row():
                    img1 = gr.Image(label="配图1", height=250)
                    img2 = gr.Image(label="配图2", height=250, visible=False)
                with gr.Row():
                    img3 = gr.Image(label="配图3", height=250, visible=False)
                    img4 = gr.Image(label="配图4", height=250, visible=False)

                gen_status = gr.Textbox(label="生成状态", interactive=False)

        refresh_btn.click(
            fn=refresh_hot_topics,
            outputs=[hot_topics_radio],
        )

        generate_btn.click(
            fn=generate_article,
            inputs=[hot_topics_radio, custom_topic],
            outputs=[title_output, word_count, content_output,
                     img1, img2, img3, img4, gen_status],
        )

        batch_start_btn.click(
            fn=start_batch_auto_publish,
            inputs=[batch_count, batch_start_time],
            outputs=[batch_status],
        )
        batch_stop_btn.click(fn=stop_batch_auto_publish, outputs=[batch_status])
        batch_refresh_btn.click(fn=get_batch_status, outputs=[batch_status])

    return tab


# 缓存最新文章和话题（供图片Tab和发布Tab使用）
_latest_article = None
_latest_topics = None


def refresh_hot_topics():
    global _latest_topics
    try:
        tool = HotTopicTool()
        topics = tool.fetch_hot_topics(max_topics=15)
        _latest_topics = topics
        choices = [f"{t['title']} (热度:{t.get('heat', '?')})" for t in topics]
        if not choices:
            return ["无可用热点"]
        return gr.update(choices=choices, value=choices[0])
    except Exception as e:
        logger.error(f"获取热点失败: {e}")
        return [f"获取失败: {e}"]


def generate_article(hot_topic: str, custom_topic: str):
    """生成文章 + 自动生成配图（优先使用缓存）"""
    global _latest_article

    topic = custom_topic.strip() if custom_topic.strip() else ""
    if not topic and hot_topic:
        # 兼容英文括号和中文括号: "标题 (热度:xxx)" 或 "标题（热度:xxx）"
        for sep in [" (", "（"]:
            if sep in hot_topic:
                topic = hot_topic.split(sep)[0].strip()
                break
        else:
            topic = hot_topic.strip()

    # 过滤无效默认值
    if topic in ("（点击获取热点）", "无可用热点", ""):
        topic = ""

    logger.info(f"[生成] hot_topic={hot_topic!r}, custom_topic={custom_topic!r}, topic={topic!r}")

    if not topic:
        yield "请选择热点或输入自定义主题", "", "", None, None, None, None, ""
        return

    # Phase 1: 生成文章
    yield "正在生成文章...", "", "", None, None, None, None, "Step 1/2: 生成文章中..."

    try:
        agent = ContentAgent()
        article = agent.generate_article(
            hot_topic=topic if not custom_topic.strip() else "",
            custom_topic=custom_topic.strip(),
        )
        _latest_article = article
    except Exception as e:
        logger.error(f"文章生成失败: {e}")
        yield f"生成失败: {e}", "", "", None, None, None, None, f"文章生成失败: {e}"
        return

    # 保存到持久化存储
    try:
        store = get_generated_store()
        store.add(article)
        logger.info(f"[生成] 文章已缓存: {article.id}")
    except Exception as e:
        logger.warning(f"[生成] 文章缓存保存失败: {e}")

    # 先输出文章内容
    yield (
        article.title,
        f"字数: {article.word_count} | 热点: {article.hot_topic}",
        article.content,
        None, None, None, None,
        "Step 2/2: 生成配图中...",
    )

    # Phase 2: 生成配图（优先使用缓存）
    try:
        # 检查缓存
        cached = image_cache.get(article.id)
        if cached and len(cached.get("image_paths", [])) >= ARTICLE_IMAGE_COUNT:
            logger.info(f"[生成] 使用已缓存的配图: {article.id}")
            article.scenes = cached["scenes"][:ARTICLE_IMAGE_COUNT]
            article.image_paths = cached["image_paths"][:ARTICLE_IMAGE_COUNT]
            current_paths = article.image_paths + [None] * (4 - len(article.image_paths))
            yield (
                article.title,
                f"字数: {article.word_count} | 热点: {article.hot_topic}",
                article.content,
                *current_paths[:4],
                "配图已从缓存加载",
            )
            return

        # 需要生成配图
        scenes = article.scenes
        if not scenes or len(scenes) < ARTICLE_IMAGE_COUNT:
            extractor = SceneExtractor()
            scenes = extractor.extract(article.content, n=ARTICLE_IMAGE_COUNT)

        while len(scenes) < ARTICLE_IMAGE_COUNT:
            scenes.append("职场成长主题，积极向上的卡通插画")

        article.scenes = scenes[:ARTICLE_IMAGE_COUNT]

        # 按文章 ID 创建输出目录
        from pathlib import Path
        from config.settings import PROJECT_ROOT
        article_img_dir = PROJECT_ROOT / "output" / "images" / article.id
        article_img_dir.mkdir(parents=True, exist_ok=True)

        generator = WanxImageGenerator(output_dir=article_img_dir)
        prompt_builder = CartoonPromptBuilder()

        all_paths = []
        for i, scene in enumerate(article.scenes):
            prompt = prompt_builder.build(scene)
            logger.info(f"[生成] 配图 {i+1}/{ARTICLE_IMAGE_COUNT}: {scene[:30]}...")

            paths = generator.generate(prompt=prompt, n=1)
            if paths:
                all_paths.append(paths[0])
                logger.info(f"[生成] 配图 {i+1} 完成: {paths[0]}")
            else:
                all_paths.append(None)
                logger.warning(f"[生成] 配图 {i+1} 失败")

            # 逐张更新 UI
            current_paths = all_paths + [None] * (4 - len(all_paths))
            yield (
                article.title,
                f"字数: {article.word_count} | 热点: {article.hot_topic}",
                article.content,
                *current_paths[:4],
                f"配图生成中 {i+1}/{ARTICLE_IMAGE_COUNT}...",
            )

        # 补齐到 4 张
        while len(all_paths) < 4:
            all_paths.append(None)

        article.image_paths = [p for p in all_paths[:ARTICLE_IMAGE_COUNT] if p]

        # 保存缓存
        image_cache.save(article.id, article.scenes, article.image_paths)

        n_success = sum(1 for p in all_paths[:ARTICLE_IMAGE_COUNT] if p)
        status_msg = f"生成完成: 文章 + {n_success}/{ARTICLE_IMAGE_COUNT} 张配图"

        # 自动发布
        if settings.publisher.auto_publish:
            try:
                from publisher.toutiao_publisher import get_toutiao_publisher
                publisher = get_toutiao_publisher()
                img_paths = [p for p in all_paths[:ARTICLE_IMAGE_COUNT] if p]

                dynamic_topics = _extract_dynamic_topics(article.hot_topic)
                publish_content = _get_publish_content(article, dynamic_topics)

                if settings.publisher.publish_type == "micro_toutiao":
                    result = publisher.publish_micro_toutiao(
                        content=publish_content,
                        image_paths=img_paths,
                        topics=dynamic_topics,
                        location=settings.publisher.default_location,
                    )
                else:
                    result = publisher.publish_article(
                        title=article.title,
                        content=publish_content,
                        image_paths=img_paths,
                        category=settings.publisher.default_category,
                    )
                if result.success:
                    article.content = publish_content
                    get_generated_store().update(
                        article.id,
                        status="published",
                        content=publish_content,
                        published_at=result.published_at,
                    )
                    status_msg += " | ✅ 已自动发布"
                else:
                    status_msg += f" | ❌ 自动发布失败: {result.error}"
            except Exception as e:
                status_msg += f" | ⚠️ 自动发布异常: {e}"

        yield (
            article.title,
            f"字数: {article.word_count} | 热点: {article.hot_topic}",
            article.content,
            *all_paths[:4],
            status_msg,
        )

    except Exception as e:
        logger.error(f"配图生成失败: {e}")
        yield (
            article.title,
            f"字数: {article.word_count} | 热点: {article.hot_topic}",
            article.content,
            None, None, None, None,
            f"配图生成失败: {e}（文章已生成）",
        )


def get_latest_article():
    """供其他 Tab 获取最新生成的文章（优先从内存缓存，其次从持久化存储）"""
    if _latest_article:
        return _latest_article
    # 从持久化存储加载最新一篇
    try:
        store = get_generated_store()
        articles = store.get_all()
        if articles:
            return articles[0]
    except Exception:
        pass
    return None


def _extract_dynamic_topics(hot_topic: str, max_count: int = 2) -> list[str]:
    topics: list[str] = []
    if hot_topic:
        for t in hot_topic.replace("，", ",").split(","):
            t = t.strip()
            if t and len(topics) < max_count:
                topics.append(t)
    return topics


def _get_publish_content(article, extra_topics: list[str] | None = None) -> str:
    return append_publish_tags(
        article.content,
        title=article.title,
        hot_topic=article.hot_topic,
        extra_topics=extra_topics,
    )


# ── 批量自动发布 ──────────────────────────────────────

def start_batch_auto_publish(count: int, start_time_text: str = "") -> str:
    """启动后台批量生成发布任务。"""
    count = int(count or 1)
    count = max(1, min(count, MAX_BATCH_COUNT))

    try:
        start_at = _parse_start_time(start_time_text)
    except ValueError as e:
        return f"开始时间格式错误: {e}"

    with _batch_lock:
        if _batch_state["running"]:
            return _format_batch_status("已有批量任务正在运行，请先停止或等待完成。")

        _batch_state.update({
            "running": True,
            "cancel": False,
            "scheduled_at": start_at.isoformat(timespec="seconds"),
            "started_at": "",
            "finished_at": "",
            "target_count": count,
            "completed": 0,
            "published": 0,
            "failed": 0,
            "current_topic": "",
            "messages": [],
        })

        thread = threading.Thread(
            target=_run_batch_job,
            args=(count, start_at),
            daemon=True,
            name="batch_auto_publish",
        )
        _batch_state["thread"] = thread
        thread.start()

    return _format_batch_status(f"批量任务已创建，计划开始时间: {start_at.strftime('%Y-%m-%d %H:%M:%S')}")


def stop_batch_auto_publish() -> str:
    """请求停止后台批量任务。"""
    with _batch_lock:
        if not _batch_state["running"]:
            return _format_batch_status("当前没有运行中的批量任务。")
        _batch_state["cancel"] = True
    return _format_batch_status("已请求停止任务；当前文章处理结束后会停止。")


def get_batch_status() -> str:
    """获取批量任务状态。"""
    return _format_batch_status()


def _parse_start_time(value: str | None) -> datetime:
    """解析开始时间；留空表示立即，HH:MM 如果已过则顺延到明天。"""
    text = (value or "").strip().replace("T", " ")
    now = datetime.now()
    if not text:
        return now

    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.startswith("%H"):
                parsed = now.replace(
                    hour=parsed.hour,
                    minute=parsed.minute,
                    second=getattr(parsed, "second", 0),
                    microsecond=0,
                )
                if parsed < now:
                    parsed += timedelta(days=1)
            return parsed
        except ValueError:
            continue

    raise ValueError("请使用 YYYY-MM-DD HH:MM、YYYY-MM-DD HH:MM:SS、HH:MM 或留空")


def _run_batch_job(count: int, start_at: datetime):
    try:
        _batch_log(f"任务等待开始: {start_at.strftime('%Y-%m-%d %H:%M:%S')}")
        while datetime.now() < start_at:
            if _batch_should_cancel():
                _finish_batch("任务已在开始前取消")
                return
            time.sleep(min(5, max(0.5, (start_at - datetime.now()).total_seconds())))

        with _batch_lock:
            _batch_state["started_at"] = datetime.now().isoformat(timespec="seconds")
        _batch_log("开始获取热点...")

        topics = _fetch_distinct_hot_topics(count)
        if not topics:
            _finish_batch("未获取到可用热点，任务结束")
            return

        if len(topics) < count:
            _batch_log(f"可用热点不足 {count} 条，本次将生成 {len(topics)} 篇")

        for idx, topic in enumerate(topics[:count], start=1):
            if _batch_should_cancel():
                _finish_batch("任务已停止")
                return

            topic_title = topic.get("title", "").strip()
            with _batch_lock:
                _batch_state["current_topic"] = topic_title
            _batch_log(f"[{idx}/{count}] 开始生成: {topic_title}")

            try:
                article = _generate_article_for_topic(topic_title)
                _set_latest_article(article)
                get_generated_store().add(article)
                _batch_log(f"[{idx}/{count}] 文章生成完成: {article.title}")

                image_paths = _generate_single_image_for_article(article)
                _batch_log(f"[{idx}/{count}] 配图完成: {len(image_paths)} 张")

                success, publish_msg = _publish_article_for_batch(article)
                with _batch_lock:
                    _batch_state["completed"] += 1
                    if success:
                        _batch_state["published"] += 1
                    else:
                        _batch_state["failed"] += 1
                _batch_log(f"[{idx}/{count}] {publish_msg}")

            except Exception as e:
                logger.error(f"[批量] 处理失败: {e}")
                with _batch_lock:
                    _batch_state["completed"] += 1
                    _batch_state["failed"] += 1
                _batch_log(f"[{idx}/{count}] 失败: {e}")

        _finish_batch("批量任务完成")

    except Exception as e:
        logger.error(f"[批量] 任务异常: {e}")
        _finish_batch(f"批量任务异常: {e}")


def _fetch_distinct_hot_topics(count: int) -> list[dict]:
    tool = HotTopicTool()
    topics = tool.fetch_hot_topics(max_topics=max(count * 3, 15))
    result = []
    seen = set()
    for topic in topics:
        title = topic.get("title", "").strip()
        key = title.replace(" ", "").lower()
        if title and key not in seen:
            seen.add(key)
            result.append(topic)
        if len(result) >= count:
            break
    return result


def _generate_article_for_topic(topic: str):
    agent = ContentAgent()
    return agent.generate_article(hot_topic=topic, custom_topic="")


def _generate_single_image_for_article(article) -> list[str]:
    scenes = article.scenes
    if not scenes:
        extractor = SceneExtractor()
        scenes = extractor.extract(article.content, n=ARTICLE_IMAGE_COUNT)
    article.scenes = (scenes or ["职场成长主题，积极向上的卡通插画"])[:ARTICLE_IMAGE_COUNT]

    article_img_dir = PROJECT_ROOT / "output" / "images" / article.id
    article_img_dir.mkdir(parents=True, exist_ok=True)

    generator = WanxImageGenerator(output_dir=article_img_dir)
    prompt_builder = CartoonPromptBuilder()
    prompt = prompt_builder.build(article.scenes[0])
    paths = generator.generate(prompt=prompt, n=1)
    article.image_paths = paths[:ARTICLE_IMAGE_COUNT]
    image_cache.save(article.id, article.scenes, article.image_paths)
    get_generated_store().update(article.id, scenes=article.scenes, image_paths=article.image_paths)
    return article.image_paths


def _publish_article_for_batch(article) -> tuple[bool, str]:
    from publisher.toutiao_publisher import get_toutiao_publisher

    publisher = get_toutiao_publisher()
    image_paths = article.image_paths[:ARTICLE_IMAGE_COUNT]

    if settings.publisher.publish_type == "micro_toutiao":
        dynamic_topics = _extract_dynamic_topics(article.hot_topic)
        publish_content = _get_publish_content(article, dynamic_topics)
        result = publisher.publish_micro_toutiao(
            content=publish_content,
            image_paths=image_paths,
            topics=dynamic_topics,
            location=settings.publisher.default_location,
        )
    else:
        publish_content = _get_publish_content(article)
        result = publisher.publish_article(
            title=article.title,
            content=publish_content,
            image_paths=image_paths,
            category=settings.publisher.default_category,
        )

    if result.success:
        get_generated_store().update(
            article.id,
            status="published",
            content=publish_content,
            published_at=result.published_at,
            published_url=result.published_url,
        )
        article.content = publish_content
        return True, f"发布成功: {article.title}"
    return False, f"发布失败: {result.error}"


def _set_latest_article(article):
    global _latest_article
    _latest_article = article


def _batch_should_cancel() -> bool:
    with _batch_lock:
        return bool(_batch_state["cancel"])


def _batch_log(message: str):
    logger.info(f"[批量] {message}")
    with _batch_lock:
        messages = _batch_state["messages"]
        messages.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
        del messages[:-60]


def _finish_batch(message: str):
    _batch_log(message)
    with _batch_lock:
        _batch_state["running"] = False
        _batch_state["cancel"] = False
        _batch_state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        _batch_state["current_topic"] = ""


def _format_batch_status(prefix: str = "") -> str:
    with _batch_lock:
        lines = []
        if prefix:
            lines.append(prefix)
        lines.extend([
            f"运行中: {'是' if _batch_state['running'] else '否'}",
            f"计划开始: {_batch_state['scheduled_at'] or '-'}",
            f"实际开始: {_batch_state['started_at'] or '-'}",
            f"结束时间: {_batch_state['finished_at'] or '-'}",
            f"目标篇数: {_batch_state['target_count']}",
            f"已完成: {_batch_state['completed']}",
            f"已发布: {_batch_state['published']}",
            f"失败: {_batch_state['failed']}",
            f"当前热点: {_batch_state['current_topic'] or '-'}",
        ])
        if _batch_state["messages"]:
            lines.append("")
            lines.append("日志:")
            lines.extend(_batch_state["messages"][-20:])
        return "\n".join(lines)
