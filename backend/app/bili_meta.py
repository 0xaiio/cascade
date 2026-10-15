"""B站 投稿元数据：模板渲染、标签派生、平台硬限校验。

四个纯函数模块之一（`bili_bin` 管进程、`bili_api` 管 HTTP、`bili_auth` 管登录态），
本模块只管**字符串**：把「任务 / 视频」变成「该提交的标题、简介、标签、分区」。

两条必须守住的判据：

1. **超限要报错，不许静默截断。** 截断会让用户以为发出去的是完整内容 —— 这与
   [ai/ui/001](../../ui/001-cookie-buttons-not-on-the-same-baseline.md) 那类「意图不等于实现」是同一个盲区。
   生成侧报错，界面就能把「第 N 条标题超了 6 个字」指到具体位置。
2. **模板渲染不能用 `str.format`。** 视频标题里出现一个 `{` 就会抛 `KeyError`，
   而用户改不了自己的视频标题。这里用显式替换，未知占位符**原样保留并在校验里报出**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 平台硬限（依据见 01-requirements.md F13）
TITLE_MAX = 80
DESC_MAX = 250
TAG_MAX = 200
TAG_MAX_COUNT = 10
TAG_ITEM_MAX = 20
# 报错时**最多回显**多少个字符。它必须大于 `TAG_ITEM_MAX`，否则「完整显示」那个分支
# 永远不可达（超限的标签至少有 21 个字）。见 `validate`。
TAG_PREVIEW_MAX = 40

# ---------------------------------------------------------------- 分区词表
# **只收能站得住的数字。** 完整分区树有 20 个主分区 + 上百个二级分区，且会随平台调整
# （例如 tid 171 在旧第三方表里被标成「知识·野生技术协会」，现行表里它是游戏区的「电子竞技」，
# 真正的「野生技术协会」是 122）。这里刻意只收一份**常用小词表**，数字取自
# bilibili-API-collect 的《视频分区一览》（2025-04-15 版）。
DEFAULT_TID = "122"

PARTITIONS: tuple[tuple[str, str], ...] = (
    ("知识 · 野生技术协会", "122"),
    ("知识 · 科学科普", "201"),
    ("知识 · 校园学习", "208"),
    ("知识 · 职业职场", "209"),
    ("知识 · 人文历史", "228"),
    ("科技 · 计算机技术", "231"),
    ("科技 · 软件应用", "230"),
    ("科技 · 数码", "95"),
    ("科技 · 极客DIY", "233"),
    ("生活 · 日常", "21"),
    ("生活 · 搞笑", "138"),
    ("生活 · 手工", "161"),
    ("影视 · 影视杂谈", "182"),
    ("影视 · 影视剪辑", "183"),
    ("音乐 · 演奏", "59"),
    ("音乐 · 翻唱", "31"),
    ("游戏 · 单机游戏", "17"),
    ("游戏 · 电子竞技", "171"),
    ("动画 · 同人·手书", "47"),
    ("美食 · 美食制作", "76"),
    ("纪录片 · 科学·探索·自然", "178"),
)

PARTITION_LABELS: dict[str, str] = {tid: label for label, tid in PARTITIONS}

# ---------------------------------------------------------------- 版权（自制 / 转载）
# 取值直接沿用 `biliup --copyright 1-自制 2-转载`（也就是 B站 接口的 `copyright` 字段），
# 所以界面上的选择不需要任何翻译层，原样能拼进命令。
COPYRIGHT_SELF = "1"
COPYRIGHT_REPRINT = "2"
# **默认转载**：这个工作流搬的是从别的平台下载下来的视频，绝大多数稿件都不是自己拍的；
# 默认「自制」会把每一单都填成假信息，而 B站 对「把转载标成自制」是有处置的。
DEFAULT_COPYRIGHT = COPYRIGHT_REPRINT
COPYRIGHT_LABELS: dict[str, str] = {
    COPYRIGHT_SELF: "自制",
    COPYRIGHT_REPRINT: "转载",
}

# ---------------------------------------------------------------- 模板占位符
PLACEHOLDERS: tuple[str, ...] = ("index", "title", "count", "job", "source")
PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

DEFAULT_TITLE_TEMPLATE = "{title}"
DEFAULT_DESC_TEMPLATE = "{job} · 第 {index}/{count} 集\n原视频：{source}"

# 英文标题里这些词单独成标签没有信息量；只在派生标签时用来过滤，不影响标题本身。
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "you", "your", "are", "was", "were",
        "how", "what", "why", "when", "who", "not", "but", "can", "will", "our", "its", "it's",
        "part", "full", "new", "ep", "episode", "official", "video", "vs", "via",
        "to", "of", "in", "on", "at", "is", "be", "do", "my", "we", "us", "as", "by", "or", "an",
        "am", "are", "has", "had", "have", "been", "than", "then", "them", "they", "she", "he",
    }
)
_TAG_TOKEN_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


def partition_options() -> list[dict[str, str]]:
    """给界面的分区下拉。**单一来源在这里**，前端不自己复制一份数字。"""
    return [{"tid": tid, "label": label} for label, tid in PARTITIONS]


def partition_label(tid: str | None) -> str:
    if not tid:
        return PARTITION_LABELS.get(DEFAULT_TID, DEFAULT_TID)
    return PARTITION_LABELS.get(str(tid), f"分区 {tid}")


def normalize_tid(value: str | None) -> str:
    """只接受纯数字的 tid（含未知数字，允许高级用户绕过小词表）；其余一律回退默认分区。"""
    if value is None:
        return DEFAULT_TID
    text = str(value).strip()
    return text if text.isdigit() else DEFAULT_TID


def normalize_copyright(value: str | None) -> str:
    """把界面上的选择收敛成 `1` 或 `2`。其余一律回退**默认的转载**。

    回退到「转载」而不是「自制」是有意的：字段读不出来时，把稿件标成自制是**替用户
    声明了一件他没说过的事**，而标成转载最多只是让用户改一下。
    """
    text = str(value).strip() if value is not None else ""
    return text if text in COPYRIGHT_LABELS else DEFAULT_COPYRIGHT


def copyright_label(value: str | None) -> str:
    return COPYRIGHT_LABELS[normalize_copyright(value)]


def effective_source(copyright_value: str | None, source: str | None) -> str:
    """**真正会提交出去**的来源链接。

    单一判据在这里，三处调用（`render` / `default_single` / `bili_bin.build_upload_argv`）：
    只有转载稿才有来源 —— 自制稿即便带着值也不提交。不这么收口的话，
    「界面上有值」与「命令里带了这个参数」会各写一遍，迟早出现落库的快照与实发参数不一致
    （`B站` 那边「来源」是转载稿专有字段，自制稿带了反而可能被判成误标）。
    """
    if normalize_copyright(copyright_value) != COPYRIGHT_REPRINT:
        return ""
    return (source or "").strip()


def derive_tags(*texts: str, limit: int = TAG_MAX_COUNT) -> str:
    """从标题里派生一组标签。**不追求准，追求「不至于空白且能一键改」。**"""
    seen: list[str] = []
    for text in texts:
        for token in _TAG_TOKEN_RE.findall(text or ""):
            if len(token) < 2 or token.lower() in _STOPWORDS:
                continue
            token = token[:TAG_ITEM_MAX]
            if token not in seen:
                seen.append(token)
            if len(seen) >= limit:
                return ",".join(seen)
    return ",".join(seen)


def trim_tags(tags: str, *, limit_total: int = TAG_MAX) -> str:
    """按总长上限从尾部删除标签 —— 只删整项，绝不截断到半个标签。"""
    items = [item.strip() for item in (tags or "").split(",") if item.strip()]
    items = items[:TAG_MAX_COUNT]
    out: list[str] = []
    for item in items:
        candidate = ",".join([*out, item])
        if len(candidate) > limit_total and out:
            break
        out.append(item)
    return ",".join(out)


@dataclass(frozen=True)
class UploadMetadata:
    """一次投稿真正要提交的字段。

    `copyright` + `source` 是后来加的（原先 `bili_bin` 把 `--copyright` 写死成 `1`）：
    B站 的转载稿要求填来源，而本工作流搬的视频本来就都有原始链接，所以这一项要跟着元数据走，
    不能由命令组装层自己决定。两个字段都带默认值，既有构造点不需要跟着改。
    """

    title: str
    desc: str
    tags: str
    tid: str
    copyright: str = DEFAULT_COPYRIGHT
    source: str = ""


@dataclass(frozen=True)
class MetadataTemplate:
    """合集模板。

    **刻意只有一个 `copyright`、没有 `source`**：合集里的每条视频各自的原始链接都不同，
    写一个共用的来源是错的。逐条渲染时来源取该条自己的 `source_url`（`render_collection` 里传）。
    """

    title_template: str
    desc_template: str
    tags: str
    tid: str
    copyright: str = DEFAULT_COPYRIGHT


def render(
    template: MetadataTemplate,
    *,
    index: int,
    title: str,
    count: int,
    job: str,
    source: str = "",
) -> UploadMetadata:
    """渲染一份模板。占位符以外的一切字符原样保留（含用户标题里的花括号）。"""
    mapping = {
        "index": f"{index:02d}",
        "title": title or "",
        "count": str(count),
        "job": job or "",
        "source": source or "",
    }
    copyright_value = normalize_copyright(template.copyright)
    return UploadMetadata(
        title=PLACEHOLDER_RE.sub(lambda m: mapping[m.group(1)], template.title_template).strip(),
        desc=PLACEHOLDER_RE.sub(lambda m: mapping[m.group(1)], template.desc_template).strip(),
        tags=trim_tags(template.tags),
        tid=normalize_tid(template.tid),
        copyright=copyright_value,
        source=effective_source(copyright_value, source),
    )


def unknown_placeholders(*texts: str) -> list[str]:
    """找出用了但不被支持的占位符（如 `{date}`），供界面提示。"""
    found: list[str] = []
    for text in texts:
        for token in re.findall(r"\{([^{}]*)\}", text or ""):
            if token not in PLACEHOLDERS and token not in found:
                found.append(token)
    return found


# ---------------------------------------------------------------- 置顶评论
# B站 评论上限 1000 字（比简介的 250 宽松得多），但仍是硬限，超了会被拒。
COMMENT_MAX = 1000


def default_comment(*, source_url: str = "") -> str:
    """默认置顶评论。

    带上来源链接是有意的：B站 简介里可以放 URL，但评论是**读者注意力所在的那一处**，
    而「这条视频从哪来」正是观众最常问的问题。用户可以在表单里整段删掉（= 不发评论）。
    """
    lines = ["本视频由个人工作流同步上传，欢迎在评论区交流。"]
    if source_url:
        lines.append(f"原视频：{source_url}")
    return "\n".join(lines)


def validate_comment(text: str) -> list[str]:
    if len(text) > COMMENT_MAX:
        return [f"评论超出 {len(text) - COMMENT_MAX} 个字（上限 {COMMENT_MAX} 字）"]
    return []


def limits() -> "BiliLimits":
    """平台硬限的**唯一来源**，下发给界面用（界面不自己复制一份数字）。"""
    from .schemas import BiliLimits

    return BiliLimits(
        title=TITLE_MAX,
        desc=DESC_MAX,
        tags=TAG_MAX,
        tag_count=TAG_MAX_COUNT,
        tag_item=TAG_ITEM_MAX,
        comment=COMMENT_MAX,
    )


def validate(meta: UploadMetadata) -> list[str]:
    """返回人话问题清单。空列表 = 可以提交。**这里绝不修改 meta。**"""
    problems: list[str] = []
    if not meta.title.strip():
        problems.append("标题是空的")
    elif len(meta.title) > TITLE_MAX:
        problems.append(f"标题超出 {len(meta.title) - TITLE_MAX} 个字（上限 {TITLE_MAX} 字）")
    if len(meta.desc) > DESC_MAX:
        problems.append(f"简介超出 {len(meta.desc) - DESC_MAX} 个字（上限 {DESC_MAX} 字）")
    tag_items = [item.strip() for item in meta.tags.split(",") if item.strip()]
    if len(tag_items) > TAG_MAX_COUNT:
        problems.append(f"标签有 {len(tag_items)} 个，最多 {TAG_MAX_COUNT} 个")
    # 单项长度也要查：`derive_tags` 派生时只截断自己造的 token，用户手填的不受它约束。
    # 少这一条时，一个 150 字的标签会通过全部校验、直达平台才被拒（`limits()` 一直在
    # 对外公布 `tag_item = 20`，界面却没有对应的检查）。与标题一样：**报错，不截断。**
    longest_tag = max(tag_items, key=len, default="")
    if len(longest_tag) > TAG_ITEM_MAX:
        # 预览上限**必须大于单项上限**，否则「短到能完整显示」这个分支永远走不到
        # —— 一个超限的标签至少有 21 个字，而阈值若写成 12，那句 `if` 就是死代码。
        shown = longest_tag if len(longest_tag) <= TAG_PREVIEW_MAX else f"{longest_tag[:TAG_PREVIEW_MAX]}…"
        problems.append(f"「{shown}」这个标签有 {len(longest_tag)} 个字，单个标签最多 {TAG_ITEM_MAX} 个字")
    if len(meta.tags) > TAG_MAX:
        problems.append(f"标签总长超出 {len(meta.tags) - TAG_MAX} 个字（上限 {TAG_MAX} 字）")
    if not meta.tid.isdigit():
        problems.append("分区不是一个有效的编号")
    if meta.copyright not in COPYRIGHT_LABELS:
        problems.append("版权只能是「自制」或「转载」")
    elif meta.copyright == COPYRIGHT_REPRINT and not (meta.source or "").strip():
        # 这条不是「我们觉得应该填」，而是平台的硬要求：转载稿没有来源会被退回来
        # （`21004 Missing source for reprint`，见 01-requirements F16）。
        # 在表单里就地拦住，比让用户点了提交再吃一个 400（而且提示语里只有一串错误码）好。
        problems.append("选了「转载」但没填原视频链接（转载稿必须填来源，留空会被 B站 退回来）")
    return problems


def default_single(*, job_title: str, item_title: str, source_url: str) -> UploadMetadata:
    """单视频的预填值。标题取视频标题，简介带出来源链接（B站 简介里的 URL 是可点击的）。

    版权默认「转载」并**把这条视频自己的原始链接预填进来源** —— 这是本工作流里唯一
    已知为真的取值（视频就是从那个链接下来的）；用户改成自制时来源会自动清空
    （`effective_source`）。
    """
    desc = f"{job_title}\n原视频：{source_url}" if source_url else job_title
    return UploadMetadata(
        title=(item_title or job_title)[:TITLE_MAX],
        desc=desc[:DESC_MAX],
        tags=trim_tags(derive_tags(item_title, job_title)),
        tid=DEFAULT_TID,
        copyright=DEFAULT_COPYRIGHT,
        source=effective_source(DEFAULT_COPYRIGHT, source_url),
    )


def default_collection_template(job_title: str) -> MetadataTemplate:
    """合集的默认模板。标签取合集名派生，逐条渲染时会各自叠加视频标题的派生标签。

    只给 `copyright`，**不给来源**：合集里每条视频的原始链接都不一样，
    逐条渲染时各取自己的 `source_url`（见 `MetadataTemplate` 的说明）。
    """
    return MetadataTemplate(
        title_template=DEFAULT_TITLE_TEMPLATE,
        desc_template=DEFAULT_DESC_TEMPLATE,
        tags=trim_tags(derive_tags(job_title)),
        tid=DEFAULT_TID,
        copyright=DEFAULT_COPYRIGHT,
    )


def default_season_desc(job_title: str, count: int) -> str:
    """默认合集简介。

    **不能留空**：`bili_api.create_season` 会把 `desc` 原样发给平台，而合集简介是 B站 的必填项之一；
    留空等于把一个「能不能过」交给平台去猜。界面上没有这个字段，所以就由后端按同样的事实派生一句。
    """
    return f"{job_title}（共 {count} 个视频，由本机下载工作流自动上传）"


def merge_item_tags(template_tags: str, item_title: str) -> str:
    """模板标签 + 该条视频标题派生的标签，去重后按总长上限收敛。

    合集里每条视频的内容不同，只用模板标签会让所有条目挂上一模一样的标签；
    只用派生标签又会丢掉用户在模板里写的固定标签。两者都要。
    """
    combined: list[str] = []
    for item in [*template_tags.split(","), *derive_tags(item_title).split(",")]:
        item = item.strip()
        if item and item not in combined:
            combined.append(item)
    return trim_tags(",".join(combined))
