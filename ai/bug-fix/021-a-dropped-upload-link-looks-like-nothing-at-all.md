# 021 · 上传被对端断开，界面上却报「没能识别出具体原因」

## 问题

投稿 `C++ Weekly - Ep 130 - C++20's for init-statements` 失败，记录上给的是

> 投稿没有成功，而且没能识别出具体原因

下一步则让人 **去 `data/logs/app.log` 里搜「biliup 输出」**。也就是说：用户手上只有一个
「不知道」，和一条要自己去翻日志的建议。而这次的原始输出里其实写着一条**完全可读**的结论 ——
视频字节传到一半，对端把 TLS 连接关了。

## 原因

**分类表里没有「传输层失败」这一类。** 那条真实输出里一个业务码都没有：前几行是 biliup 的
成功日志（验证 cookie、登录成功），最后是 reqwest + rustls 的错误链。既有的 9 条分类（共 25 个 needle）
一条都命中不了，于是落到兜底文案。

### 1. 现场（库里的 `bili_upload` 记录，`id=34568474b6054725a0c5a2301ea75a40`）

```
status=failed  progress=0.2  attempt=1
created_at=2026-10-21 21:15:05  finished_at=2026-10-21 21:15:52   ← 47 秒
error =投稿没有成功，而且没能识别出具体原因
advice=在 data/logs/app.log 里搜「biliup 输出」看原始报错原文；…
```

`log_tail` 逐字抄下来（8 行，`?<redacted>` 是落库时的凭据脱敏，主机名保留）：

```
2026-10-21 21:15:07  INFO biliup::uploader::credential: 验证cookie
2026-10-21 21:15:07  INFO biliup::uploader::credential: 无需更新cookie
2026-10-21 21:15:07  INFO biliup::uploader::credential: 通过cookie登录
2026-10-21 21:15:07  INFO biliup::uploader: user: ant-hengxin
2026-10-21 21:15:08  INFO biliup::uploader: https://archive.biliimg.com/bfs/archive/17c3e896….jpg
2026-10-21 21:15:08  INFO biliup::uploader: number of concurrent futures: 3
2026-10-21 21:15:30  INFO biliup::uploader: Line { os: Upos, probe_url: "//upos-cs-upcdnbda2.bilivideo.com/OK", query: "probe_version=20221109&upcdn=bda2&zone=cs", cost: 340282366920938463463374607431768211455 }
2026-10-21 21:15:30  INFO biliup::uploader::line: pre_upload: {"build":2110000,"name":"C++ Weekly - Ep 130 - C++20's for init-statements [gqhHbhBoDtw].mp4","profile":"ugcupos/bup","r":"upos","size":13290460,"ssl":0,"version":"2.11.0"}
Error: error sending request for url (https://upos-cs-upcdnbda2.bilivideo.com/ugcever/n261021a21ryenp2yw6mtq65huvlc872.mp4?<redacted>)
Caused by:
    0: client error (SendRequest)
    1: connection error
    2: peer closed connection without sending TLS close_notify: …#unexpected-eof
```

（最后四行在库里的同一条 `log_tail` 内，为便于阅读单独折行。）

### 2. 它死在哪一步 —— 有两条独立证据

| 证据 | 读法 |
| --- | --- |
| `progress=0.2`，且输出里**没有** `Upload completed`、也没有 `APP接口投稿成功` | `0.2` 正是 `pre_upload` 那一档（`bili_bin._PROGRESS_STAGES`）。按 `biliup` 的顺序（`pre_upload` → 把字节 PUT 到 CDN → 最后调提交接口），它死在**传字节**这一步 |
| 报错 URL 的主机是 `upos-cs-upcdnbda2.bilivideo.com`（上传 CDN），不是 `api.bilibili.com` | 请求根本没走到提交接口 |

两条合起来给了一个可以对外说的结论：**平台上不会有这条稿件，重试不会投重**。

