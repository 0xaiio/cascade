"""`bili_api`：会员态 HTTP 客户端（对账、合集、置顶评论）。

这一层全部经注入的 `Transport`，所以可以完全离线跑。用例集中钉四类**写错了也不会在本地报错**的东西：

1. **参数形态**。`episodes/add` 是 camelCase 正文 + query 上的 csrf，`season/add` 恰好相反
   （csrf 在表单正文里）。写错的那一半只会在真账号上 404 或「csrf 校验失败」。
2. **「查不到」不等于「没发生」**。对账必须区分「拿到了平台数据」与「没拿到」，
   并且把后者如实报成「未核实」，而不是猜一个。
3. **幂等**。置顶评论处在重试路径上：已经发过就必须只补置顶，不能再发一条。
4. **失败不越级**。评论/置顶失败只能污染评论这一项 —— 所以「发失败」与「置顶失败」的
   `CommentResult` 形状不同：后者要带上 rpid，用户才能自己去评论区置顶。
"""

from __future__ import annotations

import pytest

from app.bilibili.bili_api import (
    COMMENT_DUPLICATE_CODE,
    MEMBER_ORIGIN,
    TITLE_MATCH_PREFIX,
    BiliApiError,
    add_episodes,
    create_season,
    fetch_account,
    fetch_video,
    find_archive,
    find_own_comment,
    find_season_section,
    list_recent_archives,
    list_seasons,
    send_and_top_comment,
)
from app.bilibili.bili_http import MEMBER_REFERER, WEB_REFERER, HttpError
from bili_fakes import JAR, FakeBiliTransport, RecordingSleeper, StubTransport, own_reply, section_id_for

MARKER = "个人工作流同步上传"
MESSAGE = f"本视频由{MARKER}，欢迎在评论区交流。"
TITLE = "演示标题"


def jar_without_csrf() -> dict[str, str]:
    return {"SESSDATA": "sess", "DedeUserID": "42"}


def calls_to(transport, suffix: str) -> list[dict]:
    return [call for call in transport.calls if call["url"].endswith(suffix)]


# ---------------------------------------------------------------- 请求头
def test_member_requests_carry_cookie_referer_and_origin():
    """会员态接口要求 Origin/Referer 是创作中心。少了它们会被当成跨站请求拒掉。"""
    transport = FakeBiliTransport()
    list_recent_archives(transport, JAR)
    headers = transport.calls[0]["headers"]
    assert headers["Referer"] == MEMBER_REFERER
    assert headers["Origin"] == MEMBER_ORIGIN
    assert headers["Cookie"] == "SESSDATA=sess-value; bili_jct=jct-value; DedeUserID=42"


def test_public_api_requests_use_the_web_referer():
    transport = FakeBiliTransport()
    fetch_account(transport, JAR)
    assert transport.calls[0]["headers"]["Referer"] == WEB_REFERER
    assert "Origin" not in transport.calls[0]["headers"]


