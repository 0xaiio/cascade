import { describe, expect, test } from "vitest";
import { formatMediaDuration } from "./formatting";

describe("formatMediaDuration", () => {
  test("always renders 时:分:秒 so a duration never looks like elapsed time", () => {
    expect(formatMediaDuration(185)).toBe("0:03:05");
    expect(formatMediaDuration(3725)).toBe("1:02:05");
    expect(formatMediaDuration(3600 * 10)).toBe("10:00:00");
  });

  test("floors fractional seconds instead of rounding up past the real length", () => {
    expect(formatMediaDuration(59.9)).toBe("0:00:59");
  });

  test("treats a missing or unusable duration as unknown instead of 0:00:00", () => {
    expect(formatMediaDuration(null)).toBe("未知");
    expect(formatMediaDuration(0)).toBe("未知");
    expect(formatMediaDuration(Number.NaN)).toBe("未知");
  });
});
