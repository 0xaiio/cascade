"""B站 上传的**文案与分类常量**。

这一层只有字符串与纯映射，没有判定逻辑 —— 判定分别在 `bili_upload`（单次上传走哪条分支）与
`bili_manager`（状态如何落账）。把文案集中在这里的理由与 `fallback_policy` 一致：
文案是产品决策，散落在分支里就没法一次改全，也没法被参数化测试遍历。

**文案硬约束**（沿用仓库既有约定）：不得出现会被上游误分类的裸词（`timed out`、`reset`、`403`）。
另有一条本功能特有的约束：不得把「已提交」表述成「已发布」—— 平台有转码与人工审核，
两者之间隔着几天与一次驳回的可能。
"""

from __future__ import annotations

from .models import BiliUploadStatus

# ---------------------------------------------------------------- 平台稿件状态 → 人话
# state 取值与含义依据 bilibili-API-collect 的稿件状态表；未列出的值一律说「未知」并要求人工核对，
# 不猜语义 —— 猜错的代价是用户以为稿件没问题。
PLATFORM_STATE_TEXT: dict[str, str] = {
    "0": "已发布，可以正常观看",
    "-1": "等待审核",
    "-2": "审核不通过，需要去创作中心看退回原因",
    "-4": "已被锁定",
    "-9": "被管理员锁定",
    "-16": "已被禁止",
    "-30": "已提交，正在排队审核或转码",
    "-40": "定时发布中，还没到发布时间",
    "-60": "转码完成，正在等待审核",
    "1": "仅自己可见",
}

# 这些状态码代表「稿件确实存在于平台上」——只有它们能算「已上传」。
PUBLISHED_STATES = {"0"}
REVIEWING_STATES = {"-1", "-30", "-40", "-60", "1"}
REJECTED_STATES = {"-2", "-4", "-9", "-16"}


def platform_state_text(state: str | None) -> str:
    if state is None:
        return "状态未知（请到创作中心确认）"
    return PLATFORM_STATE_TEXT.get(str(state), "状态未知（请到创作中心确认）")


def status_for_platform_state(state: str | None) -> str:
    """把平台的 `state` 折成我们的稿件状态。**折不出来就是「待核对」，不是「已发布」。**"""
    value = str(state)
    if value in PUBLISHED_STATES:
        return BiliUploadStatus.published.value
    if value in REVIEWING_STATES:
        return BiliUploadStatus.reviewing.value
    if value in REJECTED_STATES:
        return BiliUploadStatus.rejected.value
    return BiliUploadStatus.unverified.value


# ---------------------------------------------------------------- 界面状态与文案
# 界面只认这 5 档；由 `bili_manager` 的读模型算出来，理由与 `fallback_policy` 同：
# 文案留在领域层，前端只负责把 status 变成颜色。
BADGE_NONE = "none"
BADGE_UPLOADING = "uploading"
BADGE_UPLOADED = "uploaded"
BADGE_UNVERIFIED = "unverified"
BADGE_PARTIAL = "partial"
BADGE_FAILED = "failed"

BADGE_TEXT: dict[str, str] = {
    BADGE_NONE: "未上传",
    BADGE_UPLOADING: "上传中",
    BADGE_UPLOADED: "已上传",
    BADGE_UNVERIFIED: "待核对",
    BADGE_PARTIAL: "部分失败",
    BADGE_FAILED: "失败",
}

# 配色档位（前端把它映射成 CSS 类）。语义：ok=可以收工，busy=还在跑，
# warn=有疑问要去确认，bad=需要处理，idle=还没开始。
BADGE_TONE: dict[str, str] = {
    BADGE_NONE: "idle",
    BADGE_UPLOADING: "busy",
    BADGE_UPLOADED: "ok",
    BADGE_UNVERIFIED: "warn",
    BADGE_PARTIAL: "warn",
    BADGE_FAILED: "bad",
}


def badge_text(badge: str) -> str:
    return BADGE_TEXT.get(badge, BADGE_TEXT[BADGE_NONE])


def badge_tone(badge: str) -> str:
    return BADGE_TONE.get(badge, "idle")


