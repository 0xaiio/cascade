import { useEffect, useState } from "react";
import { UPLOAD_STAGES, jobUploadProgress, uploadElapsedLabel } from "../biliProgress";
import type { BiliUpload } from "../types";

/**
 * 任务卡片里的「上传进度」：**一条四段轨迹 + 一句在等什么 + 已经等了多久**。
 *
 * 只在**需要用户等**的时候渲染（在跑，或已提交等审核）。已发布 / 失败 / 待核对不渲染 ——
 * 那些已经有结论了，再画一条走完的轨迹只是往任务卡片上添装饰。
 *
 * 「已用 1:23」由组件自己按秒推进：`biliup` 在传输那一段可能 8 分钟不发一条输出，
 * 只靠 SSE 刷新的话用户看到的是一个静止的界面，分不清「在跑」还是「卡死」。
 * 计时起点来自服务端的 `started_at`，不是本地点击时刻 —— 两个时钟不一致时以服务端为准。
 */
export function BiliUploadProgress({ uploads }: { uploads: readonly BiliUpload[] }) {
  const view = jobUploadProgress(uploads);
  const running = view?.running ?? false;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return;
    // 立刻对齐一次：`running` 刚变成 true 时不要把「已用 0:00」挂在屏幕上等一秒。
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

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
