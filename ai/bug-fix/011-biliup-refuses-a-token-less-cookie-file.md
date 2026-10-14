# 011 · 扫码登录只拿到 cookies，biliup 拒绝开工：投稿必失败，且查不出原因

- **状态**：已修复
- **发现方式**：真实投稿（`C++ Weekly - Ep 22 C++17's Nested Namespaces and \`std::clamp\``）
- **提交**：本次提交（hash 见索引，按目录约定下次触碰时回填）

## 问题

点「上传到 B站」之后，任务行上出现：

> C++ Weekly - Ep 22 … · **失败**
> 投稿没有成功，而且没能识别出具体原因
> 去 data/logs/app.log 搜索 BILI 看原始报错，或到 B站 创作中心确认是否存在这次投稿

三处都不通：**（一）** 原因没识别出来；**（二）** 照它说的去 `data/logs/app.log` 搜，什么都搜不到
—— 日志里只有我们自己那一行，biliup 的原文一行都没有；**（三）** 整个过程只花了 1.4 秒
（`17:45:04.193` 开始、`17:45:05.596` 结束），而视频有 30 MB 上下，不可能已经传完。

## 原因

**登录文件里的 `token_info.access_token` 是空串，biliup 在发出任何请求之前就自己退出了。**

链路（每一环都有实测证据，没有「可能是」）：

1. **命令确实失败了，只打印一行。** 库里 `bili_upload.log_tail`（`id=2e19d321…`）逐字是：

   ```
   Error: {"code":-400,"data":null,"message":"请求错误","ttl":1}
   ```

   `Error: {…}` 是 Rust `main` 返回 `Err` 的形状 —— 说明它在自己的网络层就返回了，没有走到投稿。

2. **与视频、标题、分区、封面都无关。** 用**只读**命令复现，报的是同一行：

   ```bash
   biliup.exe -u data/bili_cookies.json list
   # Error: {"code":-400,"data":null,"message":"请求错误","ttl":1}
   ```

   `list` 只读稿件列表、不碰视频文件。所以问题在**凭据加载**这一步。

3. **tracing 显示第一个连接打的是 `passport.bilibili.com`，不是 `member.bilibili.com`：**

   ```
   --rust-log trace list
   DEBUG reqwest::connect: starting new connection: https://passport.bilibili.com/
   ```

   拿凭据的第一步就去 passport 校验了。

4. **源码确认了条件分支。** `biliup-rs` 的 `crates/biliup/src/uploader/credential.rs`：
   `upload_by_command` → `login_by_cookies()` → **`validate_tokens()`，无条件先跑**：

   ```rust
   let need_refresh = client.validate_tokens(&login_info).await?;
   ```
   ```rust
   // validate_tokens 里
   let response = self.0.client
       .get("https://passport.bilibili.com/x/passport-login/oauth2/info")
       .query(&payload)   // payload["access_key"] = login_info.token_info.access_token
       …
   let refresh = match response {
       ResponseData { data: Some(ResponseValue::OAuth(OAuthInfo { refresh, .. })), .. } => refresh,
       _ => return Err(Kind::Custom(response.to_string())),   // ← 我们命中的就是这一支
   };
   ```

   `data` 是 `null` ⇒ 落到 `_ => Err`，把原始响应体当错误抛出 —— 与我们拿到的输出逐字节一致。

5. **空 `access_key` 就是这个 `-400`。** 用同样的签名算法（`md5(urlencoded + appsec)`，键按字典序）
   直接探测该端点：

   | `access_key` | 平台响应 |
   |---|---|
   | 空串（我们的值） | `{"code":-400,"message":"请求错误","ttl":1}` |
   | `deadbeef…`（非空假值） | `{"code":61000,"message":"登录状态异常","ttl":1,"data":null}` |

   非空假值给的是**另一个**码 —— 所以 `-400` 不是「token 无效」的泛称，它是「这个字段是空的」。

