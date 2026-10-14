"""`bili_meta`：模板渲染、标签派生、平台硬限校验。

这一层是纯字符串函数，所以断言可以写得比别处更死 —— 它没有 IO，也没有时序。
两条最要紧的判据（模块 docstring 里写的那两条）在这里各有一组用例：

1. **不许静默截断**：超限必须原样报出「超了几个字」，而不是悄悄切短。
2. **渲染不用 `str.format`**：视频标题里带 `{` 不能让整个上传流程抛异常。
"""

import pytest

from app.bili_meta import (
    COMMENT_MAX,
    DEFAULT_TID,
    DESC_MAX,
    PARTITIONS,
    PARTITION_LABELS,
    TAG_MAX,
    TAG_MAX_COUNT,
    TAG_ITEM_MAX,
    TAG_PREVIEW_MAX,
    TITLE_MAX,
    MetadataTemplate,
    UploadMetadata,
    default_collection_template,
    default_comment,
    default_season_desc,
    default_single,
    derive_tags,
    limits,
    merge_item_tags,
    normalize_tid,
    partition_label,
    partition_options,
    render,
    trim_tags,
    unknown_placeholders,
    validate,
    validate_comment,
)


# ---------------------------------------------------------------- 分区词表
def test_default_partition_is_the_wild_tech_association_not_esports():
    """默认分区是 122（知识·野生技术协会）。

    这条用例钉的是一个**曾经错过的事实**：旧第三方分区表把 171 标成「知识·野生技术协会」，
    而现行表里 171 是游戏区的「电子竞技」。默认分区错了不会报错，只会让每条稿件都投错区。
    """
    assert DEFAULT_TID == "122"
    assert PARTITION_LABELS["122"] == "知识 · 野生技术协会"
    assert PARTITION_LABELS["171"] == "游戏 · 电子竞技"


def test_partition_options_matches_the_single_source_table():
    """下拉项与词表同源，且形状就是界面要的 `{tid, label}`（前端不自己复制一份数字）。"""
    options = partition_options()
    assert len(options) == len(PARTITIONS)
    assert all(set(item) == {"tid", "label"} for item in options)
    assert {"tid": "122", "label": "知识 · 野生技术协会"} in options


@pytest.mark.parametrize(
    ("tid", "expected"),
    [
        ("122", "知识 · 野生技术协会"),
        ("9999", "分区 9999"),  # 词表外但合法：高级用户绕过小词表
        (None, "知识 · 野生技术协会"),
        ("", "知识 · 野生技术协会"),
    ],
)
def test_partition_label(tid, expected):
    assert partition_label(tid) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("122", "122"), (" 231 ", "231"), (None, DEFAULT_TID), ("", DEFAULT_TID), ("abc", DEFAULT_TID), ("12a", DEFAULT_TID)],
)
def test_normalize_tid_only_accepts_digits(raw, expected):
    assert normalize_tid(raw) == expected


# ---------------------------------------------------------------- 模板渲染
def test_render_pads_index_and_substitutes_placeholders():
    meta = render(
        MetadataTemplate("{job} 第 {index}/{count} 集", "{title}\n来源：{source}", "标签", "122"),
        index=3,
        title="第三集",
        count=12,
        job="合集名",
        source="https://youtu.be/x",
    )
    assert meta.title == "合集名 第 03/12 集"
    assert meta.desc == "第三集\n来源：https://youtu.be/x"
    assert meta.tid == "122"


def test_render_leaves_user_braces_untouched():
    """视频标题里带 `{` 不能让渲染抛异常 —— 用户改不了自己的视频标题。"""
    meta = render(
        MetadataTemplate("{title}", "{title}", "", "122"),
        index=1,
        title="C++ 模板 {T} 的实现",
        count=1,
        job="任务",
    )
    assert meta.title == "C++ 模板 {T} 的实现"


def test_render_keeps_unknown_placeholders_verbatim_and_reports_them():
    """未知占位符**原样留在结果里**并由校验报出，而不是悄悄变空。"""
    template = MetadataTemplate("{title} · {date}", "{title}", "", "122")
    meta = render(template, index=1, title="标题", count=1, job="任务")
    assert meta.title == "标题 · {date}"
    assert unknown_placeholders(template.title_template, template.desc_template) == ["date"]


