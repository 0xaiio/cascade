import { describe, expect, test } from "vitest";
import { biliStatusPayload } from "./test/appFixtures";
import {
  COPYRIGHT_REPRINT,
  COPYRIGHT_SELF,
  DEFAULT_COPYRIGHT,
  GROUP_MODE_EXISTING,
  GROUP_MODE_NEW,
  GROUP_MODE_NONE,
  charBudget,
  collectionTitleBudget,
  countTags,
  findChosenSeason,
  groupHint,
  groupNotice,
  localPreflight,
  longestTag,
  noticeTarget,
  renderItemTitle,
  renderTemplate
} from "./biliForm";
import type { TitleSourceItem } from "./biliForm";
import type { BiliGroupMode, BiliLimits, BiliMetadata, BiliSeasonOption } from "./types";

/**
 * 投稿表单的词汇表与预检。
 *
 * 这一层从 `BiliUploadDialog` 里抽出来，正是为了能在这里**不渲染弹窗**就把判据钉死：
 * 弹窗测试只能覆盖「点过的那几条路径」，而预检的价值恰好在「用户想不到的那几条」上
 * （单个标签超限、转载没填来源、选中合集从列表里消失）。
 *
 * 上限一律取自夹具里那份 `limits`（与服务端同源）—— **不写死数字**：测试若自己写死一个
 * 80，那条断言的真正含义就变成了「80 等于 80」，改上限时它照样绿。
 */
const LIMITS: BiliLimits = biliStatusPayload.limits;

/** 三种归集方式。**必须标注类型**：数组字面量会把 `"new"` 这类字面量放宽成 `string`。 */
const GROUP_MODES: BiliGroupMode[] = [GROUP_MODE_NEW, GROUP_MODE_EXISTING, GROUP_MODE_NONE];

function metadata(overrides: Partial<BiliMetadata> = {}): BiliMetadata {
  return { title: "标题", desc: "", tags: "", tid: "122", copyright: COPYRIGHT_SELF, source: "", ...overrides };
}

function preflight(overrides: Partial<Parameters<typeof localPreflight>[0]> = {}): string[] {
  return localPreflight({
    metadata: metadata(),
    comment: "",
    limits: LIMITS,
    isCollection: false,
    // 合集场景下标题判据量的是**渲染后的每一条**，所以这两项是判据的一部分 ——
    // 单视频传空数组即可（它量的是标题本身，不看 items）。
    items: [],
    jobTitle: "任务",
    groupMode: GROUP_MODE_NONE,
    seasonId: null,
    seasonsLoaded: false,
    chosenSeason: undefined,
    ...overrides
  });
}

/**
 * 一组条目：第 2 条的原始标题正好压在上限上，第 1 条很短。
 *
 * 用它来验「报的是渲染后最糟的那条」—— 若判据错量成模板本身，这两条的差别就消失了。
 */
const ITEMS: readonly TitleSourceItem[] = [
  { index: 1, source_title: "短", source_url: "https://youtu.be/1" },
  { index: 2, source_title: "字".repeat(LIMITS.title), source_url: "https://youtu.be/2" }
];

/** 合集场景的预检。`items` 是判据的一部分，所以显式给。 */
function collectionPreflight(
  title: string,
  overrides: Partial<Parameters<typeof localPreflight>[0]> = {}
): string[] {
  return preflight({ isCollection: true, metadata: metadata({ title }), items: ITEMS, ...overrides });
}

function seasonOption(seasonId: number, title = `合集 ${seasonId}`): BiliSeasonOption {
  return { season_id: seasonId, title, desc: "", cover_url: "", total: 0 };
}

/* ================================================================ 标签计数 */
describe("标签计数", () => {
  test("空项不算一个标签（与服务端提交时的口径一致）", () => {
    expect(countTags("")).toBe(0);
    expect(countTags(",")).toBe(0);
    expect(countTags(" , ")).toBe(0);
    expect(countTags("a,,b")).toBe(2);
    expect(countTags(" a , b ")).toBe(2);
  });

  test("去空白后仍算一个标签", () => {
    expect(countTags("a,b,c")).toBe(3);
    expect(countTags(" 编程 , 数据库 ")).toBe(2);
  });

  test("最长的标签已去空白；同长时保留靠前的那个", () => {
    expect(longestTag("a,bb,ccc")).toBe("ccc");
    expect(longestTag(" a , bbb ")).toBe("bbb");
    expect(longestTag("")).toBe("");
    expect(longestTag("bb,aa")).toBe("bb");
  });
});

