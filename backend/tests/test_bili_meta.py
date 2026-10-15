"""`bili_meta`：模板渲染、标签派生、平台硬限校验。

这一层是纯字符串函数，所以断言可以写得比别处更死 —— 它没有 IO，也没有时序。
两条最要紧的判据（模块 docstring 里写的那两条）在这里各有一组用例：

1. **不许静默截断**：超限必须原样报出「超了几个字」，而不是悄悄切短。
2. **渲染不用 `str.format`**：视频标题里带 `{` 不能让整个上传流程抛异常。
"""

import pytest

from app.bili_meta import (
    COMMENT_MAX,
    COPYRIGHT_REPRINT,
    COPYRIGHT_SELF,
    DEFAULT_COPYRIGHT,
    DEFAULT_GROUP_MODE,
    DEFAULT_TID,
    DESC_MAX,
    GROUP_MODE_EXISTING,
    GROUP_MODE_NEW,
    GROUP_MODE_NONE,
    PARTITIONS,
    PARTITION_LABELS,
    TAG_MAX,
    TAG_MAX_COUNT,
    TAG_ITEM_MAX,
    TAG_PREVIEW_MAX,
    TITLE_MAX,
    MetadataTemplate,
    UploadMetadata,
    copyright_label,
    default_collection_template,
    default_comment,
    default_season_desc,
    default_single,
    derive_tags,
    effective_source,
    limits,
    merge_item_tags,
    normalize_copyright,
    normalize_group_mode,
    normalize_season_option,
    normalize_tid,
    partition_label,
    partition_options,
    render,
    trim_tags,
    unknown_placeholders,
    validate,
    validate_comment,
    validate_group_choice,
)

# 一份**能通过校验**的元数据。
#
# 为什么需要它：默认版权是「转载」，而转载稿必须有来源（`validate` 里那条平台硬要求）。
# 下面多数用例只想测长度/个数/标签，不该被版权规则带偏 —— 但如果每个构造点都手写一遍
# `source=`，规则一变就会满屏红，而且没人分得清哪些红是真信号。
# 要测版权本身，请显式传 `copyright=` / `source=`（文件末尾那一组）。
def meta(**overrides) -> UploadMetadata:
    base = {
        "title": "标题",
        "desc": "简介",
        "tags": "a,b",
        "tid": "122",
        "source": "https://youtu.be/x",
    }
    base.update(overrides)
    return UploadMetadata(**base)


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
    assert validate(meta()) == []


def test_validate_reports_the_excess_length_instead_of_truncating():
    """**不许静默截断**：超了要报出「超几个字」，让界面能指到具体位置。"""
    problems = validate(meta(title="标" * (TITLE_MAX + 6), desc="", tags=""))
    assert problems == [f"标题超出 6 个字（上限 {TITLE_MAX} 字）"]


def test_validate_rejects_an_empty_title_before_the_length_rule():
    assert validate(meta(title="   ", desc="", tags="")) == ["标题是空的"]


@pytest.mark.parametrize(
    ("meta_value", "expected_fragment"),
    [
        (meta(desc="简" * (DESC_MAX + 3), tags=""), "简介超出 3 个字"),
        (meta(desc="", tags=",".join(f"t{i}" for i in range(TAG_MAX_COUNT + 2))), f"最多 {TAG_MAX_COUNT} 个"),
        # 十个 20 字的标签 = 200 字 + 9 个逗号 = 209，超的是**总长**，单项都没超。
        (meta(desc="", tags=",".join("t" * TAG_ITEM_MAX for _ in range(TAG_MAX_COUNT))), "标签总长超出"),
        (meta(desc="", tags="", tid="分区"), "分区不是一个有效的编号"),
    ],
)
def test_validate_reports_each_hard_limit(meta_value, expected_fragment):
    problems = validate(meta_value)
    assert any(expected_fragment in problem for problem in problems), problems