def test_member_json_requests_declare_content_type():
    transport = FakeBiliTransport()
    add_episodes(transport, JAR, section_id=1, episodes=[{"aid": 1, "cid": 2, "title": "t"}])
    assert transport.calls[0]["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------- 账号
def test_fetch_account_returns_the_logged_in_identity():
    """「登录的是谁」只从这里读，不从 cookie 里反推。"""
    transport = FakeBiliTransport(account={"mid": 7, "uname": "某人"})
    assert fetch_account(transport, JAR) == {"mid": 7, "uname": "某人"}


def test_fetch_account_returns_none_when_not_logged_in():
    assert fetch_account(FakeBiliTransport(account=None), JAR) is None


def test_fetch_account_returns_none_on_a_business_error():
    assert fetch_account(StubTransport({"code": -101, "message": "账号未登录"}), JAR) is None


def test_fetch_account_returns_none_when_the_body_is_not_json():
    """登录态检查会被体检与状态接口反复调用，一个 HTML 错误页不能把它顶成异常。"""
    assert fetch_account(StubTransport(text="<html>502 Bad Gateway</html>"), JAR) is None


def test_fetch_account_tolerates_a_missing_username():
    assert fetch_account(FakeBiliTransport(account={"mid": 7}), JAR) == {"mid": 7, "uname": ""}


# ---------------------------------------------------------------- 稿件列表
def test_list_recent_archives_unwraps_the_archive_payload():
    """`arc_audits[].Archive` 才是稿件本体，外面那层是个包装。"""
    transport = FakeBiliTransport(archives=[{"aid": 1, "title": "甲"}, {"aid": 2, "title": "乙"}])
    assert list_recent_archives(transport, JAR) == [{"aid": 1, "title": "甲"}, {"aid": 2, "title": "乙"}]


def test_list_recent_archives_passes_the_page_size():
    transport = FakeBiliTransport()
    list_recent_archives(transport, JAR, page_size=5)
    assert transport.calls[0]["params"] == {"pn": 1, "ps": 5}


def test_list_recent_archives_skips_malformed_rows():
    """混进 `null`/字符串时不能整体崩掉 —— 否则一条脏数据会让全部对账失败。"""
    transport = StubTransport({"code": 0, "data": {"arc_audits": [None, "junk", {"Archive": {"title": "好的一条"}}]}})
    assert list_recent_archives(transport, JAR) == [{"title": "好的一条"}]


def test_list_recent_archives_raises_a_readable_error_on_a_business_code():
    transport = StubTransport({"code": -101, "message": "账号未登录"})
    with pytest.raises(BiliApiError) as excinfo:
        list_recent_archives(transport, JAR)
    assert excinfo.value.code == -101
    assert excinfo.value.message == "账号未登录"
    assert excinfo.value.where == "读取稿件列表"
    # 原始 code 与 message 都要进异常文本，排障时不用回去复现。
    assert "-101" in str(excinfo.value) and "账号未登录" in str(excinfo.value)


def test_list_recent_archives_raises_when_the_body_is_not_json():
    """不是 JSON 属网络层失败（`HttpError`），与「业务码非零」是两回事，不能混成一个异常。"""
    with pytest.raises(HttpError):
        list_recent_archives(StubTransport(text="<html>nope</html>"), JAR)


# ---------------------------------------------------------------- 对账
def test_find_archive_matches_by_title_prefix():
    transport = FakeBiliTransport(archives=[{"aid": 1, "title": "别的稿件"}, {"aid": 2, "title": f"{TITLE} 第 03 集"}])
    found = find_archive(transport, JAR, TITLE, attempts=1)
    assert found is not None and found["aid"] == 2


def test_find_archive_returns_none_when_the_title_is_absent():
    """**本功能最不能妥协的一条**：查不到就说查不到，不用「命令没报错」冒充找到。"""
    transport = FakeBiliTransport(archives=[{"aid": 1, "title": "风马牛不相及"}])
    assert find_archive(transport, JAR, TITLE, attempts=1) is None


def test_find_archive_retries_and_succeeds_once_the_platform_catches_up():
    """稿件列表有平台侧延迟，第一次读不到是常态，不是失败。"""
    transport = FakeBiliTransport(
        archives=[{"aid": 9, "title": TITLE}],
        archives_error=HttpError("连接被重置"),
        archives_ok_after=2,
    )
    sleeper = RecordingSleeper()
    found = find_archive(transport, JAR, TITLE, attempts=4, delay=1.0, sleeper=sleeper)
    assert found is not None and found["aid"] == 9
    assert transport.archive_reads == 3
    assert sleeper.waits == [1.0, 1.0]


def test_find_archive_does_not_sleep_after_the_final_attempt():
    """最后一次之后不再等 —— 那只是一段白等的延迟。"""
    sleeper = RecordingSleeper()
    transport = FakeBiliTransport(archives_error=HttpError("连接被重置"))
    assert find_archive(transport, JAR, TITLE, attempts=3, delay=5.0, sleeper=sleeper) is None
    assert transport.archive_reads == 3
    assert sleeper.waits == [5.0, 5.0]


def test_find_archive_does_not_sleep_when_delay_is_zero():
    """`delay=0` 表示「一次性查完就完」，不该人为插入等待。"""
    sleeper = RecordingSleeper()
    transport = FakeBiliTransport(archives_error=HttpError("连接被重置"))
    assert find_archive(transport, JAR, TITLE, attempts=3, delay=0.0, sleeper=sleeper) is None
    assert transport.archive_reads == 3
    assert sleeper.waits == []


def test_find_archive_gives_up_after_a_business_error_without_raising():
    """对账失败要落到「未核实」，不能把整条上传判成失败。"""
    transport = StubTransport({"code": -352, "message": "风控校验失败"})
    assert find_archive(transport, JAR, TITLE, attempts=2, delay=0, sleeper=RecordingSleeper()) is None


def test_find_archive_returns_none_for_a_blank_title_without_asking_the_platform():
    """空标题没法比对；直接返回，别白打一轮接口。"""
    transport = FakeBiliTransport()
    assert find_archive(transport, JAR, "   ", attempts=1) is None
    assert transport.calls == []


def test_find_archive_ignores_whitespace_around_the_title():
    """用户在手填表单里多打一个尾随空格时，对账**不能**因此找不到自己刚投的稿件。

    模块注释里写了「B站 会对标题做首尾空白处理」；只归一一边的写法会让那句话落空，
    代价是一次成功投稿被标成「待核对」。
    """
    transport = FakeBiliTransport(archives=[{"aid": 4, "title": TITLE}])
    assert find_archive(transport, JAR, f"  {TITLE}  ", attempts=1) is not None


def test_find_archive_also_matches_when_the_platform_kept_the_whitespace():
    """平台若**保留**空白（它对首尾空白的态度并非始终一致），两边都归一才仍然对得上。"""
    transport = FakeBiliTransport(archives=[{"aid": 4, "title": f"  {TITLE}  "}])
    assert find_archive(transport, JAR, TITLE, attempts=1) is not None


def test_find_archive_matches_a_title_that_differs_after_the_prefix():
    """前缀匹配是有意的：平台会截断过长标题，逐字相等反而更脆。"""
    transport = FakeBiliTransport(archives=[{"aid": 5, "title": "甲" * 20 + "丙" * 30}])
    assert find_archive(transport, JAR, "甲" * 20 + "乙" * 30, attempts=1) is not None


def test_find_archive_rejects_a_title_that_differs_inside_the_prefix():
    """反过来说，前 20 字里差一个字就不能算同一条 —— 前缀长度就是这个匹配的精度边界。"""
    transport = FakeBiliTransport(archives=[{"aid": 5, "title": "甲" * 19 + "丁" + "丙" * 40}])
    assert find_archive(transport, JAR, "甲" * 19 + "乙" + "丙" * 40, attempts=1) is None


def test_title_match_prefix_is_twenty():
    """这个常数决定了匹配精度；改动它是**有意**的行为变更，不该被顺手调大调小。"""
    assert TITLE_MATCH_PREFIX == 20


# ---------------------------------------------------------------- 稿件详情
def test_fetch_video_returns_cid_and_cover():
    """一次调用同时给出 `cid`（归集必需）与 `pic`（合集封面），少一个就得再打一次接口。"""
    transport = FakeBiliTransport(video={"cid": 8123, "pic": "https://i0.hdslb.com/x.jpg"})
    assert fetch_video(transport, JAR, "BV1xx411c7mD") == {"cid": 8123, "pic": "https://i0.hdslb.com/x.jpg"}
    assert transport.calls[0]["params"] == {"bvid": "BV1xx411c7mD"}


def test_fetch_video_returns_none_for_an_empty_bvid_without_asking():
    transport = FakeBiliTransport()
    assert fetch_video(transport, JAR, "") is None
    assert transport.calls == []


def test_fetch_video_returns_none_on_a_business_error():
    assert fetch_video(FakeBiliTransport(video=None), JAR, "BV1xx411c7mD") is None


def test_fetch_video_returns_none_on_a_network_error():
    assert fetch_video(StubTransport(error=HttpError("连接被重置")), JAR, "BV1xx411c7mD") is None


def test_fetch_video_returns_none_when_data_is_not_an_object():
    assert fetch_video(StubTransport({"code": 0, "data": []}), JAR, "BV1xx411c7mD") is None


# ---------------------------------------------------------------- 创建合集
def test_create_season_returns_the_integer_id():
    assert create_season(FakeBiliTransport(season_id=990011), JAR, title="合集", desc="简介", cover_url="http://c/1.jpg") == 990011


def test_create_season_accepts_an_object_payload_too():
    """平台两种形状都发过（裸整数 / `{"id": n}`）；只认一种是给自己埋一个「建好了却读不出编号」。"""
    transport = StubTransport({"code": 0, "data": {"id": 555}})
    assert create_season(transport, JAR, title="合集", desc="简介", cover_url="") == 555


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (777, 777),
        ("777", 777),  # 平台上见过把编号发成字符串的形态
        ({"id": 777}, 777),
        ({"id": "777"}, 777),
    ],
)
def test_create_season_reads_the_id_out_of_every_shape_the_platform_sends(data, expected):
    """`_season_id_of` 是这两种形态的**唯一**收口 —— 它放宽一格，就是「合集建好了却读不出编号」。"""
    transport = StubTransport({"code": 0, "data": data})
    assert create_season(transport, JAR, title="合集", desc="简介", cover_url="") == expected


