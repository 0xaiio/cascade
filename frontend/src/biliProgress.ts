import { formatClock, parseServerTime } from "./formatting";
import { isAcceptedUpload, isInFlightUpload } from "./biliStatus";
import type { BiliUpload, BiliUploadStatus } from "./types";

/**
 * 上传进度：**一条四段的轨迹，不是一根百分比条**。
 *
 * 为什么不做百分比条：`biliup` 在管道模式（非 TTY）下**不输出字节进度**，
 * 只吐三类可用的行 —— `pre_upload`（准备）、`Upload completed`（传完）、`APP接口投稿成功`（提交完），
 * 服务端把它们折成 0.2 / 0.6 / 0.95（见 `backend/app/bilibili/bili_bin.py` 的 `_PROGRESS_STAGES`）。
 * 实测一次 14 MB 的投稿，传输这一段跑了 **481 秒**（`Upload completed … cost 481.85s`），
 * 期间没有任何中间信号 —— 画一根停在 20% 的进度条，用户只会以为它卡死了。
 *
 * 同样地，**B站 的审核没有进度可读**：它不是一个带百分比的队列。界面上如实说「审核中」，
 * 不编一个看起来精确的数字（那正是本仓库反复避免的那类谎报）。
 *
 * 所以这里给出的是「到哪一步了 + 这一步在等什么 + 这一步用掉了多久」，三段信息各自都是真的。
 * 第三段**只在我们自己的那四段里计时**：审核期给的是定值（上传用时 = 终点 − 起点），
 * 不替平台计时 —— 一个在「上传进度」标题下不断往上跳的数字会被读成「还在传」（见 ai/ui/008）。
 */

export type UploadStageKey = "queued" | "transfer" | "submit" | "verify";

/** 四个节点。**节点数固定**：界面上是一条四段的轨迹，跨条目的聚合也按它算。 */
export const UPLOAD_STAGES = [
  { key: "queued", label: "排队" },
  { key: "transfer", label: "传输" },
  { key: "submit", label: "提交" },
  { key: "verify", label: "核对" }
] as const satisfies readonly { key: UploadStageKey; label: string }[];

export interface UploadStageView {
  /** 当前节点下标（0 基）。 */
  index: number;
  key: UploadStageKey;
  /** 一句话说清「现在在等什么」。 */
  detail: string;
  /** 已经走完这四个节点（已发布 / 审核中 / 待核对）。此时不该再画「进行中」。 */
  settled: boolean;
  /** 走到这里断了（失败 / 被退回）。 */
  broken: boolean;
}

/**
 * 一条稿件 → 它现在处在哪一段、在等什么。
 *
 * `uploading` 内部的三个子状态来自服务端那三个闸门值，**顺序与成因都是服务端的**：
 * 0.2 = `pre_upload` 出现（开始准备）、0.6 = 文件传完、0.95 = 投稿接口回成功。
 * 这里只是把它们翻成人话，不新增判断。
 */
export function uploadStageView(upload: { status: BiliUploadStatus; progress: number }): UploadStageView {
  switch (upload.status) {
    case "queued":
      return { index: 0, key: "queued", detail: "排队中，等前面的上传跑完", settled: false, broken: false };
    case "uploading":
      if (upload.progress >= 0.6) {
        return { index: 2, key: "submit", detail: "文件传完了，正在提交投稿信息", settled: false, broken: false };
      }
      return upload.progress >= 0.2
        ? { index: 1, key: "transfer", detail: "正在传输视频文件", settled: false, broken: false }
        : { index: 1, key: "transfer", detail: "正在准备上传", settled: false, broken: false };
    case "submitted":
      return { index: 3, key: "verify", detail: "正在核对平台上的稿件", settled: false, broken: false };
    case "published":
      return { index: 3, key: "verify", detail: "已发布", settled: true, broken: false };
    case "reviewing":
      // 审核这一步没有任何可读的进度，所以只给结论，不给数字。
      return { index: 3, key: "verify", detail: "已提交，B 站 审核中", settled: true, broken: false };
    case "unverified":
      return { index: 3, key: "verify", detail: "已提交，但平台列表里还没查到", settled: true, broken: false };
    case "rejected":
      return { index: 3, key: "verify", detail: "B 站 审核没通过", settled: true, broken: true };
    case "failed":
      return { index: 3, key: "verify", detail: "这一步没成", settled: true, broken: true };
  }
}