# ---------------------------------------------------------------- 记录级状态 → 人话
# 徽标（badge）是「界面上的那一个图标」，这里的 status_text 是「某一条记录那一行的说明」——
# 两者粒度不同：合集里可以有 3 条记录、界面只有 1 个徽标。
STATUS_TEXT: dict[str, str] = {
    BiliUploadStatus.queued.value: "排队中",
    BiliUploadStatus.uploading.value: "上传中",
    BiliUploadStatus.submitted.value: "已提交，正在核对",
    BiliUploadStatus.published.value: "已发布",
    BiliUploadStatus.reviewing.value: "审核中",
    BiliUploadStatus.rejected.value: "审核未通过",
    BiliUploadStatus.unverified.value: "待核对",
    BiliUploadStatus.failed.value: "失败",
}


def upload_status_text(status: str | None) -> str:
    return STATUS_TEXT.get(str(status), "状态未知")


SEASON_STATUS_TEXT: dict[str, str] = {
    "pending": "等待归集",
    "grouping": "正在归集",
    "grouped": "已提交归集（B站 需人工审核）",
    "failed": "归集失败",
}


def season_status_text(status: str | None) -> str:
    return SEASON_STATUS_TEXT.get(str(status), "状态未知")


# ---------------------------------------------------------------- 前置条件缺失
# 每一项都必须是「结论 + 下一步」，只报结论不给动作的提示视为未完成（requirements 验收口径）。
MISSING_BILIUP = (
    "没找到投稿工具 biliup",
    "在 PowerShell 里执行 winget install --id ForgQi.biliup-rs --exact，装完点「重新检查」",
)
MISSING_COOKIE = (
    "还没有登录 B站",
    "点「扫码登录」用 B站 App 扫码，或导入一份已登录的 cookies.json",
)
MISSING_COOKIE_FIELDS = (
    "登录信息不完整，投稿会被 B站 拒绝",
    "重新扫码登录一次（需要 SESSDATA 与 bili_jct 两项）",
)
MISSING_UPLOAD_TOKEN = (
    "登录信息里没有投稿要用的登录令牌，投稿会被 B站 直接退回来",
    "重新登录一次：设置里点「重新扫码登录」，或者重新导入一份新的 cookies 文件",
)
COOKIE_TRACKED_BY_GIT = (
    "登录文件已经被 git 跟踪，等于把账号密码提交到了仓库里",
    "立刻执行 git rm --cached <该文件> 并确认 .gitignore 覆盖它，然后重新登录",
)
MISSING_FFMPEG = (
    "没找到 ffmpeg，无法自动生成封面",
    "可以先不生成封面（B站 会自动选一帧），或在系统里装好 ffmpeg 后再试",
)
MISSING_VIDEO = (
    "这个视频还没有下载好，没有可以上传的文件",
    "先等下载任务跑完，或重启失败的那一条",
)
NOT_LOGGED_IN = (
    "B站 登录已失效",
    "去设置里重新扫码登录",
)

# 超时标记：`bili_bin` 在本地判定超时后写进输出的那一行（ASCII，确保不会与 biliup 自己的输出撞词）。
# 放在这一层而不是 `bili_bin` 里，是因为它同时是**分类依据**与**文案依据**。
TIMEOUT_MARKER = "local-upload-deadline-reached"

