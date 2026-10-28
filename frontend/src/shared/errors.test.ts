import { describe, expect, test } from "vitest";
import { errorMessage } from "./errors";

/**
 * 异常 → 一句可显示的话。
 *
 * 这段判断在收口之前被重写过十几次，每处的语义都略有不同（有的在 `message` 为空时兜到
 * fallback，有的不兜）。所以这里测的重点不是「能不能取出 message」，而是**边界上的口径**：
 * 哪些形状算「有 message」，哪些必须走 fallback。
 */
describe("errorMessage", () => {
  test("Error 实例取它自己的 message", () => {
    expect(errorMessage(new Error("上传失败了"), "兜底")).toBe("上传失败了");
  });

  test("子类（TypeError / 自定义错误）同样按 Error 处理", () => {
    expect(errorMessage(new TypeError("不是函数"), "兜底")).toBe("不是函数");
    class ApiError extends Error {}
    expect(errorMessage(new ApiError("API 报错"), "兜底")).toBe("API 报错");
  });

  test("不是 Error 的一律走 fallback", () => {
    expect(errorMessage("一个字符串", "兜底")).toBe("兜底");
    expect(errorMessage(null, "兜底")).toBe("兜底");
    expect(errorMessage(undefined, "兜底")).toBe("兜底");
    expect(errorMessage({ message: "长得像 Error 但并不是" }, "兜底")).toBe("兜底");
    expect(errorMessage(42, "兜底")).toBe("兜底");
  });

  test("message 为空串时**不**兜到 fallback —— 空串也是一条「服务端明确说了」的信息", () => {
    // 这一条是口径本身：改成「空就兜底」会让上层看到的文案在两种情况下漂移，
    // 而调用方无法预知。要改这条口径，先改这条用例并想清楚为什么。
    expect(errorMessage(new Error(""), "兜底")).toBe("");
  });
});