export interface JobUploadProgressView {
  /** 总条数（单视频为 1）。 */
  total: number;
  /** 正在跑（或刚停下）的那一条的序号，1 基。 */
  current: number;
  /** 那一条的标题，合集里用来点明「第几条」。 */
  title: string;
  /** 已经确认在平台上的条数。 */
  done: number;
  /** 这一条现在的阶段。 */
  stage: UploadStageView;
  /** 还在跑 → 秒表按秒推进。**跑完（含等审核）之后它必须是 false**。 */
  running: boolean;
  /** 这一次上传的起点（ISO）。秒表的起算点。 */
  startedAt: string | null;
  /** 这一次上传的终点（ISO）。跑完时才有 —— 定值时长由它与起点算。 */
  finishedAt: string | null;
  /** 还有没有需要用户等待的东西（在跑 / 审核中）。false 时整个进度块不渲染。 */
  waiting: boolean;
}

/**
 * 一个任务的全部稿件 → 一条「上传进度」要显示的东西。
 *
 * 选哪一条当「当前」：**在跑的那条优先**（合集按序号串行，取序号最小的那条最不会误导）；
 * 没有在跑的，就取第一条还没成功的（让「等核对 / 失败」也有话说）；全成功则取最后一条，
 * 此时 `waiting` 为 false，界面不渲染进度块。
 *
 * 返回 `null` 只在**一条记录都没有**时：那种情况界面本来就不该有 B站 区。
 */
export function jobUploadProgress(uploads: readonly BiliUpload[]): JobUploadProgressView | null {
  if (!uploads.length) return null;
  const ordered = [...uploads].sort((a, b) => a.index - b.index);
  const running = ordered.find((upload) => isInFlightUpload(upload.status)) ?? null;
  const pending = ordered.find((upload) => !isAcceptedUpload(upload.status)) ?? null;
  const current = running ?? pending ?? ordered[ordered.length - 1];
  const stage = uploadStageView(current);
  const done = ordered.filter((upload) => isAcceptedUpload(upload.status)).length;
  // 「还在等」只认两种：正在跑，或者已经提交、等平台审核。
  // 失败 / 待核对 / 已发布都属于「已经有结论了」—— 进度块不说话，明细行会说话。
  const waiting = Boolean(running) || current.status === "reviewing";
  return {
    total: ordered.length,
    current: ordered.indexOf(current) + 1,
    title: current.title,
    done,
    stage,
    running: Boolean(running),
    // **两个端点分开给**，不给一个「起点（在跑时）/ 终点（跑完时）」的双义值：
    // 同一个字段在不同档位代表不同时刻，读的人迟早会算错（见 ai/ui/008）。
    startedAt: current.started_at,
    // 还在跑时终点**必须为空**：库里那一列可能留着上一轮的时间，拿它算时长会得出 0 或负数，
    // 而这个值是「上传用时」，一个假的用时比不显示更糟。
    finishedAt: running ? null : current.finished_at,
    waiting
  };
}

/**
 * 「已用 1:23」/「上传用时 0:52」。`now` 由调用方按秒推进（见 `BiliUploadProgress`）。
 *
 * **只在上传这一段用 `now`。** 已经跑完的（含等审核）算出来的是一个**定值**：
 * 上传用时 = 终点 − 起点，与此刻几点无关。
 *
 * 为什么不能继续走：`reviewing` 时起点换成 `finished_at` 之后，那串数字量的是
 * **等平台审核的时间**，而这块东西标题叫「上传进度」。实测 Ep 125 那次上传只用了 52 秒，
 * 界面上却一路显示到「已提交 6:41:18」—— 用户据此判断「它还在传」。
 * 见 ai/ui/008。
 */
export function uploadElapsedLabel(view: JobUploadProgressView, now: number): string | null {
  // 走 `parseServerTime` 而不是 `Date.parse`：服务端存的是 UTC，而 `Date.parse` 会把
  // 不带偏移量的串按**本地**时间解析（本机 UTC+8 ⇒ 时长凭空多出 8 小时）。见 ai/bug-fix/020。
  const started = parseServerTime(view.startedAt);
  if (started === null) return null;
  if (view.running) {
    return `已用 ${formatClock(Math.max(0, Math.round((now - started) / 1000)))}`;
  }
  const finished = parseServerTime(view.finishedAt);
  if (finished === null) return null;
  return `上传用时 ${formatClock(Math.max(0, Math.round((finished - started) / 1000)))}`;
}
