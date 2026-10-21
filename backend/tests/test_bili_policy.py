"""`bili_policy`：文案表、状态折合与失败分类。

这一层全是常量与映射，所以用例的价值集中在**跨表的一致性**上，而不是逐个值抄一遍：

1. 平台 `state` 表与三个集合（已发布 / 审核中 / 已退回）必须覆盖同一批键 ——
   漏一个键不会报错，只会让那条稿件永远停在「状态未知」，而它其实有明确含义。
2. **不许把「已提交」表述成「已发布」**。平台有转码与人工审核，两者之间隔着几天
   与一次驳回的可能；说成已发布会让用户不再回来看。
3. 失败分类的**顺序即优先级**：同一个输出里同时命中两条时，先命中的那条才对。
"""

import pytest

from app.bili_policy import (
    BADGE_FAILED,
    BADGE_NONE,
    BADGE_PARTIAL,
    BADGE_TEXT,
    BADGE_TONE,
    BADGE_UNVERIFIED,
    BADGE_UPLOADED,
    BADGE_UPLOADING,
    COOKIE_TRACKED_BY_GIT,
    FAILURE_PATTERNS,
    MISSING_BILIUP,
    MISSING_COOKIE,
    MISSING_COOKIE_FIELDS,
    MISSING_FFMPEG,
    MISSING_VIDEO,
    NOT_LOGGED_IN,
    PLATFORM_STATE_TEXT,
    PUBLISHED_STATES,
    REJECTED_STATES,
    REVIEWING_STATES,
    SEASON_CREATE_FAILED,
    SEASON_EPISODES_FAILED,
    SEASON_PENDING_NO_EPISODES,
    SEASON_STATUS_TEXT,
    SEASON_SUBMITTED_NOTE,
    STATUS_TEXT,
    TIMEOUT_MARKER,
    UNKNOWN_FAILURE_ADVICE,
    UPLOAD_UNVERIFIED,
    UNVERIFIED_NOTE,
    badge_text,
    badge_tone,
    classify_failure,
    platform_state_text,
    season_status_text,
    status_for_platform_state,
    upload_status_text,
)
from app.models import BiliUploadStatus


def _advice_pairs() -> list[str]:
    """所有「(结论, 下一步)」文案，展开成一串字符串。"""
    return [text for pair in _advice_pairs_grouped() for text in pair]


def _advice_pairs_grouped() -> list[tuple[str, str]]:
    """所有「(结论, 下一步)」文案对本身（不展开）。"""
    return [
        MISSING_BILIUP,
        MISSING_COOKIE,
        MISSING_COOKIE_FIELDS,
        COOKIE_TRACKED_BY_GIT,
        MISSING_FFMPEG,
        MISSING_VIDEO,
        NOT_LOGGED_IN,
        UPLOAD_UNVERIFIED,
        SEASON_CREATE_FAILED,
        SEASON_PENDING_NO_EPISODES,
        SEASON_EPISODES_FAILED,
        *(message for _needles, message in FAILURE_PATTERNS),
    ]


def _all_displayed_text() -> list[str]:
    """所有会出现在界面上的字符串（不含匹配用的 needle）。"""
    return [
        *PLATFORM_STATE_TEXT.values(),
        *BADGE_TEXT.values(),
        *STATUS_TEXT.values(),
        *SEASON_STATUS_TEXT.values(),
        UNKNOWN_FAILURE_ADVICE,
        UNVERIFIED_NOTE,
        SEASON_SUBMITTED_NOTE,
        *_advice_pairs(),
    ]


# ---------------------------------------------------------------- 平台状态折合
def test_every_known_platform_state_folds_into_a_known_status():
    """表与三个集合必须覆盖同一批键。

    这条曾经一种写法会漏：值写进 `PLATFORM_STATE_TEXT` 却忘了加进集合，
    于是那条稿件被折成 `unverified` —— 文案说得出含义，状态却说不知道。
    """
    for state in PLATFORM_STATE_TEXT:
        assert status_for_platform_state(state) != BiliUploadStatus.unverified.value, state
        assert state in PUBLISHED_STATES | REVIEWING_STATES | REJECTED_STATES, state


def test_unknown_platform_state_stays_unverified_and_asks_a_human():
    """折不出来就是「待核对」，**不是**「已发布」—— 猜错的代价是用户以为稿件没问题。"""
    for state in (None, "", "999", "abc"):
        assert status_for_platform_state(state) == BiliUploadStatus.unverified.value
        assert "人工" in platform_state_text(state) or "创作中心" in platform_state_text(state)


def test_no_reviewing_or_rejected_state_is_described_as_published():
    """**全功能最贵的一条谎话**：把还在审核的稿件说成已发布。"""
    for state in REVIEWING_STATES | REJECTED_STATES:
        assert "已发布" not in platform_state_text(state), state