# ---------------------------------------------------------------- 失败分类
# 每一条都是 (错误结论, 下一步)。顺序即优先级：先命中的先返回。
FAILURE_PATTERNS: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        (TIMEOUT_MARKER,),
        (
            "这次上传等得太久，已经停止等待",
            "视频很大或网络很慢时会出现。先确认网络正常，再重试一次；反复出现就去 v 站或 B站 创作中心看有没有半途的记录",
        ),
    ),
    # `-400 请求错误` 是 B站 的泛用拒绝码，**在投稿这条路上它的头号成因是凭据**：
    # biliup 启动时先拿 token_info.access_token 去校验，空串或签名不对都会被判 -400，
    # 而且是在发出任何一个投稿请求之前就退出。实测过（见 ai/bug-fix 的对应记录）。
    (
        ("请求错误", '"code":-400', '"code": -400'),
        (
            "B站 退回了这次投稿请求（错误码 -400「请求错误」）",
            "这个码在投稿里最常见的原因是登录信息缺投稿令牌。去设置里看凭据那一项，缺了就重新登录一次再重试",
        ),
    ),
    # 放在「未登录」那一条**之前**：它的 needle 里有 `登录`，慢一步就会被折成「登录已失效」，
    # 丢掉「状态异常」这个更准确的措辞。
    (
        ("61000", "登录状态异常"),
        (
            "B站 说这次登录状态异常，投稿凭据已经失效",
            "去设置里重新登录一次，然后重试",
        ),
    ),
    (
        ("601",),
        (
            "B站 说这次上传得太快了（账号侧临时限流）",
            "等几分钟后用同样的设置重试即可，不要改参数硬刷 —— 硬刷会把临时限流升级成账号风险",
        ),
    ),
    (
        ("not logged in", "未登录", "登录", "cookie", "登录信息已过期", "账号未登录"),
        NOT_LOGGED_IN,
    ),
    (
        ("no space left", "磁盘空间", "enospc"),
        (
            "磁盘没有剩余空间了",
            "腾出空间后重试",
        ),
    ),
    (
        ("not found", "no such file", "找不到", "系统找不到"),
        (
            "上传时找不到视频文件了",
            "确认文件还在原位置（没有被移动或删除），然后重试",
        ),
    ),
)

# 「认不出来」时的建议必须指一个真的存在的东西。原先写的是「去 app.log 搜索 BILI 看原始报错」，
# 而**那时的日志里根本没有 biliup 的输出**（`on_log` 没有任何调用方），用户照做只会扑空 ——
# 一条指错地方的建议比没有建议更糟。现在失败时会把原始输出的尾部逐行写进日志。
UNKNOWN_FAILURE_ADVICE = (
    "在 data/logs/app.log 里搜「biliup 输出」看原始报错原文；"
    "也可以到 B站 创作中心确认这次投稿是不是已经不声不响地进去了"
)


def classify_failure(output: str) -> tuple[str, str]:
    """把 `biliup` 的输出归类成（结论, 下一步）。**命中不了就如实说不知道，不猜原因。**"""
    haystack = (output or "").lower()
    for needles, message in FAILURE_PATTERNS:
        if any(needle.lower() in haystack for needle in needles):
            return message
    return ("投稿没有成功，而且没能识别出具体原因", UNKNOWN_FAILURE_ADVICE)


# ---------------------------------------------------------------- 对账查不到
# 这是本功能**最容易被写成谎报**的一处：`biliup` 返回 0 只说明命令跑完了（F7），
# 平台上有没有这条稿件只能靠对账回答。查不到就停在这一档，不给「已上传」。
UPLOAD_UNVERIFIED = (
    "已提交，但还没在 B站 看到它",
    "B站 的稿件列表有几十秒到几分钟的延迟。稍后点「重新核对」再查一次，或直接去创作中心确认",
)
UNVERIFIED_NOTE = "投稿命令返回成功，但平台稿件列表里暂时没有这条稿件"

# ---------------------------------------------------------------- 合集归集
SEASON_CREATE_FAILED = (
    "视频都传上去了，但合集没能建起来",
    "点「重试归集」再试一次；若反复失败，去 B站 创作中心看看是否已有同名合集",
)
SEASON_PENDING_NO_EPISODES = (
    "视频还在 B站 排队审核，暂时没法加进合集",
    "等几分钟后点「重试归集」",
)
# 与 SEASON_CREATE_FAILED 分开，是因为**重试的代价不同**：合集已经建好时重试只补加视频，
# 用「合集没建成」去描述会让用户以为要重建一个，反而不敢重试。
SEASON_EPISODES_FAILED = (
    "合集建好了，但视频没能加进去",
    "点「重试归集」再试 —— 合集已经存在，重试只会补加视频，不会重复建合集",
)
SEASON_SUBMITTED_NOTE = "合集已提交，B站 对合集有人工审核，通过前可能暂时看不到"
