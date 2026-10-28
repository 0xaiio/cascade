import type { BiliGroupMode, BiliLimits, BiliMetadata, BiliSeasonOption } from "../contracts/types";

/**
 * 投稿表单的**词汇表与预检逻辑**。全部是纯函数 —— 这一层从 `BiliUploadDialog` 里抽出来，
 * 是为了让「哪些内容算超限」「回执怎么说」能被直接单测，而不必先渲染一个弹窗再点按钮。
 *
 * 三条口径，改之前先读：
 *
 * 1. **超限只报错、不截断**（与服务端 `bili_meta.validate` 同一条约定）。静默截断会让用户
 *    以为发出去的是完整内容。
 * 2. **本地预检与服务端校验必须同源**。这里报的，服务端一定会报；服务端会报的，这里尽量也报
 *    —— 少一条的代价是用户点完提交才在弹窗里看到一个 400。
 * 3. **数字来自服务端**（`prefill.limits` / `status.limits`）。这里**不写死任何上限**，
 *    包括「超限标签回显多少个字」—— 那也是一个和平台有关的数字，写死就会出现
 *    「弹窗里说 32 个字、服务端回执说另一个截断」。见 `bili_meta.TAG_PREVIEW_MAX`。
 */

/* ---------------------------------------------------------------- 词汇表 */
/** 版权取值。与服务端 `bili_meta.COPYRIGHT_*` 同值，也与 B站 接口的 `copyright` 字段同值域。 */
export const COPYRIGHT_SELF = "1";
export const COPYRIGHT_REPRINT = "2";

/**
 * **默认转载**：这个工作流搬的是从别的平台下载下来的视频，绝大多数稿件都不是自己拍的。
 * 默认「自制」会把每一单都填成假信息，而 B站 对「把转载标成自制」是有处置的。
 * 判据的权威在服务端 `bili_meta.DEFAULT_COPYRIGHT` 旁边，两边都要改。
 */
export const DEFAULT_COPYRIGHT = COPYRIGHT_REPRINT;

/** 归入方式。与服务端 `bili_meta.GROUP_MODE_*` 同值。 */
export const GROUP_MODE_NONE = "none";
export const GROUP_MODE_EXISTING = "existing";
export const GROUP_MODE_NEW = "new";

/* ---------------------------------------------------------------- 标签 */
/** 有内容的标签个数。**与提交时服务端数的口径一致**（空项不算一个标签）。 */
export function countTags(tags: string): number {
  return tags.split(",").filter((item) => item.trim()).length;
}

/** 最长的那个标签（已去空白）。用来报「单个标签超限」。 */
export function longestTag(tags: string): string {
  return tags
    .split(",")
    .reduce((longest, item) => (item.trim().length > longest.length ? item.trim() : longest), "");
}

/* ---------------------------------------------------------------- 归集 */
/** 「加入已有合集」时用户选中的那一个。找不到 = 还没选、或它已不在列表里。 */
export function findChosenSeason(
  options: readonly BiliSeasonOption[],
  seasonId: number | null
): BiliSeasonOption | undefined {
  return options.find((item) => item.season_id === seasonId);
}

/** 归集方式下面那句说明。**主语按场景分句**：单视频说「这条视频」，合集说「这些视频」——
 *  共用一句会让人以为单视频投稿也会批量处理。 */
export function groupHint(input: {
  groupMode: BiliGroupMode;
  seasonTitle: string;
  jobTitle: string;
  isCollection: boolean;
}): string {
  const itemsLabel = input.isCollection ? "这些视频" : "这条视频";
  if (input.groupMode === GROUP_MODE_NEW) {
    return `自动建一个名为「${input.seasonTitle.trim() || input.jobTitle}」的新合集，把${itemsLabel}放进去。`;
  }
  if (input.groupMode === GROUP_MODE_EXISTING) {
    return `把${itemsLabel}追加到你选的已有合集里，不新建。B站 对合集有审核，加进去之后可能还需要等平台处理。`;
  }
  return `不建合集、也不加入任何合集，${itemsLabel}单独成稿。`;
}

