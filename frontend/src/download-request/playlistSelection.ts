/**
 * playlist 条目的**选择**逻辑（纯函数）。
 *
 * 面板里能做的批量动作有四个：全选 / 清空、反选、按序号范围选中、按标题筛选（只影响显示）。
 * 它们共用同一份判据，所以抽到这里 —— 组件里再写一遍就会在某一处漏掉「只作用于显示的行」。
 *
 * 两条语义在这里定死（界面上的提示语必须与它们一致）：
 *
 * 1. **范围是「追加」不是「替换」**：已勾选的条目不会被范围取消。选错了用「清空选择」。
 *    「替换」看起来更符合「我选了 20–60」的直觉，但用户接着想再补一段时就只能重来；
 *    而追加最坏的结果是多勾几条，再点一次就取消，代价小得多。
 * 2. **批量动作只作用于「当前显示的行」**。筛选改变的是工作集；被筛掉的已勾选条目保持勾选、
 *    但不会被全选/反选/范围碰到。界面必须把「已选 N（其中 M 条被筛选隐藏）」写出来，
 *    否则数字与可见行对不上，用户会以为选择丢了。
 *
 * 序号一律指条目表里的 `#` 列（`VideoEntry.index`，从 1 开始），不是它在当前筛选结果里的位置。
 */

/** 范围输入解析结果。`problems` 非空表示**这次输入没有被应用**。 */
export type RangeParse = {
  indices: number[];
  problems: string[];
};

export function parseIndexRanges(input: string, total: number): RangeParse {
  const tokens = input.split(/[,，\s]+/).filter(Boolean);
  if (tokens.length === 0) {
    return { indices: [], problems: ["先填一个范围，例如 1-20,35,40-"] };
  }
  if (!Number.isFinite(total) || total < 1) {
    return { indices: [], problems: ["这个合集没有可选的条目"] };
  }

  const collected = new Set<number>();
  const problems: string[] = [];

  for (const token of tokens) {
    const single = /^(\d+)$/.exec(token);
    if (single) {
      const value = Number(single[1]);
      if (value < 1 || value > total) problems.push(`「${token}」超出范围（共 ${total} 条）`);
      else collected.add(value);
      continue;
    }

    const span = /^(\d*)-(\d*)$/.exec(token);
    if (!span || (!span[1] && !span[2])) {
      problems.push(`看不懂「${token}」，用 1-20、35、40- 这样的写法`);
      continue;
    }

    // `-20` = 1..20，`20-` = 20..total，两边都空已经在上面挡掉。
    const start = span[1] ? Number(span[1]) : 1;
    const end = span[2] ? Number(span[2]) : total;
    if (start > end) {
      problems.push(`「${token}」的起点大于终点`);
      continue;
    }
    if (end < 1 || start > total) {
      problems.push(`「${token}」超出范围（共 ${total} 条）`);
      continue;
    }
    for (let value = Math.max(1, start); value <= Math.min(total, end); value += 1) {
      collected.add(value);
    }
  }

  return { indices: [...collected].sort((a, b) => a - b), problems };
}

/** 标题子串筛选（不区分大小写、前后空格忽略）。空查询返回原数组。 */
export function filterEntries<T extends { title: string }>(entries: T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.filter((entry) => entry.title.toLowerCase().includes(needle));
}

/** 把一批序号**加入**已选（不取消任何已勾选项）。 */
export function addIndices(selected: Set<number>, indices: number[]): Set<number> {
  const next = new Set(selected);
  for (const index of indices) next.add(index);
  return next;
}

/** 从已选里去掉一批序号。 */
export function removeIndices(selected: Set<number>, indices: number[]): Set<number> {
  const next = new Set(selected);
  for (const index of indices) next.delete(index);
  return next;
}

/** 反选：只在给定的（可见）序号里翻转，其余保持原样。 */
export function invertIndices(selected: Set<number>, indices: number[]): Set<number> {
  const next = new Set(selected);
  for (const index of indices) {
    if (next.has(index)) next.delete(index);
    else next.add(index);
  }
  return next;
}

/** 给定序号是否**全部**已选。空集合返回 false（「全选」按钮此时不该显示成「清空」）。 */
export function areAllSelected(selected: Set<number>, indices: number[]): boolean {
  if (indices.length === 0) return false;
  return indices.every((index) => selected.has(index));
}