def test_submitted_status_text_says_submitted_not_published():
    assert "已提交" in STATUS_TEXT[BiliUploadStatus.submitted.value]
    assert "已发布" not in STATUS_TEXT[BiliUploadStatus.submitted.value]


def test_season_status_text_says_grouped_not_approved():
    """合集归集是「已提交」，B站 对合集有人工审核。"""
    text = season_status_text("grouped")
    assert "已提交" in text and "审核" in text


# ---------------------------------------------------------------- 徽标与状态文案
def test_status_text_covers_every_upload_status():
    assert set(STATUS_TEXT) == {status.value for status in BiliUploadStatus}


def test_badge_tables_share_the_same_keys():
    """徽标的两张表（文案 / 配色）必须一一对应，否则会出现「有字没色」这种半成品。"""
    assert set(BADGE_TEXT) == set(BADGE_TONE)
    assert set(BADGE_TEXT) == {
        BADGE_NONE,
        BADGE_UPLOADING,
        BADGE_UPLOADED,
        BADGE_UNVERIFIED,
        BADGE_PARTIAL,
        BADGE_FAILED,
    }


def test_badge_text_and_tone_fall_back_for_unknown_values():
    """未知徽标退回「未上传 / 灰」，不抛异常 —— 前端的颜色映射是穷尽的，给不出未知档。"""
    assert badge_text("未来的某一档") == BADGE_TEXT[BADGE_NONE]
    assert badge_tone("未来的某一档") == "idle"


def test_upload_status_text_falls_back_to_unknown():
    assert upload_status_text(None) == "状态未知"
    assert upload_status_text("未来的某一档") == "状态未知"


def test_season_status_text_falls_back_to_unknown():
    assert season_status_text(None) == "状态未知"


# ---------------------------------------------------------------- 失败分类
def test_timeout_marker_wins_over_everything_else():
    """顺序即优先级：本地超时是我们**自己**判定的，比 biliup 输出里的任何字样都确定。"""
    output = f"{TIMEOUT_MARKER}\ncode 601 您上传视频过快\nnot logged in"
    message, _advice = classify_failure(output)
    assert message == FAILURE_PATTERNS[0][1][0]


@pytest.mark.parametrize(
    ("output", "expected_message"),
    [
        ("code 601 您上传视频过快", "B站 说这次上传得太快了（账号侧临时限流）"),
        ("error: not logged in", NOT_LOGGED_IN[0]),
        ("No space left on device", "磁盘没有剩余空间了"),
        ("系统找不到指定的文件。", "上传时找不到视频文件了"),
    ],
)
def test_classify_failure_matches_each_known_pattern(output, expected_message):
    assert classify_failure(output)[0] == expected_message


def test_classify_failure_recognizes_the_raw_error_that_started_this():
    """**真实投稿撞过的那一行**，逐字抄下来当夹具。

    biliup 只打印这一行就退出（`-400 请求错误`），当时被折成「没能识别出具体原因」，
    用户拿着这句话无处可查 —— 这就是把最贵的一次失败变成一条分类用例的原因。
    """
    raw = 'Error: {"code":-400,"data":null,"message":"请求错误","ttl":1}'
    message, advice = classify_failure(raw)
    assert message != "投稿没有成功，而且没能识别出具体原因"
    assert "-400" in message
    # 头号成因是凭据，下一步必须指向「看凭据 / 重新登录」，而不是一句泛泛的「重试」。
    assert "凭据" in advice or "登录" in advice


def test_classify_failure_recognizes_an_expired_app_token():
    """`61000 登录状态异常` 比它后面那条泛用的「未登录」更准确，顺序不能倒。"""
    message, _advice = classify_failure('{"code":61000,"message":"登录状态异常"}')
    assert message == "B站 说这次登录状态异常，投稿凭据已经失效"
    assert message != NOT_LOGGED_IN[0]


def test_unknown_failure_advice_points_at_something_that_exists():
    """这条建议以前写的是「去 app.log 搜索 BILI 看原始报错」，而那时的日志里**根本没有**
    biliup 的输出（`on_log` 没有调用方）—— 一条指错地方的建议比没有建议更糟。

    判据：建议里必须给出一个能被 grep 到的字面前缀，且那个前缀正是我们真的会写进日志的。
    """
    assert "biliup 输出" in UNKNOWN_FAILURE_ADVICE
    assert "data/logs/app.log" in UNKNOWN_FAILURE_ADVICE


def test_classify_failure_is_case_insensitive():
    assert classify_failure("No Space Left On Device")[0] == classify_failure("no space left")[0]


def test_classify_failure_admits_it_does_not_know():
    """认不出来就如实说不知道，**不猜原因** —— 猜错会让用户去修一个不存在的问题。"""
    message, advice = classify_failure("some brand new failure we have never seen")
    assert "没能识别出具体原因" in message
    assert advice == UNKNOWN_FAILURE_ADVICE