另外一行旁证：`Line { …, cost: 340282366920938463463374607431768211455 }` —— 那是 `u128::MAX`，
等于这一路 CDN 探测**一次也没算出代价**。它与「这一路 CDN 从一开始就不通」吻合，但
**我没有直接测到 biliup 的连通性**（见「未覆盖」），所以只当旁证用。

### 3. 为什么会全表落空

用真实输出逐条比对当时的 25 个 needle（9 条分类）（脚本见「验证」），结果全部为假：

```
'601' False   '21004' False   '21104' False   '请求错误' False   '61000' False
'not logged in' False   '未登录' False   'no space left' False   'not found' False   '找不到' False
```

这不是「needle 写得太严」的问题，而是**这张表当时根本没有「传输层」这一类**：
它的每一条都在等一个业务码或一句中文失败语，而 reqwest + rustls 报的是
`error sending request` / `connection error` / `peer closed connection`。

### 4. 为什么用户什么都看不到

前端**不渲染** `log_tail`（`grep -rn "log_tail" frontend/src` 零命中），也不渲染
`verified_note` 之外的原始输出。界面上只有 `error`（结论）+ `advice`（下一步）
（`BiliJobStatus.tsx` 的 `bili-status-advice`）。所以结论一旦是「没能识别」，
用户就只有「去翻日志」这一条路 —— 而这次连日志里都只是同一段英文错误链。

## 修复方案

### 1. 新增「传输层失败」这一类，并把 needle 的判据写死在注释里

`bili_policy.TRANSPORT_FAILURE_NEEDLES`（16 条），覆盖 reqwest / rustls / urllib3 / 系统
errno 的常见说法：`error sending request`、`client error (SendRequest)`、`client error (Connect)`、
`connection error`、`connection reset`、`connection aborted`、`connection refused`、`broken pipe`、
`peer closed connection`、`close_notify`、`unexpected-eof`、`remote end closed connection`、
`network is unreachable`、`temporary failure in name resolution`、`http status client error`、
`http status server error`。

**判据（沿用 018 那条）**：needle 必须是**失败语句本身** —— 既不是平台错误码，也不会出现在
biliup 的成功日志里。这 16 条没有一条会命中「验证cookie / 通过cookie登录 / Upload completed」。

### 2. 位置：排在 `not found` 之前（这是一处真实的次序缺陷）

reqwest 把「对端回了个 404」打印成 `HTTP status client error (404 Not Found) for url (…)` ——
那句话里就含 `not found`，而 `not found` 那条文案是给**本地文件缺失**用的
（「上传时找不到视频文件了 / 确认文件还在原位置」）。传输那条排在它后面，用户就会被派去
检查一个明明还在磁盘上的文件。现在传输那条在前，本地文件那条只接系统级 errno。

### 3. 同一类失败再细分一次：能证明「死在传字节」时才承诺「重试不投重」

`classify_failure` 命中传输那条之后，再问一句：**失败那一行里有没有上传 CDN 的主机名**
（`_dropped_while_sending_video`）？

- 有 ⇒ `UPLOAD_CDN_DROPPED`（「视频传到一半，和 B站 上传服务器的连接被对端断开了」+
  「还没到提交，点重试不会多出一条重复稿件；反复断在同一处多半是网络/代理不稳」）；
- 没有 ⇒ `UPLOAD_LINK_DROPPED`（「这次上传在传输阶段就断了」+「重试前先去创作中心看一眼」）。

为什么必须**同一行**同时满足：`pre_upload` 那行输出里就带着 `"profile":"ugcupos/bup"`，
只看主机名的话，任何一次失败（包括提交接口断线）都会被判成「传字节时断的」，
进而承诺一个**不成立**的「不会投重」。承诺投重安全的代价比不说更贵。

为什么细分逻辑放在 `classify_failure` 而不是 needle 表里：它需要「同一行」这个上下文，
`any(needle in haystack)` 表达不了；硬塞进表里就得把整张表改成带谓词的结构，
连带改掉遍历这张表的既有用例，收益不抵。