@pytest.mark.parametrize("data", [None, 0, "", "abc", {}, {"id": None}, {"id": "abc"}, {}, [555], {"other": 1}])
def test_create_season_reports_a_missing_id_for_anything_that_is_not_a_number(data):
    """坏形状必须走到「没有拿到编号」这一句，而不是把 `None` 或异常漏给调用方。

    `0` 也在这一批里：判的是「有没有编号」而不是「是不是 `None`」，`int("abc")` 抛出的
    `ValueError` 与 `[555]`/`None` 抛出的 `TypeError` 都要被吞成 `None`。
    """
    transport = StubTransport({"code": 0, "data": data})
    with pytest.raises(BiliApiError) as excinfo:
        create_season(transport, JAR, title="合集", desc="简介", cover_url="")
    assert "没有拿到编号" in excinfo.value.message


def test_create_season_puts_csrf_in_the_form():
    """与 `add_episodes` **相反**：这里的 csrf 在表单正文里。"""
    transport = FakeBiliTransport()
    create_season(transport, JAR, title="合集", desc="简介", cover_url="http://c/1.jpg")
    call = transport.calls[0]
    assert call["form"] == {
        "title": "合集",
        "desc": "简介",
        "cover": "http://c/1.jpg",
        "season_price": 0,
        "csrf": "jct-value",
    }
    assert call["params"] == {}
    assert call["json_body"] is None


