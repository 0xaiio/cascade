import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Upload, X } from "lucide-react";
import {
  COPYRIGHT_REPRINT,
  COPYRIGHT_SELF,
  GROUP_MODE_EXISTING,
  GROUP_MODE_NEW,
  GROUP_MODE_NONE,
  charBudget,
  collectionTitleBudget,
  countTags,
  findChosenSeason,
  groupHint,
  localPreflight,
  renderItemTitle
} from "./biliForm";
import { errorMessage } from "../shared/errors";
import type { BiliGroupMode, BiliMetadata, BiliPrefill, BiliPrefillItem, BiliSeasonList } from "../contracts/types";
import { BiliIcon } from "./BiliIcon";

/**
 * 上传表单弹窗。**单视频与合集共用这一个组件**，只是标题与提示词不同：
 *
 * - 单视频：标题/简介就是最终提交的内容；
 * - 合集：标题/简介是**模板**，可用 `{index}` `{title}` `{count}` `{job}` 占位符，
 *   服务端会为每条视频各渲染一份（预览表就在下面，改一个字就能看到每条会变成什么）。
 *
 * 为什么用模态（与既有 `HelpPopover` 的「非模态」原则相反）：说明浮层是**可选阅读**，
 * 而这张表单是**必须先填完才能确认**的动作，需要焦点管理。所以这里按对话框的规矩来：
 * 打开时焦点进第一个输入框、`Esc` 关闭、关闭后焦点还给触发它的那个按钮。
 *
 * 三处刻意的做法：
 *
 * 1. **硬限数字来自服务端**（`prefill.limits`），前端只负责显示成 `12/80`。
 *    自己写一份常量，迟早会出现「界面说没超、提交回来 400」。
 * 2. **超限只报错、不截断**（与 `bili_meta` 同一条约定）：静默截断会让用户以为
 *    发出去的是完整内容。
 * 3. **未就绪的条目要显示出来**：合集里只要有一条还没下载好，服务端就会拒绝整批；
 *    在这里先标出来，比让用户点了确认再吃一个 409 有用。
 *
 * 「自制 / 转载」这一项的两条规则：
 *
 * - **默认转载**，因为这个工作流搬的是从别处下载来的视频。
 * - **来源输入框只在「转载 + 单视频」时出现**。合集里每条视频的出处都不一样，
 *   一个共用的来源框在语义上就是错的 —— 逐条的来源由服务端在渲染时各填各的
 *   （`bili_upload.render_collection`）。所以这里不是「藏起来」，是**根本没有这个字段**。
 *
 * 「归集方式」**单视频与合集都有**（投稿时就该能决定这条视频进不进某个合集，
 * 而不是非要先下载一整个 playlist 才能用上这个能力），三条：
 *
 * - **新建**：把这一批投完的视频归到一个新合集里。
 * - **加入已有**：合集列表是打开弹窗时从平台读回来的。读不到（未登录 / 网络不通）时
 *   显示原因并让用户改选新建，**不静默退回新建** —— 静默退回会让用户以为加进了某个合集，
 *   实际上建了一个新的。
 * - **不归集**：什么都不建也不加入。
 *
 * **默认值按场景不同，而且是由服务端给的**（`prefill.default_group_mode`）：
 * 合集默认「新建」（改动前就是每次新建，保持不变），单视频默认「不归集」
 * （改动前单视频从不进合集）。在前端按 `kind` 再推一遍就会有两份真相，
 * 而漂移的表现是「升级后投稿悄悄多出一批合集」——不会有任何报错。
 *
 * 合集列表**只在「加入已有」时才成为提交的前置条件**：默认那两个选项都不需要它，
 * 把「读到列表」当成整张表单的门槛，会让「列表读不到」直接等于「视频传不上去」。
 */