def test_validate_reports_a_single_over_long_tag():
    """**单项**长度也要查。

    这条曾经漏掉：`derive_tags` 内部有 `token[:TAG_ITEM_MAX]`，所以**派生出来的**默认标签
    永远不会超；但用户手填的不受它约束，而 `validate` 当时只看总长与个数 ——
    一个 150 字的标签能通过全部校验、到平台才被拒。`limits()` 却一直在对外公布 `tag_item`。
    """
    over = "标" * (TAG_ITEM_MAX + 5)
    problems = validate(meta(desc="", tags=f"正常,{over}"))
    assert any(f"「{over}」这个标签有 {TAG_ITEM_MAX + 5} 个字" in problem for problem in problems), problems


def test_validate_shows_a_moderately_long_tag_in_full():
    """超限但不算太长的标签**完整回显**，用户一眼能认出是哪一个。

    这条同时钉住「回显上限必须大于单项上限」：写成 12 的话这个分支永远走不到，
    而超限的标签至少有 21 个字 —— 那就是一段死代码（与 ai/ui/003 同一类盲区）。
    """
    over = "标" * (TAG_ITEM_MAX + 1)
    problems = validate(meta(desc="", tags=over))
    assert any(over in problem for problem in problems), problems


def test_validate_truncates_the_preview_of_an_absurdly_long_tag():
    """真有人贴一整段话进标签时，报错信息里不能把那一整段回显出来。"""
    over = "标" * 150
    problems = validate(meta(desc="", tags=over))
    (problem,) = [item for item in problems if "这个标签有 150 个字" in item]
    assert over not in problem
    assert f"{'标' * TAG_PREVIEW_MAX}…" in problem


def test_validate_accepts_a_tag_exactly_at_the_item_limit():
    assert validate(meta(desc="", tags="标" * TAG_ITEM_MAX)) == []


def test_validate_never_mutates_the_metadata():
    """校验只读。改坏原值会让「用户改一个字再提交」这条路径静默丢内容。"""
    value = meta(title="标" * (TITLE_MAX + 6), desc="d", tags="a")
    validate(value)
    assert len(value.title) == TITLE_MAX + 6


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
    value = default_single(job_title="任务名", item_title="视频名", source_url="https://youtu.be/x")
    assert value.title == "视频名"
    assert "原视频：https://youtu.be/x" in value.desc
    assert value.tid == DEFAULT_TID


def test_default_single_defaults_to_reprint_and_prefills_this_video_s_link():
    """**默认转载，并把这条视频自己的原始链接预填进来源。**

    默认自制会把每一单都填成假信息（这个工作流搬的视频都不是自己拍的），
    而把链接留给用户去翻 —— 那是一份现成的、一定为真的数据。
    """
    value = default_single(job_title="任务名", item_title="视频名", source_url="https://youtu.be/x")
    assert DEFAULT_COPYRIGHT == COPYRIGHT_REPRINT
    assert value.copyright == COPYRIGHT_REPRINT
    assert value.source == "https://youtu.be/x"
    assert validate(value) == []


def test_default_single_truncates_a_title_that_is_already_too_long():
    """生成侧可以在边界上收敛（这是**我们**造的默认值），用户手填则报错不截断。"""
    value = default_single(
        job_title="j", item_title="标" * (TITLE_MAX + 20), source_url="https://youtu.be/x"
    )
    assert len(value.title) == TITLE_MAX
    assert validate(value) == []


def test_default_single_falls_back_to_the_job_title():
    assert default_single(job_title="任务名", item_title="", source_url="").title == "任务名"


def test_default_collection_template_shape():
    template = default_collection_template("演示合集")
    assert template.title_template == "{title}"
    assert "{job}" in template.desc_template and "{index}" in template.desc_template and "{count}" in template.desc_template
    assert template.tid == DEFAULT_TID
    assert template.copyright == COPYRIGHT_REPRINT


def test_default_season_desc_is_never_empty():
    """平台那边的合集简介不能发空串，所以后端派生一句非空的话。"""
    text = default_season_desc("演示合集", 3)
    assert text.strip()
    assert "3" in text


def test_default_comment_includes_the_source_only_when_given():
    assert "原视频" not in default_comment()
    assert "原视频：https://youtu.be/x" in default_comment(source_url="https://youtu.be/x")


