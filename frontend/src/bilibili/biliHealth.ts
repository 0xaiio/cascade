import type { BiliStatus } from "../contracts/types";

/**
 * 「重新体检」的**执行策略**：带超时地探一次账号，超时就退回离线结论。
 *
 * 为什么这一步值得单独成一个纯模块：`GET /api/bilibili/status?probe_account=true` 会**真的往
 * B站 发一次请求**，而本机直连 `member.bilibili.com` 时域名解析能卡几十秒（实测 40s+；
 * `socket` 超时管不到 DNS）。没有超时的话，点按钮之后界面一动不动 —— 用户只能认为它坏了。
 *
 * 组件里只留文案，判据（探多久、超时了退到哪、要不要再探一次）都在这里，于是可以用
 * 二十毫秒的假时钟把它测透，不必在界面测试里摆弄假定时器。
 */

/** 账号探测的兜底时限。取 12 秒，与「按需读合集列表」那条兜底同一个量级。 */
export const CHECK_TIMEOUT_MS = 12_000;

/** 超时哨兵。它必须与网络错误区分开：前者退回离线体检，后者才是失败。 */
const TIMEOUT = Symbol("bili-health-timeout");

export interface HealthCheckResult {
  status: BiliStatus;
  /** true = 账号探测没在时限内回来，`status` 是**离线**结论（工具/登录文件的存在性）。 */
  timedOut: boolean;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(TIMEOUT), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

/**
 * 先探账号；超时就退回离线。
 *
 * `loadProbe` 的**网络错误照旧抛出** —— 那是「真的读不到」，要由界面报错；
 * 只有「迟迟不回」才降级成离线结论，因为离线那一半（投稿工具、登录文件）本来就不需要网络。
 */
export async function runHealthCheck(
  loadProbe: () => Promise<BiliStatus>,
  loadOffline: () => Promise<BiliStatus>,
  timeoutMs: number = CHECK_TIMEOUT_MS
): Promise<HealthCheckResult> {
  try {
    return { status: await withTimeout(loadProbe(), timeoutMs), timedOut: false };
  } catch (error) {
    if (error !== TIMEOUT) throw error;
    return { status: await loadOffline(), timedOut: true };
  }
}
