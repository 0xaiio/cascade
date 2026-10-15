import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Upload, X } from "lucide-react";
import type { BiliMetadata, BiliPrefill } from "../types";
import { BiliIcon } from "./BiliIcon";

/**
 * 版权取值。与 B站 接口的 `copyright` 字段同值域，也与服务端 `bili_meta.COPYRIGHT_*` 一致。
 *
 * **默认转载**：这个工作流搬的是从别的平台下载下来的视频，绝大多数稿件都不是自己拍的。
 * 默认「自制」会把每一单都填成假信息，而 B站 对「把转载标成自制」是有处置的。
 * 理由写在 `backend/app/bili_meta.py` 的 `DEFAULT_COPYRIGHT` 旁边，两边都要改。
 */
export const COPYRIGHT_SELF = "1";
export const COPYRIGHT_REPRINT = "2";

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
 */
export function BiliUploadDialog({
  prefill,
  isCollection,
  onCancel,
  onSubmit
}: {
  prefill: BiliPrefill;
  isCollection: boolean;
  onCancel: () => void;
  onSubmit: (values: { metadata: BiliMetadata; makeCover: boolean; comment: string }) => Promise<void>;
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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const limits = prefill.limits;
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

  const tagCount = useMemo(() => metadata.tags.split(",").filter((item) => item.trim()).length, [metadata.tags]);
  // 标签的**单项**上限也要查。服务端 `validate()` 会拒，但等到点了提交才看到 400
  // 就已经离输入框很远了；`limits.tag_item` 一直下发着，这里就地用上。
  const longestTag = useMemo(
    () => metadata.tags.split(",").reduce((longest, item) => (item.trim().length > longest.length ? item.trim() : longest), ""),
    [metadata.tags]
  );
  // 转载稿缺来源是**平台的硬要求**（会被 B站 退回来），所以就地拦住 ——
  // 与服务端 `bili_meta.validate` 同一条判据，写两遍是为了让用户在输入框旁边就看到。
  // 合集场景没有这个输入框（每条各用各的原始链接），由服务端逐条渲染时保证，不在这里查。
  const sourceRequired = !isCollection && metadata.copyright === COPYRIGHT_REPRINT;
  const localProblems = useMemo(
    () => [
      ...(metadata.title.trim() ? [] : [isCollection ? "标题模板渲染后不能是空的" : "标题不能是空的"]),
      ...(metadata.title.length > limits.title ? [`标题超出 ${metadata.title.length - limits.title} 个字`] : []),
      ...(metadata.desc.length > limits.desc ? [`简介超出 ${metadata.desc.length - limits.desc} 个字`] : []),
      ...(metadata.tags.length > limits.tags ? [`标签总长超出 ${metadata.tags.length - limits.tags} 个字`] : []),
      ...(tagCount > limits.tag_count ? [`标签有 ${tagCount} 个，最多 ${limits.tag_count} 个`] : []),
      ...(longestTag.length > limits.tag_item
        ? [`「${longestTag.length > 12 ? `${longestTag.slice(0, 12)}…` : longestTag}」这个标签有 ${longestTag.length} 个字，单个标签最多 ${limits.tag_item} 个字`]
        : []),
      ...(comment.length > limits.comment ? [`评论超出 ${comment.length - limits.comment} 个字`] : []),
      ...(sourceRequired && !metadata.source.trim()
        ? ["选了「转载」但没填原视频链接（转载稿必须填来源，留空会被 B站 退回来）"]
        : [])
    ],
    [comment.length, isCollection, limits, longestTag, metadata, sourceRequired, tagCount]
  );
  const notReady = prefill.items.filter((item) => !item.ready);
  const blocked = localProblems.length > 0 || notReady.length > 0;

  async function submit() {
    if (blocked) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit({ metadata, makeCover, comment });
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
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
            />
            <span className="char-count">
              {metadata.title.length}/{limits.title}
            </span>
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
                        <td>{renderTemplate(metadata.title, item, prefill)}</td>
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

/**
 * 前端只为「预览」渲染一次模板。
 *
 * **它不是提交路径**：真正提交的内容由服务端用 `{title}` 模板 + 每条视频的数据渲染
 * （`bili_meta.render`，单一来源）。这里重放一遍是为了让用户改一个字就能看到效果；
 * 若两者不一致，以服务端为准 —— 这正是为什么提交结果的标题会在界面上回显。
 */
function renderTemplate(template: string, item: BiliPrefill["items"][number], prefill: BiliPrefill) {
  const values: Record<string, string> = {
    index: String(item.index).padStart(2, "0"),
    title: item.source_title,
    count: String(prefill.items.length),
    job: prefill.job_title,
    source: item.source_url
  };
  return template.replace(/\{(index|title|count|job|source)\}/g, (match, key: string) => values[key] ?? match);
}