6. **空串是我们自己写进去的。** `bili_auth.build_login_info()` 原文：

   ```python
   "token_info": {"access_token": "", "expires_in": 0, "mid": mid or 0, "refresh_token": refresh_token or ""}
   ```

   而扫码登录（`/x/passport-login/web/qrcode/*`）**只发 cookies，不发 app token**。
   两件事拼在一起：文件结构合法（biliup 能反序列化）、内容却不足以开工。

7. **原始输出没有进日志，是第二个独立缺陷。** `UploadContext.on_log` 定义了、
   `bili_upload._execute` 也调了，但**全仓库没有任何调用方给它赋值**：

   ```bash
   grep -rn "on_log" backend/app/*.py
   # bili_upload.py:120:    on_log: LineSink | None = None
   # bili_upload.py:222-223:   （只有消费，没有生产）
   ```

   于是失败建议「去 app.log 搜 BILI」指向了一个空地方 —— **一条指错地方的建议比没有建议更糟**。

### 附带纠正：`--submit web` 是空操作

`bili_bin.build_upload_argv` 的注释与 `01-requirements.md` F6 都写着「`--submit web` 是必须的，
因为 cookie 登录拿不到 access_token」。**两句都不对**：

- 上游 `uploader.rs` 的 `match submit` **两个分支都调 `submit_by_app`**（web 分支被注释掉了，
  旁边留着一句「说不定会适配 web 呢」）；
- `biliup.exe` 里**没有** `x/vu/web/add` 这个串，只有 `x/vu/app/add`：

  ```bash
  python -c "print(b'x/vu/web/add' in open(EXE,'rb').read(), b'x/vu/app/add' in open(EXE,'rb').read())"
  # False True
  ```

所以真正拦住投稿的不是提交接口，而是开工前那次令牌校验。这个开关照传不误，但**不能再拿它解释失败**。

### 附带修复：日志清洗漏掉 JSON 形式的凭据

**为什么并进这一条而不是单独成篇**：本次修复的「风险与回滚」里承诺「令牌值不进日志」，
而这条缓解措施在**令牌实际存在的那个形态上**是坏的。修它是让本条的承诺成立，不是一个独立的问题。

`log_safety._SECRET_ASSIGNMENT_RE` 只认 `name=value`，而 B站 的接口正文全是 JSON，
令牌在里面长这样：`"access_token":"…"`。实测（修复前）：

```python
sanitize_log_message('{"access_token":"LEAK-TOKEN"}')
# => '{"access_token":"LEAK-TOKEN"}'      ← 一字未改
sanitize_log_message("access_token=LEAK-TOKEN")
# => 'access_token=<redacted>'            ← 赋值形式本来就是好的
```

**可达性**：修复前为零。三个失败分支的文案只拼 `code` 与 `message`，从不回显正文
（`_tv_json` 只带 HTTP 状态码）；`exchange_tv_token` 的成功响应虽然含令牌，
但我们只把令牌写进文件、不进任何日志。所以这是一个**潜伏的洞**，不是一次已经发生的泄漏。
仍然要修的理由是：清洗器是 FR-B12「日志不回显凭据」声明的兜底，
而 011 是**第一次让「代码手里真的持有一份 B站 access_token」**，
兜底恰好在令牌的形状上失效 —— 留着它，下一个把响应正文交给日志的人会以为有兜底。

修法：加一条只认「引号包着的键」的正则，避免把散文里的同名词一起吃掉：

```python
_SECRET_JSON_RE = re.compile(rf"(?i)([\"'])({_SECRET_NAMES})\1\s*:\s*([\"'])(.*?)\3")
```

`"cookie_name":"x"` 不匹配（`cookie` 后面紧跟的不是引号），`"mid":479141149` 也原样保留 ——
日志要能用来排查，过度脱敏是另一种坏掉。

## 修复方案

**一句话：登录的完成判据从「文件里有 SESSDATA」改成「拿到投稿令牌」。**

