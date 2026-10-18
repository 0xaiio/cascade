import { useCallback, useEffect, useRef, useState } from "react";
import type { CompletionNotice, CompletionNotification, Job } from "./types";

/**
 * 「任务跑完了」的提醒：判定（谁该被提醒、提醒什么）**与呈现分离**。
 *
 * 声音是 Web Audio 现场合成的，没有一个音频文件要跟着仓库走；浮层是普通 DOM，
 * 位置与动画都在 `styles.css` 里。这个模块只负责：
 *
 * 1. 什么样的任务状态算「跑完了」；
 * 2. 一条提醒该写成什么话；
 * 3. 浮层队列的生命周期（自动缩回、悬停暂停、后台标签页不吞掉）。
 *
 * 三件都刻意做成**可注入的纯逻辑**：jsdom 里没有 `AudioContext`、也不做布局，
 * 只有把判定抽出来才测得到。
 */

/**
 * 会触发提醒的终态。
 *
 * `cancelled` **不在里面**：取消是用户自己点的，他自己知道，再弹一条是把「我刚做过的事」
 * 播报一遍。`paused` 也不是终态 —— 它还会被重启。
 */
export const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed"]);

/** 一条浮层默认挂多久（毫秒）。足够读完两行字，又不至于一直占着右下角。 */
export const TOAST_TTL_MS = 6_000;

/** 同时最多挂几条。再多就叠成一列，反而盖住任务中心右下角的操作按钮。 */
export const MAX_VISIBLE_TOASTS = 3;

/** 设置项的下拉选项。顺序即界面顺序：`off` 放最后会让「关掉」看起来像一个高级选项。 */
export const COMPLETION_NOTIFICATION_OPTIONS: Array<{
  value: CompletionNotification;
  label: string;
  hint: string;
}> = [
  { value: "off", label: "不提醒", hint: "任务跑完不做任何提示" },
  { value: "sound", label: "提示音", hint: "播放一声「叮」，后台标签页也听得见" },
  { value: "toast", label: "右下角弹窗", hint: "右下角浮出一条通知，几秒后自动缩回" },
  { value: "both", label: "提示音 + 弹窗", hint: "两者都要（默认）" }
];

export function wantsSound(mode: CompletionNotification): boolean {
  return mode === "sound" || mode === "both";
}

export function wantsToast(mode: CompletionNotification): boolean {
  return mode === "toast" || mode === "both";
}

/**
 * 把一条跑完的任务写成一句人话。
 *
 * 「失败」那一支必须带上下一步（`可在任务中心查看原因`）：只说「下载失败」等于让用户
 * 自己去找原因，而原因本来就在这一条记录里 —— 这是本项目所有提示的统一口径。
 */
export function describeCompletion(job: Job): { tone: CompletionNotice["tone"]; title: string; body: string } {
  if (job.status === "failed") {
    return {
      tone: "bad",
      title: "下载失败",
      body: `《${job.title}》失败 ${job.failed_items} 项，可在任务中心查看原因。`
    };
  }
  if (job.failed_items > 0) {
    return {
      tone: "warn",
      title: "下载完成（有失败条目）",
      body: `《${job.title}》完成 ${job.completed_items}/${job.total_items}，失败 ${job.failed_items} 项。`
    };
  }
  return {
    tone: "ok",
    title: "下载完成",
    body: `《${job.title}》已下载 ${job.completed_items}/${job.total_items} 项。`
  };
}

/** 设置里的「试听」用的样例。文案必须写明它是样例，否则会被当成真有一条任务跑完了。 */
export function previewNotice(): { tone: CompletionNotice["tone"]; title: string; body: string } {
  return {
    tone: "ok",
    title: "这是一条样例通知",
    body: "任务跑完时，右下角就是这样的；提示音也已经播过了。"
  };
}

/* ------------------------------------------------------------------ 提示音 */

/** 一声「叮」的两个音：C6 → G6。第二个音轻一点、晚一点，听起来才是「叮咚」而不是「哔」。 */
const CHIME_NOTES = [
  { frequency: 1046.5, delay: 0, duration: 0.5, peak: 0.16 },
  { frequency: 1568.0, delay: 0.11, duration: 0.55, peak: 0.11 }
];
const CHIME_ATTACK_SECONDS = 0.008;

/**
 * 播放结果。**必须区分这三种**，否则「用户说没听见」无从查起：
 * - `played`：音频上下文在跑，音符已排进去；
 * - `blocked`：浏览器还没放行音频（页面没有过用户手势），排进去也不会出声；
 * - `unsupported`：这个环境没有 Web Audio（jsdom、很老的浏览器）。
 */
export type ChimeOutcome = "played" | "blocked" | "unsupported";

function defaultAudioContext(): AudioContext | null {
  if (typeof AudioContext === "undefined") return null;
  return new AudioContext();
}

export class ChimePlayer {
  private context: AudioContext | null = null;

  /** `createContext` 可注入：测试里给一个替身即可，不需要真的出声。 */
  constructor(private readonly createContext: () => AudioContext | null = defaultAudioContext) {}