def test_unknown_placeholders_reports_each_name_once_in_order():
    assert unknown_placeholders("{b} {a} {b}") == ["b", "a"]


def test_render_trims_tags_and_normalizes_tid():
    meta = render(MetadataTemplate("{title}", "{title}", " a , b ", "not-a-number"), index=1, title="t", count=1, job="j")
    assert meta.tags == "a,b"
    assert meta.tid == DEFAULT_TID


# ---------------------------------------------------------------- 硬限校验
def test_limits_matches_the_constants():
    """界面上的字数上限来自这一份；两处不一致时用户会「看着没超却被拒」。"""
    value = limits()
    assert (value.title, value.desc, value.tags, value.tag_count, value.comment) == (
        TITLE_MAX,
        DESC_MAX,
        TAG_MAX,
        TAG_MAX_COUNT,
        COMMENT_MAX,
    )


def test_validate_accepts_a_normal_metadata():
    assert validate(UploadMetadata(title="标题", desc="简介", tags="a,b", tid="122")) == []


def test_validate_reports_the_excess_length_instead_of_truncating():
    """**不许静默截断**：超了要报出「超几个字」，让界面能指到具体位置。"""
    problems = validate(UploadMetadata(title="标" * (TITLE_MAX + 6), desc="", tags="", tid="122"))
    assert problems == [f"标题超出 6 个字（上限 {TITLE_MAX} 字）"]


def test_validate_rejects_an_empty_title_before_the_length_rule():
    assert validate(UploadMetadata(title="   ", desc="", tags="", tid="122")) == ["标题是空的"]


@pytest.mark.parametrize(
    ("meta", "expected_fragment"),
    [
        (UploadMetadata(title="t", desc="简" * (DESC_MAX + 3), tags="", tid="122"), "简介超出 3 个字"),
        (UploadMetadata(title="t", desc="", tags=",".join(f"t{i}" for i in range(TAG_MAX_COUNT + 2)), tid="122"), f"最多 {TAG_MAX_COUNT} 个"),
        # 十个 20 字的标签 = 200 字 + 9 个逗号 = 209，超的是**总长**，单项都没超。
        (UploadMetadata(title="t", desc="", tags=",".join("t" * TAG_ITEM_MAX for _ in range(TAG_MAX_COUNT)), tid="122"), "标签总长超出"),
        (UploadMetadata(title="t", desc="", tags="", tid="分区"), "分区不是一个有效的编号"),
    ],
)
def test_validate_reports_each_hard_limit(meta, expected_fragment):
    problems = validate(meta)
    assert any(expected_fragment in problem for problem in problems), problems


def test_validate_reports_a_single_over_long_tag():
    """**单项**长度也要查。

    这条曾经漏掉：`derive_tags` 内部有 `token[:TAG_ITEM_MAX]`，所以**派生出来的**默认标签
    永远不会超；但用户手填的不受它约束，而 `validate` 当时只看总长与个数 ——
    一个 150 字的标签能通过全部校验、到平台才被拒。`limits()` 却一直在对外公布 `tag_item`。
    """
    over = "标" * (TAG_ITEM_MAX + 5)
    problems = validate(UploadMetadata(title="t", desc="", tags=f"正常,{over}", tid="122"))
    assert any(f"「{over}」这个标签有 {TAG_ITEM_MAX + 5} 个字" in problem for problem in problems), problems


def test_validate_shows_a_moderately_long_tag_in_full():
    """超限但不算太长的标签**完整回显**，用户一眼能认出是哪一个。

    这条同时钉住「回显上限必须大于单项上限」：写成 12 的话这个分支永远走不到，
    而超限的标签至少有 21 个字 —— 那就是一段死代码（与 ai/ui/003 同一类盲区）。
    """
    over = "标" * (TAG_ITEM_MAX + 1)
    problems = validate(UploadMetadata(title="t", desc="", tags=over, tid="122"))
    assert any(over in problem for problem in problems), problems


def test_validate_truncates_the_preview_of_an_absurdly_long_tag():
    """真有人贴一整段话进标签时，报错信息里不能把那一整段回显出来。"""
    over = "标" * 150
    problems = validate(UploadMetadata(title="t", desc="", tags=over, tid="122"))
    (problem,) = [item for item in problems if "这个标签有 150 个字" in item]
    assert over not in problem
    assert f"{'标' * TAG_PREVIEW_MAX}…" in problem