# ---------------------------------------------------------------- 版权（自制 / 转载）
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", COPYRIGHT_SELF),
        ("2", COPYRIGHT_REPRINT),
        (" 1 ", COPYRIGHT_SELF),
        # 读不出来的值一律回退**转载**，不是自制：把别人的稿子标成自制是替用户
        # 声明了一件他没说过的事，而标成转载最多只是让他改一下。
        ("3", COPYRIGHT_REPRINT),
        ("", COPYRIGHT_REPRINT),
        (None, COPYRIGHT_REPRINT),
    ],
)
def test_normalize_copyright_only_accepts_the_two_platform_values(raw, expected):
    assert normalize_copyright(raw) == expected


def test_copyright_label_matches_the_platform_wording():
    assert copyright_label("1") == "自制"
    assert copyright_label("2") == "转载"
    assert copyright_label("看不懂") == "转载"


def test_effective_source_only_applies_to_reprints():
    """**单一判据**：自制稿即便带着来源也不提交出去。"""
    assert effective_source(COPYRIGHT_REPRINT, "https://youtu.be/x") == "https://youtu.be/x"
    assert effective_source(COPYRIGHT_REPRINT, "  https://youtu.be/x  ") == "https://youtu.be/x"
    assert effective_source(COPYRIGHT_SELF, "https://youtu.be/x") == ""
    assert effective_source(COPYRIGHT_REPRINT, "") == ""
    assert effective_source(COPYRIGHT_REPRINT, None) == ""


def test_render_carries_the_template_copyright_and_the_items_own_source():
    """合集模板只有一个 `copyright`，来源逐条取该条视频自己的链接。"""
    template = MetadataTemplate("{title}", "{title}\n来源：{source}", "标签", "122", COPYRIGHT_REPRINT)
    rendered = render(template, index=1, title="视频甲", count=2, job="合集", source="https://youtu.be/甲")
    assert rendered.copyright == COPYRIGHT_REPRINT
    assert rendered.source == "https://youtu.be/甲"
    assert "来源：https://youtu.be/甲" in rendered.desc


def test_render_drops_the_source_when_the_template_is_self_made():
    """切成自制之后，来源不能再跟着走 —— 否则实发参数与界面上的选择会互相矛盾。"""
    template = MetadataTemplate("{title}", "{title}\n来源：{source}", "标签", "122", COPYRIGHT_SELF)
    rendered = render(template, index=1, title="视频甲", count=1, job="合集", source="https://youtu.be/甲")
    assert rendered.copyright == COPYRIGHT_SELF
    assert rendered.source == ""


def test_render_reports_an_unusable_copyright_as_reprint_instead_of_failing():
    template = MetadataTemplate("{title}", "{title}", "标签", "122", "自制还是转载？")
    rendered = render(template, index=1, title="t", count=1, job="j", source="https://youtu.be/x")
    assert rendered.copyright == COPYRIGHT_REPRINT


def test_validate_rejects_a_reprint_without_a_source():
    """平台的硬要求（`21004 Missing source for reprint`），在表单里就地拦。"""
    problems = validate(meta(copyright=COPYRIGHT_REPRINT, source=""))
    assert any("选了「转载」但没填原视频链接" in problem for problem in problems), problems


def test_validate_accepts_a_self_made_video_without_a_source():
    assert validate(meta(copyright=COPYRIGHT_SELF, source="")) == []


def test_validate_rejects_a_copyright_outside_the_two_values():
    problems = validate(meta(copyright="公开"))
    assert any("版权只能是" in problem for problem in problems), problems
    # 版权非法时不再叠一条「没填来源」：那是同一个问题的两种说法，两条并列只会让人迷惑。
    assert not any("没填原视频链接" in problem for problem in problems), problems


# ================================================================ 归集方式
def test_group_mode_defaults_to_new_so_existing_uploads_keep_grouping():
    """改动前的行为就是「每次投完自动建一个合集」。

    默认值换成别的，会让用户**已经投出去**的稿件在下次重投时突然失去归集 ——
    这不是「新功能更好」，是把既有行为偷偷改了。
    """
    assert DEFAULT_GROUP_MODE == GROUP_MODE_NEW
    assert normalize_group_mode(None) == GROUP_MODE_NEW
    assert normalize_group_mode("") == GROUP_MODE_NEW