1. **补上令牌交换**（`bili_auth.exchange_tv_token`，`bili_auth.py#L142`）。三步与 biliup 内建的
   「网页Cookie登录」逐字一致（`credential.rs::login_by_web_cookies`）：
   申请 TV 二维码 → 用**用户自己的 cookies** 替他点「确认」→ 轮询换回 `token_info`。
   这是唯一能自动化拿到 `access_token` 的路（账号密码/短信都要求交互终端）。
   - Cookie 头只带 `SESSDATA` 与 `bili_jct`。扫码时从回调 URL 上顺手捡来的 `gourl`/`sid`/`first_domain`
     **并不是 cookie**（那是查询参数的名字），一起发过去只是给对方一个「这个客户端很奇怪」的信号。
   - `sign` 必须与 biliup 逐字节一致（键按字典序 + `md5(urlencoded + appsec)`），
     因为**签名不合法与凭据不合法在响应里长得一样**，都是 `-400 请求错误`。
   - `86039`（还没确认）重试 5 次；其它码立刻报出来 —— 那是确定性拒绝，重试只是白等。

2. **写文件时带上令牌与 `platform`**（`build_login_info`，`bili_auth.py#L282`）。`platform="BiliTV"`
   不能省：biliup 靠它挑续期用的 appkey，不写的话令牌到期后 `renew` 会原样返回、**静默不续期**。

3. **登录与导入都补齐令牌**（`bili_manager.save_login` `#L378`、`ensure_upload_token` `#L397`；
   路由 `routers/bilibili.py#L141`）。导入路径已经在用同一个交换 —— 这也就是 biliup 的
   「网页Cookie登录1」。**已经有令牌就不重复换**（换一次会作废上一次的，等于把同时在用的
   另一个客户端顶掉线）；**读不出内容就不动那个文件**（一张我们读不懂的文件更不该被清空）。

4. **原始输出进日志**（`bili_upload.py#L350` 附近 + `bili_manager._log_biliup_line` `#L161`）。
   失败时把 `log_tail` 逐行按 `WARNING` 打一遍，前缀固定为 `biliup 输出 | `——
   这条路径不依赖日志级别，用户照失败建议去 grep 就一定搜得到。成功后不再重复刷。
   逐行落盘前过 `sanitize_log_message`（工具会打印什么不在我们控制之内）。

5. **失败建议改成指一个真的存在的地方**（`bili_policy.UNKNOWN_FAILURE_ADVICE` `#L224`）：
   从「搜索 BILI」改成「搜索 `biliup 输出`」。

6. **分类出这个错误码**（`FAILURE_PATTERNS`）：`请求错误`/`"code":-400` →
   「B站 退回了这次投稿请求（错误码 -400）」，下一步指向凭据体检；`61000 登录状态异常` 单独一条，
   且**放在泛用的「未登录」之前**（后者的 needle 里有 `登录`，慢一步就会被吞掉、丢掉更准确的措辞）。

7. **体检不再放它静默通过**（`bili_manager._health_sync` 的 blockers，`_require_upload_token` `#L698`）。
   缺令牌时 `ready=False` + 一条红字；提交前也会被 `409 missing_upload_token` 拦住。
   过去这一档是全绿的 —— 面板三项全绿、一投稿必失败，用户只能去查一个查不出来的错。

**为什么不做别的**：不改去自己实现投稿协议（上游上传分片逻辑复杂且会漂移）；不调 `biliup login`
（它要求交互终端，沙箱与无人值守场景直接拒绝，本模块开头就是这么写的）。

## 效果

| | 修复前 | 修复后 |
|---|---|---|
| `biliup -u data/bili_cookies.json list` | `Error: {"code":-400,…"请求错误"…}` | `INFO … 验证cookie` / `无需更新cookie` / `通过cookie登录` / `user: ant-hengxin` |
| 登录文件 | `has_access_token=False`（无 `platform`） | `has_access_token=True`、`has_refresh_token=True`、`platform=BiliTV` |
| 令牌时长 | — | `expires_in=15552000`（180 天） |
| 一次投稿的耗时 | 1.4 s（在第一个 API 调用就退出） | —— 不再有这类「秒失败」 |
| 失败时 app.log | 只有我们那一行，搜不到原文 | 搜 `biliup 输出` 得到逐行原文 |
| 缺令牌时的界面 | 三项全绿、静默 | `ready=false` + 红字 + 提交前 409 |