/* ================================================================ 选中合集 */
describe("选中合集", () => {
  const options = [seasonOption(1), seasonOption(2)];

  test("按 season_id 找到那一个", () => {
    expect(findChosenSeason(options, 2)?.season_id).toBe(2);
  });

  test("没选（null）或已不在列表里，都返回 undefined", () => {
    expect(findChosenSeason(options, null)).toBeUndefined();
    expect(findChosenSeason(options, 99)).toBeUndefined();
    expect(findChosenSeason([], 1)).toBeUndefined();
  });
});

/* ================================================================ 说明与回执 */
describe("归集方式下面的说明", () => {
  test("新建合集时说得出合集名，标题为空时退回任务名", () => {
    const withTitle = groupHint({ groupMode: GROUP_MODE_NEW, seasonTitle: " 我的合集 ", jobTitle: "任务", isCollection: false });
    expect(withTitle).toContain("「我的合集」");
    const blank = groupHint({ groupMode: GROUP_MODE_NEW, seasonTitle: "   ", jobTitle: "任务", isCollection: false });
    expect(blank).toContain("「任务」");
  });

  test("主语按场景分句：单视频说「这条视频」，合集说「这些视频」", () => {
    for (const mode of GROUP_MODES) {
      expect(groupHint({ groupMode: mode, seasonTitle: "s", jobTitle: "j", isCollection: false })).toContain("这条视频");
      expect(groupHint({ groupMode: mode, seasonTitle: "s", jobTitle: "j", isCollection: true })).toContain("这些视频");
    }
  });

  test("三种归集方式各说各的，不共用一句话", () => {
    const args = { seasonTitle: "s", jobTitle: "j", isCollection: true };
    const hints = GROUP_MODES.map((mode) => groupHint({ groupMode: mode, ...args }));
    expect(new Set(hints).size).toBe(3);
    expect(hints[1]).toContain("不新建");
    expect(hints[2]).toContain("不建合集");
  });
});

describe("提交回执", () => {
  test("对象是「哪个任务的几个视频」还是「哪一个稿件」", () => {
    expect(noticeTarget({ isCollection: true, jobTitle: "批次", metadataTitle: "标题", itemCount: 3 })).toBe(
      "《批次》的 3 个视频"
    );
    expect(noticeTarget({ isCollection: false, jobTitle: "批次", metadataTitle: "标题", itemCount: 3 })).toBe("《标题》");
  });

  test("归集去向必须出现在回执里，包括「不归集」", () => {
    const target = "《标题》";
    const none = groupNotice({ groupMode: GROUP_MODE_NONE, isCollection: false, target });
    expect(none).toContain("不归集");
    const existing = groupNotice({ groupMode: GROUP_MODE_EXISTING, isCollection: false, target });
    expect(existing).toContain("你选的合集");
    // 同为「新建」，合集与单视频的措辞不同 —— 单视频是投完再建，合集是逐条投完再归集。
    const singleNew = groupNotice({ groupMode: GROUP_MODE_NEW, isCollection: false, target });
    const collectionNew = groupNotice({ groupMode: GROUP_MODE_NEW, isCollection: true, target });
    expect(singleNew).not.toBe(collectionNew);
    expect(collectionNew).toContain("同一个合集");
    expect(singleNew).toContain("同名合集");
  });

  test("三种归集方式的回执两两不同（否则用户分不清自己选的那个生效没有）", () => {
    const target = "《标题》";
    const notices = GROUP_MODES.map((groupMode) => groupNotice({ groupMode, isCollection: false, target }));
    expect(new Set(notices).size).toBe(3);
    for (const notice of notices) {
      expect(notice).toContain(target);
    }
  });
});