def test_group_mode_falls_back_rather_than_guessing():
    """与 `normalize_copyright` 同一条原则：回退到不改变既有事实的那一侧。

    猜成 `existing` 而没有编号会让归集无从下手；猜成 `none` 会让用户以为归过了。
    """
    assert normalize_group_mode("乱写的") == GROUP_MODE_NEW


def test_normalize_group_mode_keeps_the_three_real_values():
    for value in (GROUP_MODE_NONE, GROUP_MODE_EXISTING, GROUP_MODE_NEW):
        assert normalize_group_mode(value) == value


def test_join_an_existing_collection_requires_an_id():
    problems = validate_group_choice(group_mode=GROUP_MODE_EXISTING, season_id=None, season_title="", count=2)
    assert any("没有选具体是哪个合集" in problem for problem in problems), problems


def test_group_choice_rejects_the_three_contradictions():
    """三条矛盾各自单独报，不静默取其一。

    静默处理（比如「带了编号就当 existing」）的后果是用户选了「新建」却往老合集里塞视频，
    而界面上完全看不出来。
    """
    new_with_id = validate_group_choice(
        group_mode=GROUP_MODE_NEW, season_id=42, season_title="名字", count=2
    )
    assert any("不该带已有合集的编号" in problem for problem in new_with_id), new_with_id

    none_with_id = validate_group_choice(
        group_mode=GROUP_MODE_NONE, season_id=42, season_title="", count=2
    )
    assert any("不该指定合集" in problem for problem in none_with_id), none_with_id

    bogus = validate_group_choice(group_mode="随便", season_id=None, season_title="", count=2)
    assert len(bogus) == 1, bogus


def test_group_choice_accepts_an_empty_new_title():
    """留空**不算错**：路由层用任务名兜底（`season_title or job.title`）。

    这里若报「要给它起个名字」，就会与后端的兜底行为对不上 ——
    用户填了个名却被拒，而留空反而能过。
    """
    assert validate_group_choice(
        group_mode=GROUP_MODE_NEW, season_id=None, season_title="", count=2
    ) == []


# ================================================================ 合集归一
def test_season_option_reads_the_primary_key_names():
    assert normalize_season_option({"id": 7, "title": "C++ Weekly"}) == {
        "season_id": 7,
        "title": "C++ Weekly",
        "desc": "",
        "cover_url": "",
        "total": 0,
    }


def test_season_option_tolerates_the_alternative_key_names():
    """简介与封面的键名在不同接口版本里换过名，只认一个会把半缺的合集整条丢掉。"""
    option = normalize_season_option({"season_id": 9, "name": "数据库", "intro": "讲义", "cover_url": "http://c.jpg", "count": 12})
    assert option == {
        "season_id": 9,
        "title": "数据库",
        "desc": "讲义",
        "cover_url": "http://c.jpg",
        "total": 12,
    }


def test_season_option_drops_only_the_rows_that_cannot_be_selected():
    """没有编号的合集**选不了**（归集时要按编号提交），必须整条跳过。

    与之相对，缺标题的合集仍要留下 —— 用编号兜一个可读名字即可，
    丢掉它等于让用户少一个可选目标。
    """
    assert normalize_season_option(None) is None
    assert normalize_season_option("junk") is None
    assert normalize_season_option({"title": "没有编号"}) is None
    assert normalize_season_option({"id": "不是数字", "title": "坏编号"}) is None

    unnamed = normalize_season_option({"id": 123})
    assert unnamed is not None and unnamed["title"] == "合集 #123"


def test_season_option_survives_a_non_numeric_total():
    """条数只是显示用的。一个 `total: "很多"` 不该让整条合集从下拉里消失。"""
    option = normalize_season_option({"id": 5, "title": "合集", "total": "很多"})
    assert option is not None and option["total"] == 0
