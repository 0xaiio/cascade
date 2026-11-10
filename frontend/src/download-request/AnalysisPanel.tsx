import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { formatDuration } from "../shared/formatting";
import { formatSelectedQualitySize } from "../shared/quality";
import type { AnalyzeResponse, DownloadOptions } from "../contracts/types";
import {
  addIndices,
  areAllSelected,
  filterEntries,
  invertIndices,
  parseIndexRanges,
  removeIndices
} from "./playlistSelection";

/**
 * 解析结果面板：缩略图/标题/时长，单视频显示汇总，playlist 显示可勾选条目表。
 *
 * `selectedItems` 的**状态归调用方**（提交任务时要用同一份选择），这里只负责渲染与回调 ——
 * 面板自己存一份就会出现「勾了但提交的是旧的」。
 */
export function AnalysisPanel({
  analysis,
  options,
  selectedItems,
  setSelectedItems
}: {
  analysis: AnalyzeResponse;
  options: DownloadOptions;
  selectedItems: Set<number>;
  setSelectedItems: (items: Set<number>) => void;
}) {
  // 两个纯界面状态：范围输入与标题筛选。**选择本身仍然归调用方**（提交任务要用同一份）。
  const [rangeText, setRangeText] = useState("");
  const [filterText, setFilterText] = useState("");
  const [rangeNote, setRangeNote] = useState("");
  const [rangeProblems, setRangeProblems] = useState<string[]>([]);

  // 换一份解析结果（包括对同一个链接重新解析）就把这两项收回：它们是「这一份列表」的临时视图，
  // 留着会让新列表一进来就被上一次的筛选藏掉大半。`analysis` 每次解析都是新对象。
  useEffect(() => {
    setRangeText("");
    setFilterText("");
    setRangeNote("");
    setRangeProblems([]);
  }, [analysis]);

  // 批量动作只作用于「当前显示的行」；被筛掉的已勾选条目保持勾选，但不会被这些动作碰到。
  const visibleEntries = filterEntries(analysis.entries, filterText);
  const visibleIndices = visibleEntries.map((entry) => entry.index);
  const allVisibleSelected = areAllSelected(selectedItems, visibleIndices);
  const hiddenSelected = selectedItems.size - visibleEntries.filter((entry) => selectedItems.has(entry.index)).length;

  function toggle(index: number) {
    const next = new Set(selectedItems);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    setSelectedItems(next);
  }

  function toggleAll() {
    setSelectedItems(
      allVisibleSelected
        ? removeIndices(selectedItems, visibleIndices)
        : addIndices(selectedItems, visibleIndices)
    );
    setRangeNote("");
  }

  function invertVisible() {
    setSelectedItems(invertIndices(selectedItems, visibleIndices));
    setRangeNote("");
  }

  function applyRange() {
    const parsed = parseIndexRanges(rangeText, analysis.entries.length);
    if (parsed.problems.length > 0) {
      setRangeProblems(parsed.problems);
      setRangeNote("");
      return;
    }
    setRangeProblems([]);
    const shown = new Set(visibleIndices);
    const targets = parsed.indices.filter((index) => shown.has(index));
    const skipped = parsed.indices.length - targets.length;
    // 追加语义：已勾选的一条都不取消。选错了用「清空选择」。
    setSelectedItems(addIndices(selectedItems, targets));
    setRangeNote(
      targets.length === 0
        ? "范围内没有当前显示的条目（先清除筛选再试）"
        : `已加入 ${targets.length} 条${skipped > 0 ? `；${skipped} 条被筛选隐藏，未加入` : ""}（不会取消已选）`
    );
  }

  return (
    <section className="panel analysis-panel">
      <div className="media-heading">
        {analysis.thumbnail ? <img src={analysis.thumbnail} alt="" /> : <div className="thumbnail-placeholder" />}
        <div>
          <h2>{analysis.title}</h2>
          <p>{analysis.is_playlist ? `${analysis.entries.length} 个视频` : "单视频"}</p>
          <p className="quality-size-line">当前选择：{formatSelectedQualitySize(analysis, options)}</p>
        </div>
      </div>

      {/* E-01：空间结论由服务端算好（数字、单位、下一步都在 message 里），这里只按档位配色。
          解析阶段它只是提示 —— 真正的阻断发生在 POST /api/jobs，那时才知道用户选了哪个清晰度。 */}
      {analysis.disk_budget ? (
        <p
          className={`disk-budget-hint is-${analysis.disk_budget.status}`}
          data-status={analysis.disk_budget.status}
        >
          {analysis.disk_budget.message}
        </p>
      ) : null}

      {analysis.is_playlist ? (
        <div className="table-wrap">
          <div className="playlist-toolbar">
            <div className="playlist-toolbar-row">
              <button type="button" className="ghost-button" onClick={toggleAll}>
                {allVisibleSelected ? "清空选择" : "全选"}
              </button>
              <button type="button" className="ghost-button" onClick={invertVisible}>
                反选
              </button>
              <span className="playlist-range">
                <label className="playlist-field-label" htmlFor="playlist-range">
                  范围
                </label>
                <input
                  id="playlist-range"
                  className="playlist-range-input"
                  value={rangeText}
                  placeholder="1-20,35,40-"
                  onChange={(event) => {
                    setRangeText(event.target.value);
                    setRangeProblems([]);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      applyRange();
                    }
                  }}
                />
                <button
                  type="button"
                  className="ghost-button"
                  // 追加不是替换 —— 点之前就该知道，别等勾错了才发现。
                  title="把范围内的条目加入已选（不取消已勾选的）"
                  aria-label="选中范围内的条目"
                  onClick={applyRange}
                >
                  选中
                </button>
              </span>
            </div>
            <div className="playlist-toolbar-row">
              <span className="playlist-filter">
                {/* 可见标签不能省：placeholder 在输入之后就不见了，只剩一个来历不明的方框。 */}
                <label className="playlist-field-label" htmlFor="playlist-filter">
                  筛选
                </label>
                <input
                  id="playlist-filter"
                  className="playlist-filter-input"
                  aria-label="按标题筛选条目"
                  placeholder="按标题筛选"
                  value={filterText}
                  onChange={(event) => setFilterText(event.target.value)}
                />
              </span>
              {filterText && (
                <button type="button" className="ghost-button" onClick={() => setFilterText("")}>
                  清除筛选
                </button>
              )}
              <span className="playlist-count">
                已选 {selectedItems.size} / {analysis.entries.length}
                {hiddenSelected > 0 ? `（其中 ${hiddenSelected} 条被筛选隐藏）` : ""}
              </span>
            </div>
            {rangeProblems.length > 0 && (
              <p className="playlist-error" role="alert">
                {rangeProblems.join("；")}
              </p>
            )}
            {rangeNote && (
              <p className="playlist-note" role="status">
                {rangeNote}
              </p>
            )}
          </div>
          <table>
            <thead>
              <tr>
                <th>选择</th>
                <th>#</th>
                <th>标题</th>
                <th>时长</th>
              </tr>
            </thead>
            <tbody>
              {visibleEntries.map((entry) => (
                <tr key={entry.index}>
                  <td>
                    <input
                      aria-label={`选择 ${entry.title}`}
                      type="checkbox"
                      checked={selectedItems.has(entry.index)}
                      onChange={() => toggle(entry.index)}
                    />
                  </td>
                  <td>{entry.index}</td>
                  <td>{entry.title}</td>
                  <td>{formatDuration(entry.duration)}</td>
                </tr>
              ))}
              {visibleEntries.length === 0 && (
                <tr>
                  <td colSpan={4} className="playlist-empty">
                    没有标题包含「{filterText.trim()}」的条目；筛选只影响显示，不会改变已选。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="single-summary">
          <FileText size={18} />
          <span>{formatDuration(analysis.duration)} · {analysis.formats.length} 个格式 · {analysis.subtitles.length} 种字幕</span>
        </div>
      )}
    </section>
  );
}