**平台侧确认**：账号 64 条稿件里**没有**这条视频（`biliup list --max-pages 6 | grep -i 'C++\|Weekly'` 无匹配）。
那次投稿一个字节都没发出去，不需要去创作中心清理。

## 验证

新增 27 个用例（后端总计 774 → **804 passed**）：

| 文件 | 用例 | 钉住什么 |
|---|---|---|
| `test_bili_auth.py` | `test_app_sign_matches_the_frozen_biliup_value` | 签名字面值（签名错会被伪装成「凭据坏了」） |
| | `test_app_sign_sorts_keys_alphabetically` | 键序 |
| | `test_exchange_tv_token_returns_a_real_token` | 三步走通、字段齐 |
| | `test_exchange_tv_token_confirms_with_the_users_own_cookies` | confirm 用 `csrf` + `Cookie` 头 |
| | `test_exchange_tv_token_drops_the_junk_picked_up_from_the_callback_url` | 不把 `gourl`/`sid` 当 cookie 发 |
| | `test_exchange_tv_token_signs_the_appkey_rounds_but_not_the_confirmation` | 两组参数风格相反 |
| | `test_exchange_tv_token_retries_while_the_platform_says_not_confirmed` | `86039` 重试 |
| | `test_exchange_tv_token_gives_up_after_the_attempt_limit` | 重试上限 |
| | `test_exchange_tv_token_fails_fast_on_an_unexpected_poll_code` | 其它码不重试 |
| | `test_exchange_tv_token_reports_a_refused_confirmation` / `…missing_auth_code` | 可读报错 |
| | `test_exchange_tv_token_needs_both_web_cookies`（3 参数） | 缺项早失败 |
| | `test_exchange_tv_token_reports_a_token_less_response` | 平台没给令牌 |
| | `test_build_login_info_carries_the_upload_token` | 令牌 + `platform` 必须写进文件 |
| | `test_build_login_info_without_a_token_produces_a_file_biliup_will_reject` | 显式钉住「不能投稿的文件」长什么样 |
| | `test_read_login_token_only_reports_presence` | 摘要不泄值（FR-B12） |
| `test_bili_manager.py` | `test_save_login_writes_a_jar_that_can_be_read_back` | 登录完成判据 = 拿到令牌 |
| | `test_save_login_keeps_the_cookies_when_the_token_exchange_fails` | 换不到也留住 cookies + 返回 warning |
| | `test_ensure_upload_token_does_not_re_exchange_when_one_is_present` | 不重复换（避免顶掉别的客户端） |
| | `test_ensure_upload_token_fills_in_a_cookie_only_file` | 导入路径补令牌 |
| | `test_ensure_upload_token_leaves_an_unreadable_file_alone` | 不覆盖读不懂的文件 |
| | `test_submit_single_refuses_without_an_upload_token` | 409 `missing_upload_token` |
| | `test_health_reports_a_missing_upload_token_as_a_blocker` | 体检不再静默通过 |
| `test_bili_policy.py` | `test_classify_failure_recognizes_the_raw_error_that_started_this` | **把真实那一行原文当夹具** |
| | `test_classify_failure_recognizes_an_expired_app_token` | `61000` 优先于泛用的「未登录」 |
| | `test_unknown_failure_advice_points_at_something_that_exists` | 建议必须指向真的存在的东西 |
| `test_bili_upload.py` | `test_run_single_writes_the_raw_output_into_the_log_on_failure` | 失败时原文进日志（`caplog`） |
| | `test_run_single_never_logs_the_raw_output_of_a_successful_upload` | 成功时不刷屏 |
| `test_log_safety.py` | `test_sanitize_log_message_redacts_json_shaped_secrets` | 附带修复：JSON 形态的 `access_token` 也要被吃（修复前实测放行） |
| | `test_sanitize_log_message_keeps_non_secret_json_fields_intact` | 只吃凭据键，`"code":-400` / `"mid"` / `"cookie_name"` 原样保留 |