def test_create_season_refuses_without_csrf_and_makes_no_request():
    """`bili_jct` 就是 csrf。没有它一定失败，所以本地就拒，别去平台上试一次。"""
    transport = FakeBiliTransport()
    with pytest.raises(BiliApiError) as excinfo:
        create_season(transport, jar_without_csrf(), title="合集", desc="简介", cover_url="")
    assert "bili_jct" in excinfo.value.message
    assert transport.calls == []


def test_create_season_refuses_when_the_response_carries_no_id():
    """HTTP 200 + `code: 0` 但 `data` 是 0 —— 平台「成功」却不给编号时必须当场说清楚。"""
    transport = FakeBiliTransport(season_id=0)
    with pytest.raises(BiliApiError) as excinfo:
        create_season(transport, JAR, title="合集", desc="简介", cover_url="")
    assert "没有拿到编号" in excinfo.value.message


def test_create_season_propagates_a_business_error():
    transport = StubTransport({"code": -400, "message": "标题不合法"})
    with pytest.raises(BiliApiError) as excinfo:
        create_season(transport, JAR, title="合集", desc="简介", cover_url="")
    assert excinfo.value.code == -400


def test_create_season_lets_a_network_error_through_as_http_error():
    """「平台说要人工审核」与「根本没连上」是两回事，调用方要对它们做不同处置。"""
    with pytest.raises(HttpError):
        create_season(FakeBiliTransport(season_add_error=HttpError("连接被重置")), JAR, title="t", desc="d", cover_url="")