### 4. 文案

```
UPLOAD_CDN_DROPPED = (
    "视频传到一半，和 B站 上传服务器的连接被对端断开了",
    "这次失败发生在文件传输阶段（还没到提交），点「重试」重新上传即可，不会多出一条重复稿件。"
    "若反复断在同一处，多半是本机到 B站 CDN 的网络或代理不稳（上传域名 upos-*.bilivideo.com）"
    "—— 换个代理节点或临时直连再试",
)
```

仍受文案硬约束：不出现 `timed out` / `reset` / `403` 这些会被上游误分类的裸词
（`connection reset` 只作为 needle 存在于代码里，不进文案）。

**没有做自动重试**：传输层失败可能发生在提交接口上（应答丢了、稿件其实已经建好），
自动重发会造出重复稿件。重试的代价模型决定了它必须是**人点的**一个动作。

## 效果

同一份真实输出（`REAL_EP130_DROP`，逐字抄自上面 `log_tail`）：

| | 结论 | 下一步 |
| --- | --- | --- |
| 修复前 | 投稿没有成功，而且没能识别出具体原因 | 去 app.log 搜「biliup 输出」 |
| 修复后 | 视频传到一半，和 B站 上传服务器的连接被对端断开了 | 点「重试」重新上传即可，不会多出一条重复稿件；反复断在同一处多半是网络/代理不稳 |

同一条链路上的其它说法也一并落地：`connection reset by peer`、`broken pipe`、
`client error (SendRequest)`、`connection refused`、`Connection aborted: 远程主机强迫关闭了一个现有的连接`、
`remote end closed connection without response`、`peer closed connection without sending TLS close_notify`
（7 条，参数化用例）。

## 验证

新增 8 条后端用例（`test_bili_policy.py` 7 + `test_bili_upload.py` 1）：

| 用例 | 钉住什么 |
| --- | --- |
| `test_the_transport_drop_that_started_this_is_no_longer_unrecognized` | 真实输出**不能再落到兜底**；结论必须落在传输那两条常量之一，且不许说「登录」「找不到」 |
| `test_a_drop_inside_the_cdn_transfer_promises_retry_is_safe` | 死在 CDN 段 ⇒ 结论是 `UPLOAD_CDN_DROPPED`，且文案里有「不会多出一条重复稿件」 |
| `test_a_drop_outside_the_cdn_transfer_does_not_promise_retry_is_safe` | 断在提交接口（URL 是 `api.bilibili.com`）⇒ 换成通用结论，**不许**承诺不投重，且要指向创作中心 |
| `test_transport_failure_wording_variants_are_all_recognized`（7 例） | 换一种说法（重置 / 管道 / 拒绝 / 对端关闭 / 中文 errno）都不许沉默 |
| `test_every_transport_needle_is_a_failure_statement` | needle 全小写、无首尾空白、长度 ≥ 6 —— 挡住「裸词」这一类回归 |
| `test_a_cdn_side_http_error_is_not_read_as_a_missing_local_file` | CDN 侧 404 不许被读成「本地找不到文件」（次序缺陷） |
| `test_a_successful_upload_output_is_not_classified_as_a_drop` | 成功日志（含 `Upload completed` / `APP接口投稿成功` / `{"code": 0}`）不许被认成断线 |
| `test_run_single_reports_a_transport_drop_as_a_drop_not_as_an_unknown` | **接线**：`classify_failure` 拿到的是原始输出；结论/下一步原样落到 `UploadResult`；命令失败时不去对账 |

结果：`backend/tests/test_bili_policy.py` + `test_bili_upload.py` **128 passed**；
后端全量见提交说明。

**承重测试**（不是「跑得通」）：把传输那条的 needle 换成 `()`（等于回到修复前），
同一组用例 **12 条转红**（含上面 7 条语料与 `run_single` 那条），还原后 128 全绿。