  /**
   * 在一个**用户手势里**调一次，把音频上下文激活。
   *
   * 浏览器只允许在手势之后启动音频；等到任务跑完再去 `resume()` 已经没有手势了，
   * 那时多半会被拒。所以「试听」按钮和「加入下载队列」都会先调它 ——
   * 后者是用户**必然**会点的一步，等于顺手把音频解锁了。
   */
  prime(): ChimeOutcome {
    const context = this.ensureContext();
    if (!context) return "unsupported";
    if (context.state === "suspended") void context.resume();
    return context.state === "running" ? "played" : "blocked";
  }

  play(): ChimeOutcome {
    const context = this.ensureContext();
    if (!context) return "unsupported";
    if (context.state === "suspended") void context.resume();
    this.schedule(context);
    return context.state === "running" ? "played" : "blocked";
  }

  private ensureContext(): AudioContext | null {
    if (!this.context) this.context = this.createContext();
    return this.context;
  }

  private schedule(context: AudioContext): void {
    const now = context.currentTime;
    for (const note of CHIME_NOTES) {
      const start = now + note.delay;
      const end = start + note.duration;
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(note.frequency, start);
      // 指数衰减不能用 0（`exponentialRampToValueAtTime` 会抛），收尾停在一个听不见的值上。
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(note.peak, start + CHIME_ATTACK_SECONDS);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start);
      oscillator.stop(end + 0.02);
    }
  }
}

/* -------------------------------------------------------------- 浮层队列 */

/**
 * 右下角浮层的生命周期。
 *
 * 两条容易被改坏的规则：
 * 1. **标签页不可见时不倒计时**。下载跑十几分钟是常态，用户多半切走了；
 *    这时开始倒计时，等于让提醒在他看不见的时候自己消失 —— 浮层模式就白选了。
 *    所以后台只记「还剩多久」，等 `visibilitychange` 说可见了再接着走。
 * 2. **悬停暂停**。鼠标停在上面时用户正在读，缩回去是纯粹的打扰。
 */
export function useCompletionToasts(ttlMs: number = TOAST_TTL_MS) {
  const [toasts, setToasts] = useState<CompletionNotice[]>([]);
  const timers = useRef(new Map<string, number>());
  const remaining = useRef(new Map<string, number>());
  const startedAt = useRef(new Map<string, number>());
  const seq = useRef(0);

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
    remaining.current.delete(id);
    startedAt.current.delete(id);
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const arm = useCallback(
    (id: string, ms: number) => {
      const timer = timers.current.get(id);
      if (timer !== undefined) window.clearTimeout(timer);
      timers.current.delete(id);
      remaining.current.set(id, ms);
      if (document.hidden) return;
      startedAt.current.set(id, Date.now());
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), ms)
      );
    },
    [dismiss]
  );

  const push = useCallback(
    (notice: Omit<CompletionNotice, "id">) => {
      seq.current += 1;
      const id = `completion-${seq.current}`;
      setToasts((current) => [...current, { ...notice, id }]);
      arm(id, ttlMs);
      return id;
    },
    [arm, ttlMs]
  );

  /** 鼠标移入：把剩余时间存下来并停表。 */
  const pause = useCallback(
    (id: string) => {
      const timer = timers.current.get(id);
      const started = startedAt.current.get(id);
      if (timer === undefined || started === undefined) return;
      window.clearTimeout(timer);
      timers.current.delete(id);
      const left = (remaining.current.get(id) ?? ttlMs) - (Date.now() - started);
      remaining.current.set(id, Math.max(500, left));
      startedAt.current.delete(id);
    },
    [ttlMs]
  );

  /** 鼠标移出：接着走剩下的时间（不足 500ms 也按 500ms 给，避免刚移开就消失）。 */
  const resume = useCallback(
    (id: string) => {
      if (timers.current.has(id)) return;
      if (!remaining.current.has(id)) return;
      arm(id, remaining.current.get(id) ?? ttlMs);
    },
    [arm]
  );

  // 超出上限时丢掉最旧的几条。放在 effect 里做，是为了让「清定时器」只在渲染之后发生一次
  // —— 写在 `setToasts` 的更新函数里会在 StrictMode 下被调用两次。
  useEffect(() => {
    if (toasts.length <= MAX_VISIBLE_TOASTS) return;
    for (const dropped of toasts.slice(0, toasts.length - MAX_VISIBLE_TOASTS)) {
      dismiss(dropped.id);
    }
  }, [toasts, dismiss]);

  // 页面重新可见：把那些「因为看不见而没开始倒计时」的浮层接上。
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden) return;
      for (const id of Array.from(remaining.current.keys())) {
        if (!timers.current.has(id)) arm(id, remaining.current.get(id) ?? ttlMs);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [arm, ttlMs]);

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) window.clearTimeout(timer);
      timers.current.clear();
    },
    []
  );

  return { toasts, push, dismiss, pause, resume };
}