/* ================================================================ 模板预览 */
describe("模板预览", () => {
  test("序号补零到两位，五个占位符都能替换", () => {
    const out = renderTemplate({
      template: "{job} · 第 {index}/{count} 集 — {title} — {source}",
      index: 2,
      title: "原标题",
      count: 12,
      job: "任务",
      source: "https://youtu.be/x"
    });
    expect(out).toBe("任务 · 第 02/12 集 — 原标题 — https://youtu.be/x");
  });

  test("同一个占位符出现多次都会替换", () => {
    expect(renderTemplate({ template: "{title}-{title}", index: 1, title: "T", count: 1, job: "j", source: "" })).toBe("T-T");
  });

  test("认不出的花括号原样留着 —— 「写错的占位符会留在标题里」这句提示必须是真的", () => {
    const out = renderTemplate({ template: "{titel} {title}", index: 1, title: "T", count: 1, job: "j", source: "" });
    expect(out).toBe("{titel} T");
  });

  test("标题里带花括号不会让预览抛异常", () => {
    const out = renderTemplate({ template: "{title}", index: 1, title: "a{b}c", count: 1, job: "j", source: "" });
    expect(out).toBe("a{b}c");
  });
});

/* ================================================================ 字符预算 */
describe("字符预算", () => {
  test("超没超与数字一起算出来：合成一个字符串，不分别渲染", () => {
    const ok = charBudget("字".repeat(LIMITS.title), LIMITS.title);
    expect(ok.over).toBe(0);
    expect(ok.warning).toBe("");
    expect(ok.label).toBe(`${LIMITS.title}/${LIMITS.title}`);

    const over = charBudget("字".repeat(LIMITS.title + 3), LIMITS.title);
    expect(over.over).toBe(3);
    expect(over.warning).toBe("超出 3 个字");
    expect(over.label).toBe(`${LIMITS.title + 3}/${LIMITS.title}`);
  });

  test("over 永不为负 —— 「-3」会被读成「还剩 3」", () => {
    expect(charBudget("字", LIMITS.title).over).toBe(0);
    expect(charBudget("", LIMITS.title).over).toBe(0);
  });
});

/* ============================================================ 合集的标题判据 */
/**
 * 合集量的是**渲染后的每一条**，不是模板本身。
 *
 * 这一组是「投稿时就要能看出标题超限」这件事的真正缺口所在：单视频那一档
 * （标题框旁边那个 `71/80`）改动前已经有了，而合集只量模板 —— `{title}` 这种
 * 7 个字的模板永远合规，预检给出一个**假的通过**，用户点提交后才吃到 400，
 * 或者更糟：提交成功、传到一半才失败。判据见 `ai/bug-fix/019`。
 */
describe("合集标题：判据量的是渲染后的每一条", () => {
  test("模板合规、渲染后超限 —— 必须报出来，并点名是哪一条", () => {
    const template = "P {title}";
    // 前提：模板本身远在上限之内（否则这条测的就不是「渲染后」了）。
    expect(template.length).toBeLessThan(LIMITS.title);

    const problems = collectionPreflight(template);

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("第 2 条");
    // 第 2 条渲染后 = "P " + 上限那么多个字 → 超出 2 个。
    expect(problems[0]).toContain("超出 2 个字");
  });

  test("只量模板就会给出假的通过 —— 这是本函数存在的唯一理由", () => {
    // 反证：把 `ITEMS` 拿掉（等价于「只量模板」），同一个模板是「没问题」的。
    expect(collectionPreflight("P {title}", { items: [] })).toEqual([]);
    expect(collectionPreflight("P {title}")).toHaveLength(1);
  });

  test("模板自己超长、渲染后合规 —— 不拦（服务端不校验模板本身）", () => {
    // `{index}` 是 7 个字，渲染出来只有 2 个（"01"）：模板长度可以远大于渲染结果。
    // 前端若拿模板去拦提交，就会出现「界面说超了、服务端说没事」。
    const template = "{index}".repeat(Math.ceil((LIMITS.title + 1) / 7));
    expect(template.length).toBeGreaterThan(LIMITS.title);
    expect(collectionPreflight(template)).toEqual([]);
  });

  test("多条超限：先说有几条，再点名超出最多的那条", () => {
    const items: readonly TitleSourceItem[] = [
      { index: 1, source_title: "字".repeat(LIMITS.title + 1), source_url: "" },
      { index: 2, source_title: "字".repeat(LIMITS.title + 5), source_url: "" }
    ];
    const problems = collectionPreflight("PP{title}", { items });

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("有 2 条渲染后的标题超限");
    // 报的是超出最多的那条（第 2 条，超 7 个），不是先遇到的那条。
    expect(problems[0]).toContain("第 2 条");
    expect(problems[0]).toContain("超出 7 个字");
  });

  test("单视频量的仍然是标题本身 —— 传了 items 也不参与", () => {
    expect(preflight({ metadata: metadata({ title: "字".repeat(LIMITS.title + 1) }), items: ITEMS })).toEqual([
      "标题超出 1 个字"
    ]);
  });

  test("条目还没读到时不报假警（空 items 不是「全都合规」的相反面）", () => {
    expect(collectionPreflight("随便一个模板", { items: [] })).toEqual([]);
  });
});

