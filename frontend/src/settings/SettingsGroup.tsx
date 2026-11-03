import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * 设置面板里的一个功能分区。
 *
 * 为什么不是普通的 `<div>`：分组不只是画一道线。读屏用户要能按「下载与存储 / 代理与网络 /
 * 完成提醒 / B站 上传 / 运行环境」逐区跳转，并且知道某个控件属于哪一区 ——
 * `<section aria-labelledby>` 给到的 `role="region"` 正好是这件事，`<div>` 给不了。
 * `settings-groups.test.tsx` 用 `getByRole("region", { name })` 钉住它，不靠 class 名。
 *
 * 为什么标题是 `h3`：面板标题（`h2`「设置」）下面的一级就是分区。标题级别同时也是
 * 视觉层级 —— 两者不该各说各话。
 *
 * 边界靠三样东西一起成立：**标题、分隔线、间距差**。少了间距差，标题看起来只是列表里的
 * 一行；少了分隔线，长面板滚到中间时分不清自己还在不在同一区（规则见 `styles.css`
 * 的「设置面板的功能分区」段与 [ai/ui/010](../../../ai/ui/010-settings-panel-is-a-flat-list.md)）。
 */
export function SettingsGroup({
  id,
  title,
  icon: Icon,
  children
}: {
  /** 只在 DOM 里用：标题 id 由它生成，测试与读屏都靠 `aria-labelledby` 找标题。 */
  id: string;
  title: string;
  icon: LucideIcon;
  children: ReactNode;
}) {
  const headingId = `settings-group-${id}`;
  return (
    <section className="settings-group" aria-labelledby={headingId}>
      <h3 className="settings-group-title" id={headingId}>
        <Icon size={15} aria-hidden="true" />
        {title}
      </h3>
      <div className="settings-group-body">{children}</div>
    </section>
  );
}