def test_rate_limit_advice_forbids_brute_forcing():
    """601 的正确处置是「等几分钟原样重投」，不是改参数硬刷 —— 硬刷会把临时限流升级成账号风险。"""
    _message, advice = classify_failure("code 601")
    assert "等几分钟" in advice
    assert "不要改参数" in advice


def test_every_advice_pair_gives_a_next_step():
    """验收口径：只报结论不给动作的提示算未完成。"""
    for message, advice in _advice_pairs_grouped():
        assert message.strip() and advice.strip()


def test_unverified_note_and_advice_tell_the_user_to_recheck_not_reupload():
    """「待核对」是「我们还不知道」，不是「失败了」—— 说成失败会让用户重投一条已经投上去的稿件。"""
    assert "核对" in UNVERIFIED_NOTE or "核对" in UPLOAD_UNVERIFIED[1]
    assert "重新核对" in UPLOAD_UNVERIFIED[1]
    assert len(UPLOAD_UNVERIFIED[0]) < 40


def test_season_episodes_failure_is_worded_differently_from_create_failure():
    """两者的**重试代价**不同：合集已建好时重试只补加视频。文案混用会让用户不敢点重试。"""
    assert SEASON_EPISODES_FAILED[0] != SEASON_CREATE_FAILED[0]
    assert "不会重复建合集" in SEASON_EPISODES_FAILED[1]


# ---------------------------------------------------------------- 文案硬约束
@pytest.mark.parametrize("banned", ["timed out", "reset", "403"])
def test_displayed_text_avoids_bare_words_that_upstream_misclassifies(banned):
    """沿用仓库既有约定：界面文案里不出现会被上游误分类的裸词。"""
    offenders = [text for text in _all_displayed_text() if banned in text.lower()]
    assert offenders == [], offenders


def test_displayed_text_never_asserts_published_for_a_non_published_status():
    for status in (BiliUploadStatus.submitted.value, BiliUploadStatus.reviewing.value, BiliUploadStatus.queued.value):
        assert "已发布" not in STATUS_TEXT[status], status


# ---------------------------------------------------------------- 21104：被登录文案盖住的标题错误
# 2026-10-21 真实投稿（C++ Weekly Ep 105）留下的那段输出，逐字抄下来当夹具。
# 它的危险之处在于：**成功日志与失败原因混在一起** —— 前几行写着「验证cookie / 通过cookie登录」，
# 最后一行才是真正的错误。用宽松的 needle 去分类，就会把一次标题/文件名超限报成「登录已失效」，
# 而用户照那句话去重新扫码，扫多少次都不会好。
REAL_21104_TAIL = """\
 INFO biliup::uploader::credential: 验证cookie
 INFO biliup::uploader::credential: 无需更新cookie
 INFO biliup::uploader::credential: 通过cookie登录
 INFO biliup::uploader: user: ant-hengxin
 INFO biliup::uploader: Upload completed: Ep 105.mp4 => cost 12.26s, 1.16 MB/s.
 WARN biliup::uploader::bilibili: 客户端接口已失效, 将使用APP接口
 INFO biliup::uploader::bilibili: ResponseData { code: 21104, data: None, message: "第(1)个视频的标题过长,已经超过80个字符", ttl: Some(1) }
Error: ResponseData { code: 21104, data: None, message: "第(1)个视频的标题过长,已经超过80个字符", ttl: Some(1) }
"""


def test_the_title_error_is_not_mistaken_for_a_login_failure():
    """**本轮的核心回归**：成功日志里的「通过cookie登录」不能把 21104 盖成登录失效。

    判据是「结论里必须出现 21104 或『文件名』」，而不是「不等于登录失效」——
    后者在文案换个说法时依然可能绿着过去。
    """
    message, advice = classify_failure(REAL_21104_TAIL)
    assert message != NOT_LOGGED_IN[0]
    assert "21104" in message or "文件名" in message
    # 下一步要指向真能解决它的动作：重试（会自动换短文件名）或改源文件名。
    assert "重试" in advice or "改名" in advice


def test_a_benign_login_line_alone_is_not_a_failure():
    """只有成功日志、没有任何失败语句时不能凭空判成「登录已失效」。"""
    message, _advice = classify_failure("验证cookie\n通过cookie登录\nuser: ant-hengxin")
    assert message != NOT_LOGGED_IN[0]


@pytest.mark.parametrize(
    "output",
    [
        "error: not logged in",
        '{"code":-101,"message":"账号未登录"}',
        "登录信息已过期",
        "账号未登录",
    ],
)
def test_real_auth_failures_are_still_recognized(output):
    """收紧 needle 之后，真的登录失效必须还能认出来（否则修一个 bug 换来一个静默）。"""
    assert classify_failure(output)[0] == NOT_LOGGED_IN[0]