/** 提交成功后的回执里，这次上传的**对象**是谁。 */
export function noticeTarget(input: {
  isCollection: boolean;
  jobTitle: string;
  metadataTitle: string;
  itemCount: number;
}): string {
  return input.isCollection
    ? `《${input.jobTitle}》的 ${input.itemCount} 个视频`
    : `《${input.metadataTitle}》`;
}

/**
 * 提交成功后的回执。
 *
 * **归集去向必须出现在回执里**，包括「不归集」：那是用户刚在表单里做过的一个选择，
 * 回执不提它，用户就没法确认自己选的那个真的生效了 —— 而「不归集」恰恰是最容易被
 * 误以为没生效的一项（界面上看不出任何变化）。
 */
export function groupNotice(input: {
  groupMode: BiliGroupMode;
  isCollection: boolean;
  target: string;
}): string {
  if (input.groupMode === GROUP_MODE_NONE) {
    return `已开始上传${input.target}，不归集 —— 跑完这里会变成「已上传」。`;
  }
  if (input.groupMode === GROUP_MODE_EXISTING) {
    return `已开始上传${input.target}，跑完会加进你选的合集。`;
  }
  return input.isCollection
    ? `已开始上传${input.target}，逐个投完后会自动归入同一个合集。`
    : `已开始上传${input.target}，投完会自动建一个同名合集并把它放进去。`;
}

/* ---------------------------------------------------------------- 字符预算 */
/**
 * 一个字段的「字符预算」。字段旁边那个 `71/80` 就是它的 `label`。
 *
 * 抽成结构体而不是直接 `{len}/{limit}`，是因为**超没超必须和数字一起算出来**：
 * 原先四处计数都只渲染数字，超限与合规长得一模一样（同一档灰字），
 * 用户得先读数字、再去别处比对上限，才知道自己超了。见 `ai/bug-fix/019`。
 */
export interface CharBudget {
  length: number;
  limit: number;
  /** 超出的字数。`0` = 没超。**永远不为负**，否则「-3」会被读成「还剩 3」。 */
  over: number;
  /** 「71/80」。 */
  label: string;
  /** 超限时的一句话；合规时是空串（不是 `null`，少一处空值判断）。 */
  warning: string;
}

export function charBudget(value: string, limit: number): CharBudget {
  const length = value.length;
  const over = Math.max(0, length - limit);
  return { length, limit, over, label: `${length}/${limit}`, warning: over > 0 ? `超出 ${over} 个字` : "" };
}

/** 预检与预览只需要这三样：序号、原视频标题、原始链接（渲染模板用）。 */
export interface TitleSourceItem {
  index: number;
  source_title: string;
  source_url: string;
}

/** 合集里某一条最终会提交的标题。
 *
 * **前端只为预览与预检重放一次**，权威仍是服务端（`bili_meta.render`）。两处必须用
 * 同一组入参，否则「预览说 79 字、服务端判 85 字」这种差异会让预检变成假保证。
 */
export function renderItemTitle(input: {
  template: string;
  item: TitleSourceItem;
  count: number;
  jobTitle: string;
}): string {
  return renderTemplate({
    template: input.template,
    index: input.item.index,
    title: input.item.source_title,
    count: input.count,
    job: input.jobTitle,
    source: input.item.source_url
  });
}

export interface RenderedTitleBudget {
  index: number;
  title: string;
  budget: CharBudget;
}

/**
 * 合集的标题预算：**量的是渲染后的每一条，不是模板本身**。
 *
 * 这是本函数存在的**唯一理由**。模板 `{title}` 只有 7 个字符，永远「合规」；
 * 渲染成 `C++ Weekly - Ep 105 - Learning "Modern" C++ - 5: Looping and Algorithms`
 * 之后是 78 个字，再加个前后缀就超了。服务端提交时是逐条渲染后校验的
 * （`bili_upload.validate_collection`），前端只量模板就等于**什么都没查**。
 *
 * `template` 那一档仍然返回（界面要显示用户正在输入的这串字有多长），但它**不参与判据**：
 * 服务端不校验模板本身，前端若拿它拦提交，就会出现「界面说超了、服务端说没事」。
 */