describe("合集标题预算", () => {
  test("longest 是渲染后最长的那条；等长时取先出现的（序号小的）", () => {
    const budget = collectionTitleBudget({
      template: "{title}",
      items: [
        { index: 1, source_title: "字".repeat(30), source_url: "" },
        { index: 2, source_title: "字".repeat(10), source_url: "" },
        { index: 3, source_title: "字".repeat(30), source_url: "" }
      ],
      jobTitle: "任务",
      limits: LIMITS
    });
    expect(budget.longest?.index).toBe(1);
    expect(budget.longest?.budget.length).toBe(30);
    expect(budget.rendered).toHaveLength(3);
  });

  test("判据与预览用的是同一个渲染结果（否则会出现「预览 79 字、判据 85 字」）", () => {
    const template = "{job}-{title}";
    const budget = collectionTitleBudget({ template, items: ITEMS, jobTitle: "任务", limits: LIMITS });
    for (const entry of budget.rendered) {
      const item = ITEMS.find((candidate) => candidate.index === entry.index)!;
      expect(entry.title).toBe(
        renderItemTitle({ template, item, count: ITEMS.length, jobTitle: "任务" })
      );
      expect(entry.budget.length).toBe(entry.title.length);
    }
  });

  test("模板那一档也返回，但不参与判据 —— 界面要知道用户正在打多少字", () => {
    const template = "P {title}";
    const budget = collectionTitleBudget({ template, items: ITEMS, jobTitle: "任务", limits: LIMITS });
    expect(budget.template.length).toBe(template.length);
    expect(budget.template.over).toBe(0);
    expect(budget.longest?.budget.over).toBeGreaterThan(0);
  });
});

/* ================================================================ 本地预检 */
describe("本地预检：可以提交的情形", () => {
  test("一切正常就没有问题", () => {
    expect(preflight()).toEqual([]);
  });

  test("合集默认「新建」而列表没读出来，也**不**拦提交", () => {
    // 「读到合集列表」只在真的要选合集时才是前置条件：把它当成整张表单的前置条件，
    // 会让「列表读不到」直接等于「视频传不上去」，而用户可能压根不打算归集。
    expect(preflight({ isCollection: true, groupMode: GROUP_MODE_NEW, seasonsLoaded: false })).toEqual([]);
  });

  test("自制稿不填来源是允许的（那条硬要求只针对转载）", () => {
    expect(preflight({ metadata: metadata({ copyright: COPYRIGHT_SELF, source: "" }) })).toEqual([]);
  });

  test("合集场景不在本地查来源 —— 模板上没有共用来源，逐条渲染由服务端保证", () => {
    expect(
      preflight({ isCollection: true, metadata: metadata({ copyright: COPYRIGHT_REPRINT, source: "", title: "x" }) })
    ).toEqual([]);
  });

  test("刚好等于上限不算超", () => {
    const length = LIMITS.title;
    expect(preflight({ metadata: metadata({ title: "字".repeat(length) }) })).toEqual([]);
  });
});

