import { useEffect, useState } from "react";
import { UPLOAD_STAGES, jobUploadProgress, uploadElapsedLabel } from "./biliProgress";
import type { BiliUpload } from "../contracts/types";

/**
 * 任务卡片里的「上传进度」：**一条四段轨迹 + 一句在等什么 + 这一步用掉了多久**。
 *
 * 只在**需要用户等**的时候渲染（在跑，或已提交等审核）。已发布 / 失败 / 待核对不渲染 ——
 * 那些已经有结论了，再画一条走完的轨迹只是往任务卡片上添装饰。
 *
 * 「已用 1:23」由组件自己按秒推进：`biliup` 在传输那一段可能 8 分钟不发一条输出，
 * 只靠 SSE 刷新的话用户看到的是一个静止的界面，分不清「在跑」还是「卡死」。
 * 计时起点来自服务端的 `started_at`，不是本地点击时刻 —— 两个时钟不一致时以服务端为准。
 *
 * **推进的条件是「在跑」，不是「在等」**：审核期这块东西仍然挂着（`waiting`），
 * 但那一段已经结束了，秒表必须停在**上传用时**这个定值上。
 * 早先在这里按 `waiting` 推进（`ai/bug-fix/020` 的修法），结果是「上传进度」下面
 * 一个数字替平台审核计时，一路涨到 `已提交 6:41:18`（实测 Ep 125，那次上传只用了 52 秒）。
 * 「停住会被读成卡住」这个担心由**措辞**解决：定值那一档写的是「上传用时 0:52」，
 * 是个已经发生完的时长，而不是一个还在走的钟。见 ai/ui/008。
 */
export function BiliUploadProgress({ uploads }: { uploads: readonly BiliUpload[] }) {
  const view = jobUploadProgress(uploads);
  // 渲染与否由 `waiting` 决定（下面 `return null`），秒表只认 `running`。
  const waiting = view?.waiting ?? false;
  const ticking = view?.running ?? false;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!ticking) return;
    // 立刻对齐一次：切到「在跑」的那一刻不要把上一轮的旧 `now` 留在屏幕上。
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);

  if (!view || !view.waiting) return null;
  const elapsed = uploadElapsedLabel(view, now);
  const stage = view.stage;

  return (
    <div
      className="bili-progress"
      // 为什么没有百分比：`biliup` 不输出字节进度（管道模式非 TTY），B站 审核也不提供进度。
      // 这句解释挂在 hover 上 —— 需要的人能看到，不需要的人不会被一行说明挡住眼睛。
      title="传输阶段 biliup 不上报字节数，B 站 审核也不提供百分比，所以这里按阶段展示"
    >
      <div className="bili-progress-head">
        <span className="bili-status-label">上传进度</span>
        {view.total > 1 && (
          <span className="bili-progress-count">
            第 {view.current}/{view.total} 条 · {view.title}
          </span>
        )}
        {elapsed && <span className="bili-progress-clock">{elapsed}</span>}
      </div>
      <ol className="bili-steps" aria-label={`上传到第 ${stage.index + 1} 步：${stage.detail}`}>
        {UPLOAD_STAGES.map((item, index) => {
          // 已走完的节点是 done；当前节点在「进行中」时是 active（有动画），
          // 已经 settled（审核中 / 已发布）时它也算 done —— 动画不能停在一个已经结束的步骤上。
          const state =
            index < stage.index || (index === stage.index && stage.settled)
              ? "is-done"
              : index === stage.index
                ? "is-active"
                : "";
          return (
            <li key={item.key} className={`bili-step ${state}`}>
              <span className="bili-step-dot" aria-hidden="true" />
              <span className="bili-step-label">{item.label}</span>
            </li>
          );
        })}
      </ol>
      <div className={`bili-progress-detail ${stage.broken ? "is-bad" : ""}`}>
        {stage.detail}
        {view.total > 1 && view.done > 0 ? ` · 已有 ${view.done}/${view.total} 条确认在平台上` : ""}
      </div>
    </div>
  );
}