夹具同步：`bili_fakes.FakeBiliTransport` 认全了 TV 三个端点（默认一次到位，`tv_poll_codes`
可构造「前几拍还没确认」）；`test_bili_manager` / `test_api_bilibili` 的 `write_login` 默认写**完整**
登录态（带令牌），「没有令牌」改成一条要显式构造的分支。

回归：后端 **804 passed**（按文件逐块跑，无 `FAILED`、无 `ERROR`；块内 31 个文件全绿）。
前端 **12 files / 99 passed**、`tsc --noEmit` 退出码 0（本轮未动前端，属未回归确认）。

**真实端到端验收**：用产品自己的代码路径（`exchange_tv_token` → `write_cookie_file`）
修好 `data/bili_cookies.json`，`biliup list` 随即从 `-400` 变为正常登录（见上表第一行）。
证据脚本 `tmp_acceptance/acceptance_token_fix.py`（`tmp_acceptance/` 不入库）。

## 风险与回滚

- **登录文件里多了一份 180 天的 OAuth 令牌。** 文件本来就在 `.gitignore` 里
  （`data/bili_cookies.json`），且本模块的硬约束是「对外只暴露有没有、缺哪项」，令牌值不进日志、
  不进 API 响应、不进日志尾部（`read_login_token` 只回布尔与长度）。
  —— 这句话在本次**实测验证过一遍**：正因为要证明它，才发现清洗器漏掉了 JSON 形态（见上「附带修复」）。
- **交换会新建一个 TV 端会话。** 影响与「用 B站 App 扫一次 TV 登录码」相同；
  不会动 web cookies，所以读接口（对账、合集、评论）行为不变。
- **`platform=BiliTV` 写错会让令牌到期后静默不续。** 已由
  `test_build_login_info_carries_the_upload_token` 钉住；`read_login_token` 也会把 `platform` 读出来。
- **`ensure_upload_token` 会改写导入的文件为 biliup 的 `LoginInfo` 结构。** 这是必要的规范化
  （biliup 只认这个结构，Netscape `cookies.txt` 它连解析都过不去）。读不出内容时**不动**那个文件。
- **回滚**：`git revert <本次 commit>`。回滚后界面恢复「三项全绿但投稿必失败」的旧行为，
  且 `data/bili_cookies.json` 里已经带上的令牌不影响旧代码读取（旧代码只读 `cookie_info`）。
- ⚠️ **未覆盖**：`renew_tokens`（令牌到期后的自动续期）没有实测过 —— 它要等 180 天或构造一个
  快过期的令牌。目前只由「写对 `platform`」间接保证。已登记进
  [已知未处理](../bug-fix/README.md#已知但未处理的问题下一轮候选)。

## 关联

- **同一条失败链的三段**（本次一并处理）：令牌缺失（根因）→ 失败分类认不出 `-400` → 建议指向空日志。
  只修其中一段都不够：只修令牌，用户下次遇到别的未知失败仍会被指向空日志；只修日志，
  投稿依旧一次都发不出去。
- **与 007（`5e556f9`）同型**：「应用当时根本不写日志，连查都没法查」。
  007 修的是下载链路的日志缺失，这次是投稿链路的 — 同一类盲区在两个子系统里各出现一次。
- **纠正 F6**：`ai/feature/bilibili-upload/01-requirements.md` 里「`--submit web` 是必须的、
  cookie 登录拿不到 access_token」这条结论**不成立**，已在 01/02/03 与技术文档中改写。
- **附带修复动的是全仓库共用的清洗器**：`log_safety.py` 也被下载链路使用（`job_manager` 记失败原文时），
  所以 JSON 形态那一格补上之后，收益不限于 B站 —— 任何把 JSON 正文交给日志的地方都跟着变安全。
- 「删任务不连带删 B站 上传记录」是**另一条**独立缺陷，见索引第 13 条，本次未处理。