# ---------------------------------------------------------------- 归集
def test_add_episodes_uses_camel_case_body_and_csrf_in_the_query():
    """**本模块最容易写反的一处。** 文档表格里那对下划线命名是笔误：真实的参数名是
    `sectionId`，而且 csrf 必须走 query —— 写错的那一半只会在真账号上 404。
    """
    transport = FakeBiliTransport()
    add_episodes(transport, JAR, section_id=990011, episodes=[{"aid": 111, "cid": 7001, "title": "第一集"}])
    call = transport.calls[0]
    assert call["params"] == {"csrf": "jct-value"}
    assert call["form"] == {}
    assert call["json_body"] == {
        "sectionId": 990011,
        "episodes": [{"aid": 111, "cid": 7001, "title": "第一集", "charging_pay": 0}],
    }
    assert "section_id" not in call["json_body"]


def test_add_episodes_coerces_ids_to_int():
    """平台对字符串数字不宽容；这里统一转成 int，避免「本地看着对、平台 404」。"""
    transport = FakeBiliTransport()
    add_episodes(transport, JAR, section_id="990011", episodes=[{"aid": "111", "cid": "7001", "title": "t"}])
    body = transport.calls[0]["json_body"]
    assert body["sectionId"] == 990011
    assert body["episodes"][0]["aid"] == 111
    assert body["episodes"][0]["cid"] == 7001


def test_add_episodes_sends_several_episodes_in_one_call():
    """一次请求带上全部条目：逐条调用会被频控，也会在「第 3 条失败」时留下半个合集。"""
    transport = FakeBiliTransport()
    episodes = [{"aid": n, "cid": n * 10, "title": f"第 {n} 集"} for n in (1, 2, 3)]
    add_episodes(transport, JAR, section_id=1, episodes=episodes)
    assert len(transport.calls) == 1
    assert len(transport.calls[0]["json_body"]["episodes"]) == 3


def test_add_episodes_refuses_without_csrf_and_makes_no_request():
    transport = FakeBiliTransport()
    with pytest.raises(BiliApiError) as excinfo:
        add_episodes(transport, jar_without_csrf(), section_id=1, episodes=[{"aid": 1, "cid": 2, "title": "t"}])
    assert "bili_jct" in excinfo.value.message
    assert transport.calls == []


def test_add_episodes_propagates_a_business_error():
    transport = StubTransport({"code": 21070, "message": "已经添加过"})
    with pytest.raises(BiliApiError) as excinfo:
        add_episodes(transport, JAR, section_id=1, episodes=[{"aid": 1, "cid": 2, "title": "t"}])
    assert excinfo.value.code == 21070
    assert excinfo.value.where == "归集稿件"


def test_add_episodes_lets_a_network_error_through_as_http_error():
    with pytest.raises(HttpError):
        add_episodes(
            FakeBiliTransport(episodes_error=HttpError("连接被重置")),
            JAR,
            section_id=1,
            episodes=[{"aid": 1, "cid": 2, "title": "t"}],
        )


# ---------------------------------------------------------------- 合集列表
def test_list_seasons_unwraps_the_inner_season_object():
    assert list_seasons(FakeBiliTransport(season_id=990011), JAR) == [{"id": 990011, "title": "已有合集"}]


def test_list_seasons_returns_empty_on_failure():
    """它只是用来把 season_id 反查成标题；查不到就不显示标题，不该让调用方崩掉。"""
    assert list_seasons(StubTransport({"code": -101, "message": "未登录"}), JAR) == []
    assert list_seasons(StubTransport(error=HttpError("断网了")), JAR) == []


def test_list_seasons_skips_rows_without_an_inner_season():
    transport = StubTransport({"code": 0, "data": {"seasons": [None, "junk", {"season": {"id": 1}}, {"id": 2}]}})
    assert list_seasons(transport, JAR) == [{"id": 1}]


