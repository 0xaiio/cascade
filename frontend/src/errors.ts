/**
 * 异常 → 一句可显示的话。**唯一实现。**
 *
 * 这段判断在本仓库里出现过十几次（`err instanceof Error ? err.message : fallback`），
 * 每处都重写一遍的代价不是行数，而是**语义会各自漂移**：有的地方会在 `message` 为空时
 * 兜到 fallback，有的不会。收成一个函数后，「什么算一条可显示的 message」只有一处定义。
 *
 * 注意它**不看 `ApiError`**：B站 上传那条「把 `advice` 拼进去」的规则只在 `App.biliMessage`
 * 里，因为只有那条链路会带上 `advice`（见 `types.ApiErrorDetail`）。
 *
 * **适用范围目前是 B站 上传那几个文件。** 仓库其余地方（`CookieSection` / `ProxySection` /
 * `JobQueue` / `App` 的解析流程）仍有各自的一份，那属于 `main` 的既有代码 ——
 * 本次只把新增功能这一片收口，避免把 diff 扩到与本次改动无关的文件上。
 */
export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