def test_validate_accepts_a_tag_exactly_at_the_item_limit():
    assert validate(UploadMetadata(title="t", desc="", tags="标" * TAG_ITEM_MAX, tid="122")) == []


def test_validate_never_mutates_the_metadata():
    """校验只读。改坏原值会让「用户改一个字再提交」这条路径静默丢内容。"""
    meta = UploadMetadata(title="标" * (TITLE_MAX + 6), desc="d", tags="a", tid="122")
    validate(meta)
    assert len(meta.title) == TITLE_MAX + 6


def test_validate_comment_uses_the_comment_limit():
    assert validate_comment("x" * COMMENT_MAX) == []
    assert validate_comment("x" * (COMMENT_MAX + 7)) == [f"评论超出 7 个字（上限 {COMMENT_MAX} 字）"]


# ---------------------------------------------------------------- 标签
def test_derive_tags_filters_stopwords_short_tokens_and_duplicates():
    tags = derive_tags("The database is DB and DB systems", "DB")
    assert "the" not in tags.split(",")
    assert "is" not in tags.split(",")
    assert len(tags.split(",")) == len(set(tags.split(",")))


def test_derive_tags_keeps_contiguous_cjk_as_one_token():
    """CJK 是连续匹配的一段，不会被切成单字 —— 切成单字会派生出一堆无意义的标签。"""
    assert derive_tags("数据库系统概论") == "数据库系统概论"


def test_derive_tags_stops_at_the_count_limit():
    assert len(derive_tags(*[f"token{i}" for i in range(30)]).split(",")) == TAG_MAX_COUNT


def test_derive_tags_truncates_a_single_long_token_to_the_item_limit():
    """单个标签有 20 字上限；超长的 token 是**截断**（它不是用户写下的标签，是派生物）。"""
    assert len(derive_tags("a" * 40).split(",")[0]) <= 20


def test_trim_tags_drops_whole_items_and_never_splits_one():
    """总长放不下时**删掉整个标签**，不是把某一个切成半截。"""
    kept = trim_tags("a" * 190 + "," + "b" * 30)
    assert kept == "a" * 190
    assert "," not in kept


def test_trim_tags_caps_the_item_count():
    many = ",".join(f"t{i}" for i in range(TAG_MAX_COUNT + 5))
    assert len(trim_tags(many).split(",")) <= TAG_MAX_COUNT


def test_merge_item_tags_keeps_template_tags_and_dedupes():
    merged = merge_item_tags("编程,数据库", "编程 Part one")
    items = merged.split(",")
    assert items[0] == "编程"
    assert "数据库" in items
    assert len(items) == len(set(items))


# ---------------------------------------------------------------- 默认值
def test_default_single_uses_the_item_title_and_carries_the_source_link():
    meta = default_single(job_title="任务名", item_title="视频名", source_url="https://youtu.be/x")
    assert meta.title == "视频名"
    assert "原视频：https://youtu.be/x" in meta.desc
    assert meta.tid == DEFAULT_TID


def test_default_single_truncates_a_title_that_is_already_too_long():
    """生成侧可以在边界上收敛（这是**我们**造的默认值），用户手填则报错不截断。"""
    meta = default_single(job_title="j", item_title="标" * (TITLE_MAX + 20), source_url="")
    assert len(meta.title) == TITLE_MAX
    assert validate(meta) == []


def test_default_single_falls_back_to_the_job_title():
    assert default_single(job_title="任务名", item_title="", source_url="").title == "任务名"


def test_default_collection_template_shape():
    template = default_collection_template("演示合集")
    assert template.title_template == "{title}"
    assert "{job}" in template.desc_template and "{index}" in template.desc_template and "{count}" in template.desc_template
    assert template.tid == DEFAULT_TID


def test_default_season_desc_is_never_empty():
    """平台那边的合集简介不能发空串，所以后端派生一句非空的话。"""
    text = default_season_desc("演示合集", 3)
    assert text.strip()
    assert "3" in text


def test_default_comment_includes_the_source_only_when_given():
    assert "原视频" not in default_comment()
    assert "原视频：https://youtu.be/x" in default_comment(source_url="https://youtu.be/x")