# ---------------------------------------------------------------- 合集 → 分区
def test_find_season_section_returns_the_section_id_not_the_season_id():
    """**本缺陷的回归断言。** 归集接口的 `sectionId` 要的是**分区 id**：

    平台上的分区 id 与合集 id 是两个独立编号（实测 `9343273` → `10455347`）。把合集 id
    原样发过去得到的是 `code=-404 message=啥都木有`，而且重试不自愈 —— 所以这条用例同时
    钉住「换过号」和「换出来的不是同一个数」两件事。
    """
    transport = FakeBiliTransport(season_id=990011)
    picked = find_season_section(transport, JAR, season_id=990011, attempts=1)
    assert picked == {"id": section_id_for(990011), "title": "正片"}
    assert picked["id"] != 990011


def test_find_season_section_prefers_the_platform_default_section():
    """`type=1` 是平台自动建的默认分区。就算它的 `order` 更靠后，也要选它 —— 那才是
    「正片」该去的地方，用户自建的分区各有各的用途。"""
    transport = FakeBiliTransport(
        seasons=[{"id": 7001, "title": "自己的合集"}],
        season_sections={
            7001: [
                {"id": 11, "title": "第一讲", "type": 0, "order": 1},
                {"id": 12, "title": "正片", "type": 1, "order": 5},
            ]
        },
    )
    assert find_season_section(transport, JAR, season_id=7001, attempts=1) == {"id": 12, "title": "正片"}


def test_find_season_section_falls_back_to_the_first_custom_section():
    """实测存在**没有默认分区**的合集（`2026-math4cs` 有 7 个 `type=0` 的分区）。
    这类合集按 `order` 取最靠前的一个；判失败会让它永远归不了集。"""
    transport = FakeBiliTransport(
        seasons=[{"id": 7001, "title": "只有自定义分区"}],
        season_sections={
            7001: [
                {"id": 21, "title": "第二讲", "type": 0, "order": 2},
                {"id": 22, "title": "第一讲", "type": 0, "order": 1},
            ]
        },
    )
    assert find_season_section(transport, JAR, season_id=7001, attempts=1) == {"id": 22, "title": "第一讲"}


def test_find_season_section_returns_none_when_the_season_has_no_sections():
    """合集读到了、但里面一个分区都读不出来：返回 `None` 让调用方停手，
    **不能退化成「用合集 id 试试」**。"""
    transport = FakeBiliTransport(seasons=[{"id": 7001, "title": "空合集"}], season_sections={7001: []})
    assert find_season_section(transport, JAR, season_id=7001, attempts=1) is None


def test_find_season_section_returns_none_when_the_season_is_absent():
    transport = FakeBiliTransport(seasons=[])
    assert find_season_section(transport, JAR, season_id=7001, attempts=1) is None


def test_find_season_section_retries_while_a_new_season_is_missing_from_the_list():
    """新合集在平台列表里有延迟。读一次没有就退避再读 —— 一次就判死会让「刚建好就归集」
    这条路稳定失败，而用户看到的是「合集建好了，但视频没加进去」。"""
    transport = FakeBiliTransport(season_id=990011, season_visible_after=2)
    sleeper = RecordingSleeper()
    picked = find_season_section(transport, JAR, season_id=990011, attempts=3, delay=1.0, sleeper=sleeper)
    assert picked == {"id": section_id_for(990011), "title": "正片"}
    assert transport.season_reads == 3
    assert sleeper.waits == [1.0, 1.0]


def test_find_season_section_does_not_sleep_after_the_final_attempt():
    sleeper = RecordingSleeper()
    transport = FakeBiliTransport(seasons=[], season_visible_after=99)
    assert find_season_section(transport, JAR, season_id=990011, attempts=2, delay=3.0, sleeper=sleeper) is None
    assert transport.season_reads == 2
    assert sleeper.waits == [3.0]


def test_find_season_section_pages_past_the_first_page():
    """合集可能不在第一页。翻页有上限，但**不能只看第一页** —— 账号下合集多于 30 个时，
    只看第一页会把「合集明明在」判成「读不到」。"""
    seasons = [{"id": 1000 + i, "title": f"合集{i}"} for i in range(35)]
    transport = FakeBiliTransport(seasons=seasons)
    assert find_season_section(transport, JAR, season_id=1034, attempts=1) == {
        "id": section_id_for(1034),
        "title": "正片",
    }
    assert transport.called("/x2/creative/web/seasons") == 2