export function collectionTitleBudget(input: {
  template: string;
  items: readonly TitleSourceItem[];
  jobTitle: string;
  limits: Pick<BiliLimits, "title">;
}): { template: CharBudget; rendered: RenderedTitleBudget[]; longest: RenderedTitleBudget | null } {
  const rendered = input.items.map((item) => {
    const title = renderItemTitle({
      template: input.template,
      item,
      count: input.items.length,
      jobTitle: input.jobTitle
    });
    return { index: item.index, title, budget: charBudget(title, input.limits.title) };
  });
  // 多条等长时保留**先出现的那条**（序号小的）：用户从上往下改，第一条更有指导性。
  let longest: RenderedTitleBudget | null = null;
  for (const entry of rendered) {
    if (longest === null || entry.budget.length > longest.budget.length) longest = entry;
  }
  return { template: charBudget(input.template, input.limits.title), rendered, longest };
}

/* ---------------------------------------------------------------- 预检 */
export interface LocalPreflightInput {
  metadata: BiliMetadata;
  comment: string;
  limits: BiliLimits;
  isCollection: boolean;
  /** 合集里的条目。单视频传空数组。**合集场景下标题判据完全依赖它**（见 `collectionTitleBudget`）。 */
  items: readonly TitleSourceItem[];
  /** 任务名。模板里的 `{job}` 要用它渲染，缺了就会和真实提交的标题不一致。 */
  jobTitle: string;
  groupMode: BiliGroupMode;
  seasonId: number | null;
  /** `false` = 列表还在读（`null`）。那时选不了具体合集，也不该拿「没选」去怪用户。 */
  seasonsLoaded: boolean;
  chosenSeason: BiliSeasonOption | undefined;
}

/**
 * 提交前的本地预检。返回人话问题清单，空列表 = 可以提交。
 *
 * **这里绝不改动 `input`。** 与服务端 `bili_meta.validate` 一样：报错而不是修好再提交 ——
 * 先修再校验等于把校验废掉（非法值已被修成合法值，校验永远看不到它）。
 *
 * 「正在读取账号里的合集…」也算一条问题，这是**故意的**：`blocked` 是「有没有问题」，
 * 而提交按钮的禁用条件正是它。把它做成问题，就不需要再单独维护另一个布尔量 ——
 * 两份「能不能提交」的判据迟早会不一致。
 */
/**
 * 标题超限的判据。**单视频与合集量的不是同一个东西，这一点是全部要点**：
 *
 * - 单视频：量标题本身（服务端 `bili_meta.validate` 就是这么判的）。
 * - 合集：量**渲染后的每一条**（服务端 `validate_collection` 就是这么判的）。
 *
 * 合集若拿模板去量，`{title}` 这种 7 个字的模板永远合规 —— 预检会给出一个**假的通过**，
 * 而用户点提交后吃到一个 400，或者更糟：提交成功、上传到一半才 failed。
 * 见 `ai/bug-fix/019`。
 */
function titleProblems(input: LocalPreflightInput): string[] {
  if (!input.isCollection) {
    const over = input.metadata.title.length - input.limits.title;
    return over > 0 ? [`标题超出 ${over} 个字`] : [];
  }
  const { rendered } = collectionTitleBudget({
    template: input.metadata.title,
    items: input.items,
    jobTitle: input.jobTitle,
    limits: input.limits
  });
  const overs = rendered.filter((entry) => entry.budget.over > 0);
  if (overs.length === 0) return [];
  // 报**超出最多**的那条：用户改模板时最需要的是「还差几个字」，而不是自己去数哪条最糟。
  const worst = overs.reduce((left, right) => (right.budget.over > left.budget.over ? right : left));
  // 只超一条时说清是哪条；超多条时先给个数，否则用户会以为改完这一条就好了。
  return overs.length === 1
    ? [`第 ${worst.index} 条「${worst.title}」渲染后超出 ${worst.budget.over} 个字`]
    : [
        `有 ${overs.length} 条渲染后的标题超限，最严重的是第 ${worst.index} 条「${worst.title}」（超出 ${worst.budget.over} 个字）`
      ];
}