export function BiliUploadDialog({
  prefill,
  isCollection,
  seasons,
  loadSeasons,
  onCancel,
  onSubmit
}: {
  prefill: BiliPrefill;
  isCollection: boolean;
  /**
   * 账号下的已有合集。`null` = 还没读（界面显示「读取中」并拦住提交 —— 那时候
   * 用户看不到能选什么）；空数组 = 账号下确实没有合集；带 `error` = 没读成。
   */
  seasons: BiliSeasonList | null;
  /**
   * 去读 `seasons`。**由弹窗按需触发**：只有用户真的选中「加入已有合集」才会被调。
   * 读它是一次真实的网络往返，而默认选项（合集「新建」/ 单视频「不归集」）都用不上 ——
   * 放在开窗默认路径上，等于让两个默认选项也陪着等（理由见 App 的 `loadBiliSeasons`）。
   */
  loadSeasons: () => void;
  onCancel: () => void;
  onSubmit: (values: {
    metadata: BiliMetadata;
    makeCover: boolean;
    comment: string;
    groupMode: BiliGroupMode;
    seasonId: number | null;
    seasonTitle: string;
  }) => Promise<void>;
}) {
  const initial = (isCollection ? prefill.template : prefill.metadata) ?? {
    title: "",
    desc: "",
    tags: "",
    tid: prefill.partitions[0]?.tid ?? "",
    // 兜底值与服务端 `DEFAULT_COPYRIGHT` 一致（转载）。走 `??` 而不是写死，
    // 是为了让「服务端改默认值」这件事不需要在这里跟着改一遍。
    copyright: COPYRIGHT_REPRINT,
    source: ""
  };
  const [metadata, setMetadata] = useState<BiliMetadata>(initial);
  const [makeCover, setMakeCover] = useState(prefill.make_cover);
  const [comment, setComment] = useState(prefill.comment);
  // **默认值由服务端给**（合集 `new` / 单视频 `none`）。这里保留一个按 `kind` 的兜底，
  // 是为了不让「服务端返回里少了这个字段」变成「一个都没选中」—— 那样用户会以为
  // 归集方式是必填项，而其实有一条正确的默认值。
  const [groupMode, setGroupMode] = useState<BiliGroupMode>(
    prefill.default_group_mode ?? (isCollection ? GROUP_MODE_NEW : GROUP_MODE_NONE)
  );
  const [seasonId, setSeasonId] = useState<number | null>(null);
  // 合集名**优先用上次填的那个**（`prefill.season_title`，只有复用时才有值），
  // 否则退回任务名 —— 与「留空会用任务名顶上」是同一条口径，只是把默认值从
  // 「服务端现算」换成了「用户上次写的」。
  const [seasonTitle, setSeasonTitle] = useState(prefill.season_title || prefill.job_title);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const limits = prefill.limits;
  const seasonOptions = seasons?.seasons ?? [];
  const seasonsLoaded = seasons !== null;
  // `onCancel` 走 ref：调用方（App）每收到一条 SSE 事件就重渲染一次，若把它放进副作用依赖里，
  // 每次都会重新 `focus()` + `select()` —— 用户正打字时全选标题。挂载时只跑一次才是对的。
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;

  useEffect(() => {
    // 记住是谁打开的，关闭后把焦点还回去（`document.activeElement` 在模态期间会变，
    // 所以必须在挂载时就记下来）。
    const opener = document.activeElement as HTMLElement | null;
    titleRef.current?.focus();
    titleRef.current?.select();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") cancelRef.current();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      opener?.focus?.();
    };
  }, []);

  // `loadSeasons` 也走 ref，理由同上：App 每收到一条 SSE 事件就重渲染一次，
  // 而 `loadBiliSeasons` 每次渲染都是新的函数身份 —— 放进依赖里会让副作用每渲染跑一次。
  const loadSeasonsRef = useRef(loadSeasons);
  loadSeasonsRef.current = loadSeasons;

  // **按需读合集列表**：只有真的选中「加入已有合集」才发起这次请求。三个选项里只有它需要
  // 这份数据，另外两个（新建 / 不归集）都不看它；`seasons` 一旦落定（真的列表、空列表、
  // 或读不出来的结论）就不再为 `null`，去重与超时兜底都在 App 侧，这里只管触发。
  useEffect(() => {
    if (groupMode === GROUP_MODE_EXISTING && seasons === null) loadSeasonsRef.current();
  }, [groupMode, seasons]);

  // 标签的个数与最长项：判据在 `biliForm`（与服务端 `bili_meta.validate` 同源），
  // 这里只负责把 `tagCount` 渲染成 `3/10 个`。
  const tagCount = useMemo(() => countTags(metadata.tags), [metadata.tags]);
  // 「加入已有合集」必须真的选了一个，而选的那个此刻必须**还在列表里**。
  // 后一条不是防御性编程：合集列表是打开弹窗时读回来的，用户在弹窗开着的时候
  // 不可能删掉合集，但**测试夹具与服务端都会变** —— 与其赌它不会变，
  // 不如在提交前确认一次，否则就是一个 400。
  const chosenSeason = findChosenSeason(seasonOptions, seasonId);
  // 全部预检条件收在一个纯函数里（`biliForm.localPreflight`）：
  // 它单独可测，不必先渲染一个弹窗、再往输入框里打 80 个字。
  const localProblems = useMemo(
    () =>
      localPreflight({
        metadata,
        comment,
        limits,
        isCollection,
        // 合集场景下标题判据量的是**渲染后的每一条**，所以必须把条目传进去 ——
        // 只量模板会让 `{title}` 这种短模板永远「合规」，而渲染后可能超出几十个字。
        items: prefill.items,
        jobTitle: prefill.job_title,
        groupMode,
        seasonId,
        seasonsLoaded,
        chosenSeason
      }),
    [chosenSeason, comment, groupMode, isCollection, limits, metadata, prefill.items, prefill.job_title, seasonId, seasonsLoaded]
  );
  const notReady = prefill.items.filter((item) => !item.ready);
  const blocked = localProblems.length > 0 || notReady.length > 0;

  // 字段旁边那几个 `71/80` 的**状态**（不只是数字）。
  const titleB = charBudget(metadata.title, limits.title);
  const descB = charBudget(metadata.desc, limits.desc);
  const tagsB = charBudget(metadata.tags, limits.tags);
  const commentB = charBudget(comment, limits.comment);
  // 合集的标题预算：`template` 是用户正在编辑的那串字，`longest` 是渲染后最长的一条。
  // 只有后者参与判据（服务端不校验模板），但前者要显示出来 —— 用户得知道自己在打多少字。
  const renderedTitles = isCollection
    ? collectionTitleBudget({
        template: metadata.title,
        items: prefill.items,
        jobTitle: prefill.job_title,
        limits
      })
    : null;
  const groupHintText = groupHint({
    groupMode,
    seasonTitle,
    jobTitle: prefill.job_title,
    isCollection
  });

  /** 逐条预览要提交的标题。**只是预览** —— 真正提交的内容由服务端渲染。
   *  与预检共用 `renderItemTitle`：两处若各拼一次参数，就会出现「预览 79 字、判据 85 字」。 */
  function previewTitle(item: BiliPrefillItem): string {
    return renderItemTitle({
      template: metadata.title,
      item,
      count: prefill.items.length,
      jobTitle: prefill.job_title
    });
  }

  async function submit() {
    if (blocked) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit({
        metadata,
        makeCover,
        comment,
        groupMode,
        // **只在「加入已有合集」时给编号**：其余场景带上一个残留值会让服务端
        // 按「有 season_id 就不建合集」处理，「新建」悄悄变成往老合集里塞视频。
        seasonId: groupMode === GROUP_MODE_EXISTING ? seasonId : null,
        seasonTitle
      });
    } catch (err) {
      setError(errorMessage(err, "提交失败"));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="bili-dialog-title">
        <header className="modal-header">
          <div className="modal-heading">
            <BiliIcon size={20} />
            <h2 id="bili-dialog-title">{isCollection ? "把整个合集上传到 B 站" : "上传到 B 站"}</h2>
          </div>
          <button className="icon-button" type="button" title="关闭" aria-label="关闭上传窗口" onClick={onCancel}>
            <X size={18} />
          </button>
        </header>

        <div className="modal-body">
          <p className="hint">
            {isCollection ? (
              <>
                下面填的是<strong>模板</strong>：{prefill.items.length} 个视频会按它各生成一份上传信息，
                然后<strong>一个一个</strong>自动上传（B站 对连续上传有限流，所以中间会间隔一会儿），
                全部跑完再把它们归到同一个合集里。
              </>
            ) : (
              <>这里填的内容会原样提交给 B站。上传完成后会自动核对一次，确认平台上真的有了这条稿件。</>
            )}
          </p>

          {/* 复用了上一次投稿的值时**必须说出来**。不说，用户会以为这些标题/标签是
              应用按视频标题猜的 —— 一个「被猜中」的表单比空表单更不敢改，
              而它恰恰就是用户自己上次亲手填的那一份。 */}
          {prefill.reused && (
            <p className="hint">
              已填入<strong>上一次投稿</strong>时写的内容（标题、简介、标签、分区、版权、封面与评论），可以直接改，也可以清空重填。
            </p>
          )}

          <div className="field">
            <label className="field-label" htmlFor="bili-title">
              标题{isCollection ? "模板" : ""}
            </label>
            <input
              id="bili-title"
              ref={titleRef}
              value={metadata.title}
              onChange={(event) => setMetadata({ ...metadata, title: event.target.value })}
              placeholder={isCollection ? "{title}" : "视频标题"}
              // 语义化的「这一格填得不对」：读屏、以及 CSS（`[aria-invalid="true"]`）都用得上。
              aria-invalid={titleB.over > 0 || undefined}
              aria-describedby="bili-title-budget"
            />
            <span id="bili-title-budget" className={titleB.over > 0 ? "char-count is-over" : "char-count"}>
              {titleB.label}
              {titleB.warning ? ` · ${titleB.warning}` : ""}
            </span>
            {/* 合集才有这一行，而且它是**真正的判据**：服务端量的是渲染后的每一条。
                `{title}` 这样的模板永远只有 7 个字，只看上一行会给出一个假的「没问题」。 */}
            {renderedTitles?.longest && (
              <span
                className={renderedTitles.longest.budget.over > 0 ? "char-count is-over" : "char-count"}
              >
                {`渲染后最长 ${renderedTitles.longest.budget.label}（第 ${renderedTitles.longest.index} 条`}
                {renderedTitles.longest.budget.over > 0
                  ? `，超出 ${renderedTitles.longest.budget.over} 个字`
                  : ""}
                ）
              </span>
            )}
          </div>

          <div className="field">
            <label className="field-label" htmlFor="bili-tid">
              分区
            </label>
            <select
              id="bili-tid"
              value={metadata.tid}
              onChange={(event) => setMetadata({ ...metadata, tid: event.target.value })}
            >
              {prefill.partitions.map((partition) => (
                <option key={partition.tid} value={partition.tid}>
                  {partition.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <span className="field-label" id="bili-copyright-label">
              视频来源
            </span>
            <div className="bili-choice" role="radiogroup" aria-labelledby="bili-copyright-label">
              <label className="bili-choice-option">
                <input
                  type="radio"
                  name="bili-copyright"
                  value={COPYRIGHT_REPRINT}
                  checked={metadata.copyright === COPYRIGHT_REPRINT}
                  onChange={() => setMetadata({ ...metadata, copyright: COPYRIGHT_REPRINT })}
                />
                <span>转载</span>
              </label>
              <label className="bili-choice-option">
                <input
                  type="radio"
                  name="bili-copyright"
                  value={COPYRIGHT_SELF}
                  checked={metadata.copyright === COPYRIGHT_SELF}
                  onChange={() => setMetadata({ ...metadata, copyright: COPYRIGHT_SELF })}
                />
                <span>自制</span>
              </label>
            </div>
            <p className="hint">
              {metadata.copyright === COPYRIGHT_REPRINT
                ? "转载：这条视频不是你自己拍的，要填它原来的出处。"
                : "自制：这条视频是你自己的作品，不需要填出处。"}
            </p>
          </div>

          {!isCollection && metadata.copyright === COPYRIGHT_REPRINT && (
            <div className="field">
              <label className="field-label" htmlFor="bili-source">
                原视频链接（转载必填）
              </label>
              <input
                id="bili-source"
                value={metadata.source}
                onChange={(event) => setMetadata({ ...metadata, source: event.target.value })}
                placeholder="https://www.youtube.com/watch?v=…"
              />
              <p className="hint">B站 要求转载稿必须标明来源，留空会被直接退回来。</p>
            </div>
          )}

          <div className="field">
            <span className="field-label" id="bili-group-label">
              归集方式
            </span>
            <div className="bili-choice" role="radiogroup" aria-labelledby="bili-group-label">
              <label className="bili-choice-option">
                <input
                  type="radio"
                  name="bili-group-mode"
                  value={GROUP_MODE_NEW}
                  checked={groupMode === GROUP_MODE_NEW}
                  onChange={() => setGroupMode(GROUP_MODE_NEW)}
                />
                <span>新建合集</span>
              </label>
              <label className="bili-choice-option">
                <input
                  type="radio"
                  name="bili-group-mode"
                  value={GROUP_MODE_EXISTING}
                  checked={groupMode === GROUP_MODE_EXISTING}
                  onChange={() => setGroupMode(GROUP_MODE_EXISTING)}
                />
                <span>加入已有合集</span>
              </label>
              <label className="bili-choice-option">
                <input
                  type="radio"
                  name="bili-group-mode"
                  value={GROUP_MODE_NONE}
                  checked={groupMode === GROUP_MODE_NONE}
                  onChange={() => setGroupMode(GROUP_MODE_NONE)}
                />
                <span>不归集</span>
              </label>
            </div>
            <p className="hint">{groupHintText}</p>
          </div>

          {groupMode === GROUP_MODE_NEW && (
            <div className="field">
              <label className="field-label" htmlFor="bili-season-title">
                合集名
              </label>
              <input
                id="bili-season-title"
                value={seasonTitle}
                onChange={(event) => setSeasonTitle(event.target.value)}
                placeholder={prefill.job_title}
              />
              <p className="hint">留空会用任务名顶上。简介由应用自动写，不需要你填。</p>
            </div>
          )}

          {groupMode === GROUP_MODE_EXISTING && (
            <div className="field">
              <label className="field-label" htmlFor="bili-season-select">
                加进哪个合集
              </label>
              {!seasonsLoaded ? (
                <p className="hint">正在读取账号里的合集…</p>
              ) : seasons?.error ? (
                <p className="hint hint-error">{seasons.advice || seasons.error}</p>
              ) : seasonOptions.length === 0 ? (
                <p className="hint">
                  这个账号下还没有合集。改选「新建合集」就能直接建一个 ——
                  B站 的合集是要在创作中心建好后，这里才能往里加。
                </p>
              ) : (
                <select
                  id="bili-season-select"
                  value={seasonId === null ? "" : String(seasonId)}
                  onChange={(event) => setSeasonId(event.target.value === "" ? null : Number(event.target.value))}
                >
                  <option value="">请选择一个合集</option>
                  {seasonOptions.map((option) => (
                    <option key={option.season_id} value={option.season_id}>
                      {option.title}
                      {option.total > 0 ? `（现有 ${option.total} 个视频）` : ""}
                    </option>
                  ))}
                </select>
              )}
              {chosenSeason?.desc && <p className="hint">{chosenSeason.desc}</p>}
            </div>
          )}

          <div className="field">
            <label className="field-label" htmlFor="bili-tags">
              标签（用英文逗号分隔）
            </label>
            <input
              id="bili-tags"
              value={metadata.tags}
              onChange={(event) => setMetadata({ ...metadata, tags: event.target.value })}
              placeholder="例如 编程,数据库"
            />
            <span className="char-count">
              {tagCount}/{limits.tag_count} 个 · {metadata.tags.length}/{limits.tags} 字
            </span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="bili-desc">
              简介{isCollection ? "模板" : ""}
            </label>
            <textarea
              id="bili-desc"
              rows={4}
              value={metadata.desc}
              onChange={(event) => setMetadata({ ...metadata, desc: event.target.value })}
            />
            <span className="char-count">
              {metadata.desc.length}/{limits.desc}
            </span>
          </div>

          {isCollection && (
            <div className="field">
              <span className="field-label">可用的占位符</span>
              <p className="hint">
                <code>{"{index}"}</code> 序号（01、02…） · <code>{"{title}"}</code> 该视频的标题 ·{" "}
                <code>{"{count}"}</code> 总条数 · <code>{"{job}"}</code> 任务名 · <code>{"{source}"}</code> 原视频链接。
                写错的占位符会原样留在标题里，下面的预览能看出来。
              </p>
            </div>
          )}

          <div className="field">
            <label className="toggle-row" htmlFor="bili-cover">
              <input
                id="bili-cover"
                type="checkbox"
                checked={makeCover}
                onChange={(event) => setMakeCover(event.target.checked)}
              />
              <span>自动生成封面（取视频第 1 秒的画面；没装 ffmpeg 时会自动跳过，不影响上传）</span>
            </label>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="bili-comment">
              上传后置顶一条评论（留空 = 不发）
            </label>
            <textarea
              id="bili-comment"
              rows={3}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
            <span className="char-count">
              {comment.length}/{limits.comment}
            </span>
          </div>

          {isCollection && (
            <div className="field">
              <span className="field-label">逐条预览（{prefill.items.length} 条）</span>
              <div className="table-wrap bili-preview-wrap">
                <table className="bili-preview-table">
                  <thead>
                    <tr>
                      <th>序号</th>
                      <th>原视频</th>
                      <th>将提交的标题</th>
                      <th>字幕</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prefill.items.map((item) => (
                      <tr key={item.item_id ?? item.index} className={item.ready ? undefined : "is-blocked"}>
                        <td>{item.index}</td>
                        <td>
                          {item.source_title}
                          {item.ready ? null : <span className="bili-inline-warn">还没下载好</span>}
                        </td>
                        <td>{previewTitle(item)}</td>
                        {/* 文案由服务端给（`BiliPrefillItem.subtitle`）：前端拼一遍就会出现
                            「界面说自动生成、后端写 ASR」这种两套说法。空串 = 还没有结论。 */}
                        <td>{item.subtitle || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(localProblems.length > 0 || prefill.problems.length > 0 || notReady.length > 0) && (
            <ul className="bili-problems" role="alert">
              {notReady.map((item) => (
                <li key={`ready-${item.item_id ?? item.index}`}>
                  <AlertTriangle size={15} />第 {item.index} 条「{item.source_title}」还没下载好，不能上传
                </li>
              ))}
              {[...new Set([...prefill.problems, ...localProblems])].map((problem) => (
                <li key={problem}>
                  <AlertTriangle size={15} />
                  {problem}
                </li>
              ))}
            </ul>
          )}

          {error && (
            <div className="alert" role="alert">
              <AlertTriangle size={18} />
              {error}
            </div>
          )}
        </div>

        <footer className="modal-footer">
          <button className="ghost-button" type="button" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button className="primary-button bili-submit" type="button" onClick={() => void submit()} disabled={submitting || blocked}>
            <Upload size={16} />
            {submitting ? "提交中…" : isCollection ? "开始上传全部" : "开始上传"}
          </button>
        </footer>
      </div>
    </div>
  );
}