def test_find_season_section_skips_malformed_section_rows():
    """分区行里混进 `None`/字符串/拿不出 id 的脏数据时，跳过它们而不是整体崩掉。"""
    transport = FakeBiliTransport(
        seasons=[{"id": 7001, "title": "脏数据"}],
        season_sections={
            7001: [
                None,
                "junk",
                {"id": None, "title": "没有 id"},
                {"id": "12x", "title": "不是数字"},
                {"id": "345", "title": "能用的"},
            ]
        },
    )
    assert find_season_section(transport, JAR, season_id=7001, attempts=1) == {"id": 345, "title": "能用的"}


def test_find_season_section_returns_none_on_a_business_error():
    transport = StubTransport({"code": -101, "message": "账号未登录"})
    assert (
        find_season_section(transport, JAR, season_id=7001, attempts=2, delay=0, sleeper=RecordingSleeper())
        is None
    )


def test_find_season_section_returns_none_on_a_network_error():
    assert find_season_section(StubTransport(error=HttpError("断网了")), JAR, season_id=7001, attempts=1) is None


def test_find_season_section_returns_none_for_a_bogus_season_id_without_asking():
    """编号本身不是数字时不必打接口。"""
    transport = FakeBiliTransport()
    assert find_season_section(transport, JAR, season_id="不是编号", attempts=1) is None
    assert transport.calls == []


# ---------------------------------------------------------------- 找到自己的评论
def test_find_own_comment_matches_mid_and_marker():
    replies = [own_reply(1, "别人的评论", mid=99), own_reply(2, f"本视频由{MARKER}")]
    transport = FakeBiliTransport(replies=replies)
    assert find_own_comment(transport, JAR, oid=111, marker=MARKER, mode=3) == 2


def test_find_own_comment_ignores_other_peoples_matching_comments():
    """`mid` 不符就不算我的 —— 否则会把观众复制粘贴的同一句话认成自己发过的。"""
    transport = FakeBiliTransport(replies=[own_reply(1, MESSAGE, mid=99)])
    assert find_own_comment(transport, JAR, oid=111, marker=MARKER, mode=3) is None


def test_find_own_comment_ignores_my_comments_without_the_marker():
    """我自己发的**别的**评论不该让这次置顶被跳过。"""
    transport = FakeBiliTransport(replies=[own_reply(1, "沙发")])
    assert find_own_comment(transport, JAR, oid=111, marker=MARKER, mode=3) is None


def test_find_own_comment_returns_none_without_a_numeric_mid_and_makes_no_request():
    """没有 `DedeUserID` 就无法判断哪条是自己的；直接放弃，别打接口。"""
    transport = FakeBiliTransport(replies=[own_reply(1, MESSAGE)])
    assert find_own_comment(transport, {"SESSDATA": "s"}, oid=111, marker=MARKER, mode=3) is None
    assert transport.calls == []


def test_find_own_comment_passes_the_mode_through():
    """mode 3 = 热门、mode 2 = 时间序；平台判重之后要靠换 mode 再找一次。"""
    transport = FakeBiliTransport()
    find_own_comment(transport, JAR, oid=111, marker=MARKER, mode=2)
    assert transport.calls[0]["params"] == {"oid": 111, "type": 1, "mode": 2}


def test_find_own_comment_returns_none_on_a_business_error():
    assert find_own_comment(StubTransport({"code": -404, "message": "啥都木有"}), JAR, oid=111, marker=MARKER, mode=3) is None


def test_find_own_comment_returns_none_on_a_network_error():
    assert find_own_comment(StubTransport(error=HttpError("超时")), JAR, oid=111, marker=MARKER, mode=3) is None