describe("本地预检：长度与数量", () => {
  test("标题是空的：单视频与合集的措辞不同", () => {
    expect(preflight({ metadata: metadata({ title: "   " }) })).toEqual(["标题不能是空的"]);
    expect(preflight({ isCollection: true, metadata: metadata({ title: "" }) })).toEqual(["标题模板渲染后不能是空的"]);
  });

  test("每一项都报出「超了几个字」，而不是悄悄截断", () => {
    const problems = preflight({
      metadata: metadata({
        title: "字".repeat(LIMITS.title + 2),
        desc: "字".repeat(LIMITS.desc + 3),
        tags: "字".repeat(LIMITS.tags + 4)
      })
    });
    expect(problems).toContain(`标题超出 2 个字`);
    expect(problems).toContain(`简介超出 3 个字`);
    expect(problems).toContain(`标签总长超出 4 个字`);
  });

  test("标签个数超限时报出实际个数与上限", () => {
    const tags = Array.from({ length: LIMITS.tag_count + 1 }, (_, i) => `t${i}`).join(",");
    expect(preflight({ metadata: metadata({ tags }) })).toContain(`标签有 ${LIMITS.tag_count + 1} 个，最多 ${LIMITS.tag_count} 个`);
  });

  test("单个标签超限时回显被截断的前缀，截断长度也来自服务端（tag_preview）", () => {
    // 既越过 `tag_item`（会被报出来）又越过 `tag_preview`（回显必须截断）。
    const long = "字".repeat(Math.max(LIMITS.tag_item, LIMITS.tag_preview) + 5);
    const problems = preflight({ metadata: metadata({ tags: `ok,${long}` }) });
    const message = problems.find((item) => item.includes("单个标签最多"));
    expect(message).toBeDefined();
    // 回显的是前 `tag_preview` 个字 + 一个省略号；写死 32 或 40 都会在这里露出来。
    expect(message).toContain(`「${"字".repeat(LIMITS.tag_preview)}…」`);
    expect(message).toContain(`${long.length} 个字`);
  });

  test("标签短于回显上限时原样回显，不加省略号", () => {
    const tag = "字".repeat(LIMITS.tag_preview - 1);
    // 先把 tag_item 压到比 tag_preview 小，才能构造出「回显不截断」的那条分支。
    const problems = preflight({ limits: { ...LIMITS, tag_item: 3 }, metadata: metadata({ tags: tag }) });
    const message = problems.find((item) => item.includes("单个标签最多"));
    expect(message).toContain(`「${tag}」`);
    expect(message).not.toContain("…");
  });

  test("评论超限也报出来", () => {
    expect(preflight({ comment: "字".repeat(LIMITS.comment + 7) })).toContain(`评论超出 7 个字`);
  });

  test("多条问题按固定顺序列出，标题那条在最前", () => {
    const problems = preflight({
      comment: "字".repeat(LIMITS.comment + 1),
      metadata: metadata({ title: "   ", tags: Array.from({ length: LIMITS.tag_count + 1 }, (_, i) => `t${i}`).join(",") })
    });
    expect(problems[0]).toBe("标题不能是空的");
    expect(problems.length).toBeGreaterThan(1);
  });
});

describe("本地预检：转载来源", () => {
  test("选了转载却没填来源 —— 就地拦住（留空会被 B站 退回来）", () => {
    const problems = preflight({ metadata: metadata({ copyright: COPYRIGHT_REPRINT, source: "  " }) });
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("没填原视频链接");
  });

  test("填了来源就放行", () => {
    expect(preflight({ metadata: metadata({ copyright: COPYRIGHT_REPRINT, source: "https://youtu.be/x" }) })).toEqual([]);
  });

  test("默认版权就是转载 —— 默认值本身也要让这条校验生效", () => {
    expect(DEFAULT_COPYRIGHT).toBe(COPYRIGHT_REPRINT);
    expect(preflight({ metadata: metadata({ copyright: DEFAULT_COPYRIGHT, source: "" }) })).toHaveLength(1);
  });
});

describe("本地预检：加入已有合集", () => {
  const existing = { groupMode: GROUP_MODE_EXISTING } as const;

  test("列表还在读时把它当成一条问题 —— 提交按钮的禁用条件就是「有没有问题」", () => {
    expect(preflight({ ...existing, seasonsLoaded: false })).toEqual(["正在读取账号里的合集…"]);
  });

  test("列表读出来了但一个都没选", () => {
    expect(preflight({ ...existing, seasonsLoaded: true, seasonId: null, chosenSeason: undefined })).toEqual([
      "选了「加入已有合集」但还没选是哪个合集"
    ]);
  });

  test("选过的合集已经不在列表里（列表刷新后消失）—— 提示要用户重选，不是「没选」", () => {
    expect(preflight({ ...existing, seasonsLoaded: true, seasonId: 7, chosenSeason: undefined })).toEqual([
      "选中的合集已经不在列表里了，请重新选一个"
    ]);
  });

  test("选中了就放行", () => {
    expect(preflight({ ...existing, seasonsLoaded: true, seasonId: 7, chosenSeason: seasonOption(7) })).toEqual([]);
  });

  test("单视频与合集共用同一条规则 —— 否则单视频用户只能吃到一个服务端 400", () => {
    for (const isCollection of [false, true]) {
      expect(preflight({ ...existing, isCollection, seasonsLoaded: true, seasonId: null })).toEqual([
        "选了「加入已有合集」但还没选是哪个合集"
      ]);
    }
  });
});
