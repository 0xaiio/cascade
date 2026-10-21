# 018 · 长文件名被报成「标题过长」，又被折成「登录已失效」

## 问题

投稿 `C++ Weekly - Ep 105 - Learning "Modern" C++ - 5: Looping and Algorithms` 连续失败 7 次，
界面上给的是 **「B站 登录已失效 / 去设置里重新扫码登录」**。按提示重新扫码、再点重试，
仍然一模一样 —— 也就是说这句结论**指向了一个根本不存在的原因**。

## 原因

两处缺陷叠在一起，缺任何一处都不会出现这个症状。

### 1. 真正的失败：视频文件名超了 80 个字符

`biliup` 的原始输出（库里 `bili_upload.log_tail` 逐字抄下来）：

```
 INFO biliup::uploader::credential: 验证cookie
 INFO biliup::uploader::credential: 无需更新cookie
 INFO biliup::uploader::credential: 通过cookie登录
 INFO biliup::uploader: user: ant-hengxin
 INFO biliup::uploader: Upload completed: ... => cost 12.26s, 1.16 MB/s.
 WARN biliup::uploader::bilibili: 客户端接口已失效, 将使用APP接口
Error: ResponseData { code: 21104, data: None, message: "第(1)个视频的标题过长,已经超过80个字符", ttl: Some(1) }
```

标题只有 **71** 个字符，离 80 还差 9 个。真正超限的是**文件名**：下载器在文件名后加了
` [A0-x-Djey-Q]`（YouTube ID），`stem` 变成 **85**。B站 的 App 接口把文件名也按「标题」的
80 字符上限校验，报错文案却只说标题。

**实测证据（本机库里全部 10 条投稿记录，逐一比对标题与文件名长度）**：

| 稿件 | 标题长度 | 文件名 stem | 结果 |
| --- | --- | --- | --- |
| Ep 28 | 54 | **80** | 通过（正好卡在上限上） |
| Ep 101 | 58 | 72 | 通过 |
| Ep 102 | 60 | 74 | 通过 |
| Ep 21 | 60 | 74 | 通过 |
| **Ep 105** | **71** | **85** | **21104** |

标题长度与成败无关，`stem ≤ 80` 与成败完全吻合。

### 2. 误导性的结论：登录分类的 needle 太宽

`bili_policy.FAILURE_PATTERNS` 里那一条是：

```python
(("not logged in", "未登录", "登录", "cookie", "登录信息已过期", "账号未登录"), NOT_LOGGED_IN)
```

`登录` 与 `cookie` 是**裸词**，而 biliup 的**成功**日志里就写着「验证cookie」「无需更新cookie」
「通过cookie登录」。于是任何一次失败（只要输出里有这几行）都会命中这一条 —— 21104 也不例外。
另外 21104 本身没有专门的 pattern，于是最终落在「登录已失效」。

判据：**needle 必须是失败语句的一部分，不能是成功日志里也会出现的名词。**

## 修复方案

1. `bili_policy` 新增 `21104 / 标题过长` 这一条，排在登录那一条之前，文案把真相说清
   （「它报的是标题过长，但超的是文件名」），下一步给「点重试（会自动换短文件名）」。
2. `bili_policy` 收紧登录 needle：只留 `not logged in` / `账号未登录` / `未登录` /
   `登录信息已过期` / `"code":-101`。删掉 `登录` 与 `cookie` 两个裸词。
3. `bili_upload._short_name_video()`：文件名 stem > `VIDEO_NAME_MAX`(80) 时，
   在同一目录建一个**短名硬链接**（`cascade-<8 hex>.mp4`）交给 biliup，`finally` 里删掉。
   选硬链接而不是复制：同目录 ⇒ 同卷 ⇒ 零字节开销；原文件名一个字不动。
   硬链接失败（FAT/exFAT/跨卷/权限）时**退回原名**并记日志 —— 宁可让平台用 21104 拒掉
   （那条错误的下一步已经写明「把源文件改名短一点」），也不偷偷复制一个几 GB 的文件。

## 效果

修复后对同一条记录（`b6f1193b…`）执行一次真实重试：

```
action=reupload  message=已重新排队上传
→ uploading 0.2 → uploading 0.95 → reviewing
FINAL: reviewing | error=None | bvid=BV1iunv61Eu4
```

账号稿件列表里确实出现了这条稿件（标题 71 字符，`state=-30` 已提交待审核）。
`downloads/` 下没有残留 `cascade-*` 文件。

## 验证

| 用例 | 钉住什么 |
| --- | --- |
| `test_the_title_error_is_not_mistaken_for_a_login_failure` | 用**真实日志**当夹具：结论里必须出现 21104 或「文件名」，且不能等于登录失效 |
| `test_a_benign_login_line_alone_is_not_a_failure` | 只有成功日志时不得判成登录失效 |
| `test_real_auth_failures_are_still_recognized`（4 例） | 收紧 needle 后，真登录失效仍然认得出：`not logged in` / `-101` / `登录信息已过期` / `账号未登录` |
| `test_a_long_file_name_is_uploaded_through_a_short_hard_link` | stem=85 ⇒ 传给 biliup 的是 `cascade-*.mp4`、`≤ 80`、那一刻内容大小与源文件一致、结束后链接删除、原文件仍在 |
| `test_a_normal_file_name_is_passed_through_untouched` | stem 正常时不造链接（用户目录里不多一个来路不明的文件） |
| `test_a_failed_hard_link_falls_back_to_the_original_name` | 硬链接失败 ⇒ 退回原名 + 一条 warning，不复制、不中断 |

回归：后端全量 `1081 passed`（本轮 +9）；前端不受影响。

## 风险与回滚

- **临时硬链接**：只在 stem > 80 时出现，命名带随机后缀，正常路径下删除；删除失败只留下一个
  指向同一份数据的名字（不多占空间）。若用户目录在 exFAT/网络盘上，`os.link` 会失败并退回原名，
  此时该稿件仍可能被 21104 拒 —— 错误文案会告诉用户去改源文件名。
- **分类收窄的副作用**：若 biliup 将来把登录失败改成别的措辞（不含上述 four 个短语），会落到
  「没能识别出具体原因」。这是**有意的**：认不出来时如实说不知道，比错误地指责登录好。
  届时加 needle 即可（`FAILURE_PATTERNS` 是单点）。
- 回滚：`git revert <commit>` 即可，无数据迁移。

## 未覆盖 / 如实说明

- **没有在别的文件系统上验过硬链接**（本机是 NTFS）。exFAT/网络盘的退回路径只有单测覆盖。
- 平台侧 80 字符上限是**实测归纳**（stem 80 通过、85 失败），B站 没有公开文档给出这条规则，
  也没有测 81..84 这几个值 —— 取 `TITLE_MAX`(80) 作为界更保守。
- 这次只验证了这一条稿件；「文件名超限」在**合集**（多条）场景下的表现没有实测
  （合集走同一个 `run_single`/`upload_one`，逻辑上同一条路径）。

## 关联

- [011](011-biliup-refuses-a-token-less-cookie-file.md)：同样是「错误被折成另一个原因」——
  那次是令牌空串被报成泛用的 `-400`。
- [016](016-subtitle-lost-while-the-video-is-in-review.md)：同一个教训的另一面 ——
  **一个值不能同时表达两件事**；这里是「一个 needle 不能同时匹配成功日志与失败语句」。
- [ai/ui/004](../ui/004-the-recheck-button-looks-dead.md)：同一轮里修的界面缺陷（重新体检按钮无反馈）。