# ---------------------------------------------------------------- 发评论并置顶
def test_send_and_top_comment_posts_then_tops():
    transport = FakeBiliTransport(comment_rpid=777)
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "posted"
    assert result.rpid == 777
    assert result.message == "评论已发送并置顶"
    assert calls_to(transport, "/x/v2/reply/add")[0]["form"] == {
        "oid": 111,
        "type": 1,
        "message": MESSAGE,
        "plat": 1,
        "csrf": "jct-value",
        "csrf_token": "jct-value",
    }
    # 置顶拿的是**刚发出来**的那个 rpid，不是猜的。
    assert calls_to(transport, "/x/v2/reply/top")[0]["form"] == {
        "oid": 111,
        "rpid": 777,
        "type": 1,
        "action": 1,
        "csrf": "jct-value",
        "csrf_token": "jct-value",
    }


def test_send_and_top_comment_is_idempotent_and_only_tops_when_already_posted():
    """**重试路径会再调一次这个函数。** 已经发过就必须只补置顶，不能再发一条重复评论。"""
    transport = FakeBiliTransport(replies=[own_reply(888, MESSAGE)])
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "posted"
    assert result.rpid == 888
    assert transport.called("/x/v2/reply/add") == 0
    assert transport.called("/x/v2/reply/top") == 1


def test_send_and_top_comment_refinds_in_time_order_after_a_duplicate():
    """平台判重（12051）先于本地判重：热门列表里还没有这条，要按时间序再找一次。

    这条只有在「热门为空、时间序有」时才测得到 —— 两种结果一样的话，那句 mode=2 的
    重找根本没被执行，用例会假绿。
    """
    transport = FakeBiliTransport(
        replies=[],
        reply_pages={2: [own_reply(555, MESSAGE)]},
        comment_code=COMMENT_DUPLICATE_CODE,
    )
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "posted"
    assert result.rpid == 555
    modes = [call["params"]["mode"] for call in calls_to(transport, "/x/v2/reply/main")]
    assert modes == [3, 2]
    assert transport.called("/x/v2/reply/top") == 1


def test_send_and_top_comment_reports_already_when_the_duplicate_cannot_be_located():
    """判重了但两次都找不到 rpid：如实说「已存在、请手动置顶」，**不要**假装成功。"""
    transport = FakeBiliTransport(replies=[], comment_code=COMMENT_DUPLICATE_CODE)
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "already"
    assert result.rpid is None
    assert "手动" in result.message
    assert transport.called("/x/v2/reply/top") == 0


def test_send_and_top_comment_fails_without_csrf_and_makes_no_request():
    transport = FakeBiliTransport()
    result = send_and_top_comment(transport, jar_without_csrf(), oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "failed"
    assert result.rpid is None
    assert "bili_jct" in result.message
    assert transport.calls == []


def test_send_and_top_comment_reports_a_post_failure_with_the_code():
    transport = FakeBiliTransport(comment_code=-412, comment_message="请求被拦截")
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "failed"
    assert result.rpid is None
    assert "-412" in result.message and "请求被拦截" in result.message


def test_send_and_top_comment_does_not_top_after_a_post_failure():
    transport = FakeBiliTransport(comment_code=-412)
    send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert transport.called("/x/v2/reply/top") == 0


def test_send_and_top_comment_survives_a_network_error_while_posting():
    result = send_and_top_comment(
        StubTransport(error=HttpError("连接被重置")), JAR, oid=111, message=MESSAGE, marker=MARKER
    )
    assert result.state == "failed"
    assert "评论没有发出去" in result.message


def test_send_and_top_comment_keeps_the_rpid_when_only_topping_fails():
    """**这条决定用户能不能自救**：评论已经发出去了，rpid 必须带回去，用户才能手动置顶。"""
    transport = FakeBiliTransport(comment_rpid=777, top_code=-400, top_message="置顶失败")
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "failed"
    assert result.rpid == 777
    assert "置顶失败" in result.message
    assert "-400" in result.message


def test_send_and_top_comment_keeps_the_rpid_when_topping_raises():
    transport = FakeBiliTransport(comment_rpid=777, top_error=HttpError("连接被重置"))
    result = send_and_top_comment(transport, JAR, oid=111, message=MESSAGE, marker=MARKER)
    assert result.state == "failed"
    assert result.rpid == 777
    assert "评论发出去了" in result.message