**逐条 needle 比对**（证明「全表落空」不是推断）：

```python
row = con.execute("select log_tail from bili_upload where title like '%init-statements%'").fetchone()
for needle in ("601", "21004", "21104", "请求错误", "61000", "not logged in", "未登录",
               "no space left", "not found", "no such file", "找不到"):
    print(repr(needle), needle.lower() in row[0].lower())     # 全部 False
```

## 风险与回滚

- **误报面变宽**：`connection reset` / `connection refused` 这类词在别的工具的失败输出里也常见，
  但 `classify_failure` 只吃 `biliup` 的输出，且这 16 条都不会出现在 biliup 的成功日志里
  （有用例钉住这一点）。真正的风险是「把一次本该更精确的分类抢走」——`601`、`21104`、`-400`、
  `61000` 都排在它前面，不受影响。
- **`UPLOAD_CDN_DROPPED` 里承诺了「不会多出一条重复稿件」**：该承诺的成立完全依赖
  「同一行里同时有传输失败语句与 CDN 主机名」。故意收窄到同行的原因见上；如果将来 biliup
  改了输出格式（比如把 URL 单独打一行），这条会退化成 `UPLOAD_LINK_DROPPED` ——
  **退回的是更保守的说法，不会退成假承诺**。
- 回滚：`git revert <commit>`，无数据迁移、无接口字段变化（`docs/openapi.yaml` 未动）。
- 已有的错误记录不会被自动改写：库里那条仍是旧文案，用户点一次「重试」会重新判一次。

## 未覆盖 / 如实说明

- **没有代用户真实重投**。重投会产出一条**公开**稿件（018 那次是代执行的），且这次失败
  很可能与网络路径有关、重投未必成功，所以只做了离线验证。判断它是否已经修好的真实判据是
  「同一条记录点重试 → 走到 `uploading 0.2 → … → reviewing`」，这一步留给用户。
- **没有测到 biliup 到 CDN 的真实可达性**。我用 `curl https://upos-cs-upcdnbda2.bilivideo.com/OK`
  探过（HTTP 200），但这个 shell 里 `HTTP(S)_PROXY=127.0.0.1:54791`，`remote_ip` 打出来是
  `127.0.0.1` —— 走的是本机代理，与 biliup（应用进程无代理环境变量，直连）**不是同一条路径**，
  所以这次探测不能当证据。`Line{… cost: u128::MAX }` 只是旁证。
- **CDN 侧 HTTP 4xx/5xx 这一支没有真实样本**：`http status client error` /
  `http status server error` 两条 needle 是按 reqwest 的 Display 格式加的，只有单测覆盖。
- **兜底文案没有变**。仍然存在「真的认不出来」的失败，那时用户还是只有「去 app.log 搜 raw」。
  把最后一行 `Error:` 脱敏后拼进结论是更彻底的做法，但会改动结论的短句契约与前端排版，
  本轮没做 —— 若要推进，建议另开一条。
- `601` 这条 needle 仍是**裸数字**（`Grep` 一下就知道它在时间戳/上传路径里都有机会出现，
  这次的真实输出恰好没有）。它是既有的已知隐患，与这条修复无因果，本轮不动。

## 关联

- [018](018-a-long-file-name-looks-like-a-login-failure.md)：同一条原则的两个方向 ——
  018 是「needle 太宽，把别的失败吞进登录」，这里是「表里缺一类，什么也吞不到」。
- [011](011-biliup-refuses-a-token-less-cookie-file.md)：同一种体验缺陷的早期版本 ——
  结论指错方向（让人去查凭据），而真实原因是本机环境。
- [015](015-orphan-items-never-resume.md)：无关，但同属「失败之后没有下一步」这一类。
- 与 [020](020-the-progress-clock-is-eight-hours-off.md) 无关：那条改的是时间契约。
