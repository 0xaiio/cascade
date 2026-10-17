# 013 · 界面「新建合集」之后的归集把合集 id 当成了分区 id：平台每次都回 `-404 啥都木有`，点「重试归集」也不会自愈

- **状态**：已修复
- **发现方式**：给宣传视频投稿时手动打平台接口（见 [promotion/README.md](../../promotion/README.md#发布记录)），
  新建合集后归集返回 `-404`，换成分区 id 立即成功 —— 于是回头读代码，两条路（新建 / 加入已有）都是错的
- **提交**：本次提交（hash 见索引，按目录约定下次触碰时回填）

## 问题

用户在投稿弹窗里选「新建合集」并填了合集名，投稿本身成功、视频也进了账号，但**合集里什么都没有**：

```text
合集建好了，但视频没能加进去
点「重试归集」再试 —— 合集已经存在，重试只会补加视频，不会重复建合集
```

照做点「重试归集」（`POST /api/bilibili/seasons/{id}/retry`），得到的是同一句。多次之后仍是这样，
而界面上没有任何地方能看出「到底哪里不对」——**它既不像网络问题，也不像权限问题**。

平台侧的原始返回是：

```text
BiliApiError: 归集稿件失败（code=-404 message=啥都木有）
```

同一个接口、同一条稿件，把 id 换一下就能过：

```text
== 1) 拿合集 id 当 sectionId（旧行为） ==
  BiliApiError code=-404 message=啥都木有
== 2) 换成分区 id ==
  合集 9343273 -> {'id': 10455347, 'title': '正片'}
  （2026-10-16 实测：用 10455347 归集成功，该分区 epCount 0 → 1）
```

## 原因

**两个不同的实体共用了一个名字。** 归集接口 `POST .../season/**section**/episodes/add` 的
`sectionId` 要的是**合集下的分区（section）id**，而 `season/add`（建合集）返回的是**合集（season）id**。
它们在平台上是两套独立编号，实测 `9343273`（合集）对应 `10455347`（其中「正片」分区）。

修复前，这两个值在代码里是同一个变量 `season_id`，从头到尾没人换过号：

`git show HEAD:backend/app/bili_api.py`（第 196-217 行，节选）：

```python
def add_episodes(transport, jar, *, season_id: int, episodes) -> None:
    """把稿件加进合集。"""
    body = {
        "sectionId": int(season_id),      # ← 装进去的是**合集** id
        "episodes": [...],
    }
```

`git show HEAD:backend/app/bili_upload.py`（第 395-442 行，节选）：

```python
    season_id = existing_season_id
    if not season_id:
        season_id = create_season(...)     # ← 这里返回的是**合集** id
    ...
    add_episodes(context.transport, context.jar, season_id=int(season_id), episodes=episodes)
```

**为什么重试不自愈。** `retry_season()` → `_group_work()` 读的是上一次落库的 `season.season_id`
（`bili_manager.py` 的 `existing_season_id = season.season_id`），也就是**同一个合集 id**，
于是每次重试都把同一个错值再发一遍。这不是「偶发的平台抖动」，是一条**每次必然失败**的路。

**为什么本地一直没发现。** 三件事叠在一起：

1. `bili_fakes.FakeBiliTransport` 对 `episodes/add` 一律回 `{"code": 0}`，**不校验 `sectionId`**；
2. 两个值都是 `int` —— 类型检查、`mypy`、`tsc` 都不可能拦；
3. 端点路径里写着 `section`，而参数名叫 `season_id` —— 名字本身在误导读代码的人。

于是既有用例（含「重试不重复建合集」「加入已有合集」那几条）全部通过，而这条路径在真账号上
100% 失败。与 [011](011-biliup-refuses-a-token-less-cookie-file.md) 同一形状：
**本地全绿、真账号必失败**。

**影响面（两条路都中）。**

| 归集方式 | 修复前 |
|---|---|
| `group_mode=new`（新建合集） | 建合集成功 → 归集必失败 → 重试仍失败 |
| `group_mode=existing`（加入已有合集） | 用户选的是**合集**，透传下去的还是合集 id → 同样 -404 |

## 修复方案

**（一）新增 `bili_api.find_season_section()`：把合集 id 真的换成它下面某个分区的 id。**

来源是 `seasons` 列表里每一行的 `sections.sections[]`（`_read_season_page` 现在把这层原始行读出来；
`list_seasons` 只取内层 `season`，所以它拿不到分区）。挑法：

1. 优先 `type == 1` —— 平台自动建的那个默认分区，实测账号下就是「正片」；
2. 一个合集被加了自定义分区之后**可能没有默认分区**（实测 `2026-math4cs` 有 7 个 `type=0` 的分区、
   一个 `type=1` 都没有），这时按 `order` 取最靠前的那个 —— 宁可放进第一个分区，也不要整条归集失败；
3. 一个分区都读不出来 → 返回 `None`，**不猜 id**。

同时带退避重试（3 次 / 2 s）：新建的合集不是立刻出现在列表里，读一次没有就判死会让
「刚建好就归集」稳定失败。翻页上限 10 页，且超过一页时会继续翻 —— 账号下合集多于 30 个时
只看第一页会把「合集明明在」判成「读不到」。

**（二）`add_episodes()` 的参数改名 `season_id` → `section_id`。**

两个 id 都是 `int`，只有名字能表达区别。改完之后，「把合集 id 传给归集接口」在调用点上是
**肉眼可见的错**（`section_id=season_id` 会让人停下来）。

**（三）换不到分区时**落一条说得清的结论，并且**一个归集请求都不发**：

```python
SEASON_SECTION_UNRESOLVED = (
    "合集建好了，但没读到它下面的分区，视频没能加进去",
    "点「重试归集」再试 —— 合集已经存在，重试只会补加视频；若反复失败，去 B站 创作中心确认这个合集下有没有分区",
)
```

`SeasonOutcome.season_id` 仍然带回合集 id，于是重试走「只补加」那条路、**每次重试都会重新换号**
（换号这一步放在建合集**之外**，这是「重试能自愈」的前提）。

**为什么不做别的**：没有在 `add_episodes` 里偷偷把 `seasonId` 换成「先查一次再发」——
那会让一个薄薄的接口封装悄悄发起第二个请求，调用方与测试都看不见；也没有把「换号失败」
降级成「用合集 id 试一下」—— 那正是这条缺陷的形状，试出来的 `-404` 对用户毫无信息量。

## 效果

| | 修复前 | 修复后 |
|---|---|---|
| 提交给 `episodes/add` 的 `sectionId`（合集 `9343273`） | `9343273`（= 合集 id） | `10455347`（= 该合集「正片」分区） |
| 平台返回 | `code=-404 message=啥都木有` | `code=0`（2026-10-16 实测 `epCount` 0→1） |
| 点「重试归集」 | 同一个错值再发一遍，**永不自愈** | 重新换号，能自愈；换不到则落「读不到分区」并保留合集 id |
| 「加入已有合集」 | 同样拿合集 id 顶（用户选的是合集） | 同样换成该合集的分区 id |
| 每次归集的平台请求数 | 建合集 1 + 归集 1 | 建合集 1 + **读合集列表 1** + 归集 1 |
| 读不到分区时的行为 | 拿合集 id 试 → `-404` → 落「视频没能加进去」 | **不发归集请求**，落「没读到它下面的分区」并说清下一步 |

## 验证

**离线**（`backend/tests`，`pytest -q`：**913 passed**，本轮 +14；逐文件数见
[04-testing.md §1](../feature/bilibili-upload/04-testing.md)）：

| 文件 | 用例 | 钉住什么 |
|---|---|---|
| `test_bili_api.py` | `test_find_season_section_returns_the_section_id_not_the_season_id` | **本条的核心断言**：换出来的必须是分区 id，且**不等于**合集 id（替身用 `season_id + 1000`，任何「看起来差不多」的巧合都会露馅） |
| | `…_prefers_the_platform_default_section` | `type=1` 优先，即使它的 `order` 更靠后 |
| | `…_falls_back_to_the_first_custom_section` | 没有默认分区时按 `order` 取第一个（实测存在这种合集） |
| | `…_returns_none_when_the_season_has_no_sections` | 读不到分区时返回 `None`，**不退化成「用合集 id 试试」** |
| | `…_retries_while_a_new_season_is_missing_from_the_list` | 新合集的列表延迟：3 次读取、2 次等待（`RecordingSleeper`），不睡最后一次之后 |
| | `…_pages_past_the_first_page` | 35 个合集、目标在第 2 页：请求数恰好 2 |
| | `…_skips_malformed_section_rows` / `…_returns_none_on_a_business_error` / `…_on_a_network_error` / `…_for_a_bogus_season_id_without_asking` | 脏分区行、业务码、断网、编号非数字四种「读不到」 |
| `test_bili_upload.py` | `test_group_into_season_sends_the_section_id_not_the_season_id` | **界面路径的入口断言**：`json_body["sectionId"] == section_id_for(990011)` 且 `!= 990011` |
| | `test_group_into_season_stops_when_the_section_cannot_be_read` | 读不到分区 → `failed` + 保留 `season_id`（重试才能「只补加」）+ `episodes/add` **调用 0 次** |
| | `test_group_into_season_reuses_an_existing_season_and_never_creates_a_second`（改） | 「加入已有合集」这条路也要换号 |
| | `test_run_collection_adds_the_season_after_the_last_upload`（改） | 归集仍是最后一步；断言改成比**写请求**顺序 —— 建合集与归集之间现在夹着一次读，原来「最后两条 URL」的写法会被这次读打断 |
| `test_bili_manager.py` | `test_join_an_existing_collection_never_creates_a_new_one`、`test_single_submit_joins_an_existing_collection`、`test_switching_the_target_collection_overrides_the_previous_one`（改） | 三条的夹具补上「被选中的合集在列表里」；切换目标那条的 `sectionId` 断言改为 `[section_id_for(111), section_id_for(222)]` |

**真实平台**（`tmp_acceptance/verify_season_section.py`，只读 + 一次幂等性试探）：

```text
== 1) 拿合集 id 当 sectionId（旧行为） ==
  BiliApiError code=-404 message=啥都木有
== 2) find_season_section ==
  合集 9343273 -> {'id': 10455347, 'title': '正片'}
  合集 7582381 -> {'id': 8415071, 'title': '计算机数学概论'}      ← 7 个 type=0 分区的合集，走回退
== 3) 用分区 id 重复加同一条（幂等性试探） ==
  BiliApiError code=20080 message=当前稿件已存在在合集中
```

第 3 条是**顺手测出来的、本轮没修**的东西，见下节。

## 风险与回滚

- **每次归集多一次 GET**（读合集列表）。它只是读，失败时按「读不到分区」处理（不发归集请求），
  不会把一次能成的归集变成失败 —— 除非真的读不到，而那种情况本来也发不出正确的 id。
- **新建合集可能要多等最多 4 秒**（3 次尝试、每次间隔 2 s）。这发生在后台线程里，界面不受影响。
- **多分区合集里进哪个分区是猜的**（按 `order` 取第一个）。界面上没有「选分区」的入口，
  所以用户可能发现视频进了「第一讲」而不是「正片」—— 本轮选了「宁可进错一个分区也不要整条失败」。
  真正的解法是让用户选分区，属产品改动，没做。
- **回滚**：`git revert <本次 commit>`。回滚后 `add_episodes` 的参数名回到 `season_id`，
  `find_season_section` 消失，行为退回「必 404」。

## 关联

- **与 [011](011-biliup-refuses-a-token-less-cookie-file.md) 同型**：本地用例全绿、真账号必失败。
  011 是「令牌为空串时 biliup 无条件先去校验」，本条是「两个 id 共用了一个变量名」。
  共同教训：**接缝处的替身不校验参数语义时，只有真打一次平台才能发现**。
- **与 [010](010-unselectable-probe-raise-skips-the-fallback.md) 的对比**：010 是分支写对了、
  触发条件写错了；本条是**调用本身就对**（路径、参数名、csrf 位置全对），只有**值的来源**错了。
  这类错误没有「死代码」可查，只能靠「平台上这个值是什么意思」去核。
- **[012](012-preflight-contradicts-itself-and-the-server.md) 末尾提到的「013 候选」**
  （`App.tsx` 里三条 B站 请求没有超时）**并未占用编号**，也仍未处理；
  它若被处理应取 **014**。本条的 013 是实际修掉的这一条。

## 未覆盖 / 如实说明

1. **「新建合集后立即回读」的平台延迟没有实测。** 我验证的是**重试逻辑**（替身构造「前 N 次读不到」），
   没有在真账号上建一个一次性合集来量延迟 —— 那会在用户的创作中心里留下一个删不掉的空合集。
   所以「3 次 / 2 s 够不够」是**按代码路径判定的，不是按实测延迟判定的**。
2. **重复添加返回 `20080 当前稿件已存在在合集中`，本轮仍记为失败。** 实测（上面第 3 段）。
   这意味着：一次**实际已成功但响应丢失**的归集，重试会落成「视频没能加进去」，而视频其实在里面。
   没有把它判成成功，是因为**混合批的语义没验证**——如果一批里既有已存在的、又有新的，
   平台是整批拒绝还是逐条处理？前者会让「判成功」变成谎报。已记入
   [bug-fix README 的「已知但未处理」](README.md#已知但未处理的问题下一轮候选) 第 16 条。
3. **`SeasonOutcome.note` 不落库**（`BiliSeason` 没有这一列，`_group_work` 也没写它），
   所以「实际进了哪个分区」无法告诉用户；本轮只写进了日志（`bili 归集：合集 X → 分区 Y「正片」`）。
   多分区合集的用户因此只能去创作中心自己看。改 note 落地属 schema 迁移，没顺手做。
4. **前端没有「选分区」入口**（`GET /api/bilibili/seasons` 的 `BiliSeasonOption` 只有 `season_id`）。
   本条修的是「能不能归集」，不是「归到哪个分区」。
5. **`list_seasons` 仍然只读第一页**（`/api/bilibili/seasons` 用它给用户列候选）。
   账号下合集多于 30 个时，第 31 个之后的合集**在界面上选不到**。本条只给
   `find_season_section` 加了翻页，没动它 —— 那是另一个可达的问题，留待单独立项。
