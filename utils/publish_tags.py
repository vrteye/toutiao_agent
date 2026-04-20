"""发布标签选择与正文组装工具"""
from __future__ import annotations

import re
from collections.abc import Iterable


FALLBACK_PUBLISH_TAGS = ("热点",)

_HASHTAG_RE = re.compile(r"#([^#\s][^#\n]{0,30}?)#")

_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("职场", ("职场", "上班", "公司", "领导", "同事", "老板", "员工", "职业", "跳槽", "升职", "加班", "面试", "简历", "打工", "管理")),
    ("副业搞钱", ("副业", "搞钱", "赚钱", "变现", "收入", "兼职", "现金流", "接单", "生意", "创业", "私域", "项目")),
    ("个人成长", ("个人成长", "成长", "认知", "自律", "复盘", "习惯", "学习", "提升", "长期主义", "心态", "能力", "改变")),
    ("AI工具", ("AI", "人工智能", "大模型", "提示词", "自动化", "智能体", "工具")),
    ("自媒体", ("自媒体", "账号", "流量", "内容", "短视频", "直播", "涨粉", "爆款", "创作者")),
    ("普通人创业", ("创业", "普通人", "小生意", "轻创业", "商业模式", "客户", "获客")),
    ("理财", ("理财", "存钱", "投资", "基金", "股票", "资产", "负债", "财务自由")),
    ("科技", ("科技", "芯片", "手机", "新能源", "机器人", "算力", "数据中心", "硬件", "软件", "互联网")),
    ("教育", ("教育", "学校", "老师", "学生", "高考", "考研", "考试", "课堂", "学历", "培训")),
    ("情感", ("情感", "婚姻", "恋爱", "夫妻", "分手", "家庭关系", "伴侣", "沟通")),
    ("生活", ("生活", "日常", "家务", "租房", "通勤", "消费", "习惯", "烟火气")),
    ("健康", ("健康", "睡眠", "运动", "减肥", "饮食", "医生", "医院", "焦虑", "心理")),
    ("亲子", ("亲子", "孩子", "父母", "育儿", "家庭教育", "陪伴", "妈妈", "爸爸")),
    ("消费", ("消费", "买", "价格", "降价", "优惠", "品牌", "门店", "外卖", "电商")),
    ("房产", ("房产", "房子", "买房", "租房", "楼市", "房贷", "小区", "装修")),
    ("汽车", ("汽车", "车", "新能源车", "电车", "油车", "驾驶", "续航", "车企")),
    ("旅游", ("旅游", "旅行", "景区", "酒店", "机票", "出行", "攻略", "城市")),
    ("美食", ("美食", "吃", "餐厅", "做饭", "菜", "咖啡", "茶", "夜宵")),
    ("娱乐", ("娱乐", "明星", "电影", "电视剧", "综艺", "演唱会", "票房", "艺人")),
    ("体育", ("体育", "比赛", "足球", "篮球", "跑步", "运动员", "冠军", "联赛")),
    ("社会热点", ("社会", "热点", "事件", "网友", "舆论", "回应", "官方", "争议")),
    ("财经", ("财经", "经济", "市场", "企业", "公司", "股价", "营收", "利润", "财报")),
    ("法律", ("法律", "维权", "合同", "法院", "律师", "判决", "赔偿", "侵权")),
    ("读书", ("读书", "书", "阅读", "作者", "写作", "知识", "笔记")),
)


def normalize_publish_tag(tag: str) -> str:
    """把 #标签#、空格等输入规整为干净标签名。"""
    return (tag or "").strip().strip("#").strip()


def extract_publish_tags(text: str) -> list[str]:
    """提取正文里已经存在的 #标签#。"""
    tags: list[str] = []
    seen: set[str] = set()
    for raw in _HASHTAG_RE.findall(text or ""):
        tag = normalize_publish_tag(raw)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def format_publish_tags(tags: Iterable[str]) -> str:
    """格式化成头条正文可见的 #标签# #标签#。"""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = normalize_publish_tag(raw)
        if tag and tag not in seen:
            seen.add(tag)
            cleaned.append(tag)
    return " ".join(f"#{tag}#" for tag in cleaned)


def select_publish_tags(
    title: str = "",
    content: str = "",
    hot_topic: str = "",
    extra_topics: Iterable[str] | None = None,
    max_tags: int = 3,
) -> list[str]:
    """根据文章内容选择发布标签；不把某一类标签硬塞给所有文章。"""
    text = "\n".join([title or "", hot_topic or "", content or ""]).lower()

    scores: dict[str, int] = {}
    for tag, keywords in _TAG_RULES:
        score = 0
        for keyword in keywords:
            score += text.count(keyword.lower())
        if score:
            scores[tag] = score

    selected: list[str] = []
    for source in [extract_publish_tags(content)]:
        for raw in source:
            tag = normalize_publish_tag(raw)
            if _is_usable_tag(tag) and tag not in selected:
                selected.append(tag)
            if len(selected) >= max_tags:
                return selected

    ranked = sorted(
        _TAG_RULES,
        key=lambda item: (scores.get(item[0], 0), -_rule_index(item[0])),
        reverse=True,
    )
    for tag, _ in ranked:
        if scores.get(tag, 0) > 0 and tag not in selected:
            selected.append(tag)
        if len(selected) >= max_tags:
            return selected

    for raw in extra_topics or []:
        tag = normalize_publish_tag(raw)
        if _is_usable_tag(tag) and tag not in selected:
            selected.append(tag)
        if len(selected) >= max_tags:
            return selected

    if not selected:
        for tag in FALLBACK_PUBLISH_TAGS:
            if tag not in selected:
                selected.append(tag)
            if len(selected) >= max_tags:
                break

    return selected


def append_publish_tags(
    content: str,
    title: str = "",
    hot_topic: str = "",
    extra_topics: Iterable[str] | None = None,
    max_tags: int = 3,
) -> str:
    """返回末尾带发布标签的正文，已有标签会去重并规整到最后一行。"""
    body = (content or "").rstrip()
    tags = select_publish_tags(
        title=title,
        content=body,
        hot_topic=hot_topic,
        extra_topics=extra_topics,
        max_tags=max_tags,
    )
    tag_line = format_publish_tags(tags)
    if not tag_line:
        return body

    body_without_tag_tail = re.sub(r"(?:\n\s*)?#(?:[^#\n]{1,30})#(?:\s+#(?:[^#\n]{1,30})#)*\s*$", "", body).rstrip()
    return f"{body_without_tag_tail}\n\n{tag_line}" if body_without_tag_tail else tag_line


def _rule_index(tag: str) -> int:
    for idx, (candidate, _) in enumerate(_TAG_RULES):
        if candidate == tag:
            return idx
    return len(_TAG_RULES)


def _is_usable_tag(tag: str) -> bool:
    return bool(tag) and len(tag) <= 12 and "\n" not in tag