export function localPreflight(input: LocalPreflightInput): string[] {
  const { metadata, limits } = input;
  const longest = longestTag(metadata.tags);
  const tags = countTags(metadata.tags);
  // 转载稿缺来源是**平台的硬要求**（会被 B站 退回来），所以就地拦住 ——
  // 与服务端 `bili_meta.validate` 同一条判据，写两遍是为了让用户在输入框旁边就看到。
  // 合集场景没有这个输入框（每条各用各的原始链接），由服务端逐条渲染时保证，不在这里查。
  const sourceRequired = !input.isCollection && metadata.copyright === COPYRIGHT_REPRINT;
  const preview = longest.length > limits.tag_preview
    ? `${longest.slice(0, limits.tag_preview)}…`
    : longest;

  return [
    ...(metadata.title.trim() ? [] : [input.isCollection ? "标题模板渲染后不能是空的" : "标题不能是空的"]),
    ...titleProblems(input),
    ...(metadata.desc.length > limits.desc ? [`简介超出 ${metadata.desc.length - limits.desc} 个字`] : []),
    ...(metadata.tags.length > limits.tags ? [`标签总长超出 ${metadata.tags.length - limits.tags} 个字`] : []),
    ...(tags > limits.tag_count ? [`标签有 ${tags} 个，最多 ${limits.tag_count} 个`] : []),
    ...(longest.length > limits.tag_item
      ? [`「${preview}」这个标签有 ${longest.length} 个字，单个标签最多 ${limits.tag_item} 个字`]
      : []),
    ...(input.comment.length > limits.comment ? [`评论超出 ${input.comment.length - limits.comment} 个字`] : []),
    ...(sourceRequired && !metadata.source.trim()
      ? ["选了「转载」但没填原视频链接（转载稿必须填来源，留空会被 B站 退回来）"]
      : []),
    // **只在真的要用这份列表时才拦提交**：选了「加入已有」才需要它。
    // 把「读到合集列表」当成整张表单的前置条件，会让「列表读不到」直接等于
    // 「视频传不上去」—— 而用户可能只是想投个视频、压根不打算归集。
    ...(input.groupMode === GROUP_MODE_EXISTING && !input.seasonsLoaded ? ["正在读取账号里的合集…"] : []),
    // **单视频与合集用同一条规则**：控件既然在两处都可见，判据就不能只在一处成立 ——
    // 否则单视频用户选了「加入已有」又没挑合集时，只会吃到一个服务端 400
    // （`missing_season_id`），而这句话明明可以就在下拉旁边说出来。
    //
    // 而 `seasonsLoaded` 是这条判据的**前提**：列表还没读出来时用户根本没得选，
    // 再说一句「还没选是哪个合集」就是在怪他 —— 两句话同时出现还会自相矛盾
    // （前一句说「还没读完」，后一句就断定他漏选了）。
    ...(input.groupMode === GROUP_MODE_EXISTING && input.seasonsLoaded && !input.chosenSeason
      ? [
          input.seasonId === null
            ? "选了「加入已有合集」但还没选是哪个合集"
            : "选中的合集已经不在列表里了，请重新选一个"
        ]
      : [])
  ];
}

/* ---------------------------------------------------------------- 模板预览 */
/**
 * 前端只为**预览**渲染一次模板。
 *
 * **它不是提交路径**：真正提交的内容由服务端用同一个模板 + 每条视频的数据渲染
 * （`bili_meta.render`，单一来源）。这里重放一遍是为了让用户改一个字就能看到效果；
 * 若两者不一致，以服务端为准 —— 这正是为什么提交结果的标题会在界面上回显。
 */
export function renderTemplate(input: {
  template: string;
  index: number;
  title: string;
  count: number;
  job: string;
  source: string;
}): string {
  const values: Record<string, string> = {
    index: String(input.index).padStart(2, "0"),
    title: input.title,
    count: String(input.count),
    job: input.job,
    source: input.source
  };
  // `?? match` 保留无法识别的花括号原样 —— 与 `bili_meta.render` 的行为一致，
  // 所以下面那句「写错的占位符会原样留在标题里」的提示是真的。
  return input.template.replace(/\{(index|title|count|job|source)\}/g, (match, key: string) => values[key] ?? match);
}
