export type DownloadMode = "video_subtitles" | "video_only" | "subtitles_only";
/** 下载选项里的字幕**来源偏好**。 */
export type SubtitleSource = "human" | "auto" | "both";
/** 一条字幕**实际**的来源结论。与 `SubtitleSource` 不是一回事：那是「想下哪一类」，这是「下到的是哪一类」。 */
export type SubtitleOrigin = "human" | "auto" | "none";
export type SubtitleFormat = "best" | "srt" | "vtt";
export type JobBatchAction = "pause" | "restart" | "delete";

/**
 * 「打开字幕」用哪种方式：`default` = 交给系统的文件关联；`chooser` = 让系统弹「打开方式」窗口。
 *
 * 两者**都不表示具体程序** —— 用哪个查看器由系统决定（用户选过一次之后系统会记住），
 * 前端与后端都不维护编辑器清单。
 */
export type SubtitleOpenMode = "default" | "chooser";

/**
 * 任务跑到终态时怎么提醒。取值与服务端 `schemas.CompletionNotification` 一一对应。
 *
 * `off` = 不提醒；`sound` = 提示音；`toast` = 右下角浮层（会自动缩回）；`both` = 两者都要。
 * 提醒**发生在浏览器里**（Web Audio 合成的声音 + DOM 浮层），后端只负责存偏好。
 */
export type CompletionNotification = "off" | "sound" | "toast" | "both";

export interface FormatOption {
  format_id: string;
  label: string;
  height: number | null;
  ext: string | null;
  fps: number | null;
  filesize: number | null;
}

export interface SubtitleOption {
  language: string;
  name: string | null;
  formats: string[];
}

export interface VideoEntry {
  index: number;
  id: string | null;
  title: string;
  url: string;
  duration: number | null;
  thumbnail: string | null;
}

export interface AnalyzeResponse {
  url: string;
  title: string;
  is_playlist: boolean;
  duration: number | null;
  thumbnail: string | null;
  entries: VideoEntry[];
  formats: FormatOption[];
  subtitles: SubtitleOption[];
  automatic_subtitles: SubtitleOption[];
  ffmpeg: Record<string, boolean>;
}

export interface DownloadOptions {
  mode: DownloadMode;
  resolution: string;
  format_id?: string | null;
  subtitle_languages: string[];
  subtitle_source: SubtitleSource;
  subtitle_format: SubtitleFormat;
  playlist_items: number[] | null;
  write_metadata: boolean;
  write_thumbnail: boolean;
  skip_existing: boolean;
  speed_limit_kbps: number | null;
  retries: number;
  notify_on_complete: boolean;
}

/**
 * 一条「任务跑完了」的提醒。
 *
 * `tone` 决定颜色：`ok` 成功、`warn` 部分成功、`bad` 失败。**不新造第四种** ——
 * 每多一种颜色就多一处「这个状态到底是什么色」的口头约定。
 */
export interface CompletionNotice {
  id: string;
  tone: "ok" | "warn" | "bad";
  title: string;
  body: string;
}

export interface JobItem {
  id: string;
  job_id: string;
  source_url: string;
  title: string;
  index: number;
  status: string;
  progress: number;
  downloaded_bytes: number | null;
  total_bytes: number | null;
  /** 视频时长（秒）。建任务时从解析结果写入；平台没给时长时为 `null`（界面显示「时长 未知」）。 */
  duration: number | null;
  speed: number | null;
  eta: number | null;
  output_path: string | null;
  actual_width: number | null;
  actual_height: number | null;
  actual_format: string | null;
  requested_resolution: string | null;
  fallback_resolution: string | null;
  fallback_reason: string | null;
  resolution_fallback: ResolutionFallback | null;
  /**
   * 下载到的字幕的语言族（`"en"` / `"zh"`）与来源（`"human"` 人工 / `"auto"` 自动生成 /
   * `"none"` 没有可用字幕）。**`null` 表示还没有字幕结论**（任务刚建、还没跑完），
   * 与 `subtitle_source === "none"`（跑过一次，结论是没有）不是一回事。
   */
  subtitle_language: string | null;
  subtitle_source: SubtitleOrigin | null;
  /**
   * 已下载到磁盘的字幕文件；`null` = 这条没有可用字幕。
   *
   * 与 `output_path` 一样是**后端算出来的**（记录值优先，其次看视频旁边有没有同名字幕），
   * 前端只拿它决定「打开字幕」按钮是否可点，不拼路径、不自己猜语言后缀。
   */
  subtitle_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
}

export interface Job {
  id: string;
  url: string;
  title: string;
  status: string;
  progress: number;
  speed: number | null;
  eta: number | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  current_item_title: string | null;
  error: string | null;
  download_dir: string | null;
  actual_resolution: string | null;
  actual_format: string | null;
  resolution_fallback: ResolutionFallback | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
  /** 创建这个任务时勾没勾「完成后通知」。由服务端从 `options_json` 派生 —— 刷新页面后前端仍然拿得到。 */
  notify_on_complete: boolean;
  items: JobItem[];
}

export interface JobBatchActionResponse {
  affected_job_ids: string[];
  jobs: Job[];
}

export interface DeleteJobItemsResponse {
  deleted_item_ids: string[];
  job_deleted: boolean;
  job: Job | null;
}

export interface Settings {
  download_dir: string;
  default_concurrency: number;
  default_subtitle_languages: string[];
  default_resolution: string;
  default_speed_limit_kbps: number | null;
  default_retries: number;
  aria2c_connections: number;
  proxy: string | null;
  proxy_source: "setting" | "system" | "environment" | "direct" | "none";
  proxy_effective: string | null;
  cookies_enabled: boolean;
  ffmpeg: Record<string, boolean>;
  /** 任务跑到终态时怎么提醒。`off` 时前端一个都不发。 */
  completion_notification: CompletionNotification;
}

export interface CookieStatus {
  enabled: boolean;
  filename: string | null;
  source?: "none" | "file" | "browser";
  browser?: string | null;
  imported_count?: number | null;
}

export interface ProxyTestResult {
  ok: boolean;
  source: Settings["proxy_source"];
  proxy: string | null;
  probe_url: string;
  http_status: number | null;
  elapsed_ms: number;
  bytes_read: number;
  error: string | null;
  summary: string;
  next_steps: string[];
}

export interface EnvironmentRisk {
  name: string;
  value: string;
  reason: string;
}

export interface Diagnostics {
  cookies_enabled: boolean;
  dependencies: Record<string, boolean | number | string | string[] | null>;
  log_file: string | null;
  sanitized_environment: EnvironmentRisk[];
}

export interface CookieHealth {
  present: boolean;
  path: string;
  filename: string | null;
  size_bytes: number;
  format_ok: boolean;
  format_note: string;
  cookie_count: number;
  domains: Record<string, number>;
  youtube_domain_count: number;
  auth_cookie_names: string[];
  missing_auth_cookie_names: string[];
  anonymous_only: boolean;
  expired_count: number;
  verdict: string;
  next_steps: string[];
  logged_in: boolean | null;
  logged_in_detail: string | null;
  checked_at: number;
}

export interface ApiErrorDetail {
  code?: string;
  browser?: string | null;
  message?: string;
  /**
   * 「下一步该怎么办」。B站 上传的前置条件失败（409）会带上它 ——
   * 只报结论不给动作的提示在验收口径里算未完成，所以界面必须把它显示出来。
   */
  advice?: string | null;
  raw_detail?: string | null;
}

export interface ResolutionFallback {
  requested_resolution: string;
  fallback_resolution: string;
  reason: string | null;
  restart_resolution: string | null;
  message: string;
}

/** B站 上传的界面状态档位（后端派生，前端只映射颜色）。 */
export type BiliBadge = "none" | "uploading" | "uploaded" | "unverified" | "partial" | "failed";
export type BiliBadgeTone = "idle" | "busy" | "ok" | "warn" | "bad";

/**
 * 稿件级上传状态。**取值与服务端 `models.BiliUploadStatus` 一一对应**，写在这里而不是
 * 让字段是裸 `string`，是为了让「拿一个状态去查表」这类代码在 `tsc` 阶段就被挡住 ——
 * 不然打错一个字母只有运行时才会发现（而那时它表现为「重试按钮不出现」这种静默现象）。
 */
export type BiliUploadStatus =
  | "queued"
  | "uploading"
  | "submitted"
  | "published"
  | "reviewing"
  | "rejected"
  | "unverified"
  | "failed";

/** 合集归集状态。`skipped` = 用户在投稿时选了「不归集」，是**终态**。 */
export type BiliSeasonStatus = "pending" | "grouping" | "grouped" | "failed" | "skipped";

/**
 * CC 字幕这一步的结论。**取值与服务端 `bili_subtitle` 的三档一一对应**。
 *
 * 它**不是**稿件状态的一档：`published` + `failed` 是合法组合（视频投上去了、字幕没传成），
 * 界面必须能把这两件事分开说。`skipped` 表示「这次没有字幕可传」，是常态而不是错误。
 */
export type BiliSubtitleState = "posted" | "skipped" | "failed";

/** 版权取值。与 B站 接口的 `copyright` 同值域，所以界面不需要翻译层。 */
export type BiliCopyright = "1" | "2";

/**
 * 「结论 + 下一步」。本功能所有缺项/失败提示都是这个形状 ——
 * 只报结论不给动作的提示在验收口径里算未完成。
 */
export interface BiliAdvice {
  message: string;
  advice: string;
}

export interface BiliPartition {
  tid: string;
  label: string;
}

export interface BiliAccount {
  mid: number | null;
  name: string;
}

export interface BiliTool {
  found: boolean;
  path: string | null;
  version: string | null;
  problem: string | null;
  advice: string | null;
}

export interface BiliLogin {
  present: boolean;
  cookie_count: number;
  names: string[];
  missing_required: string[];
  missing_recommended: string[];
  tracked_by_git: boolean;
  account: BiliAccount | null;
  account_checked: boolean;
}

export interface BiliStatus {
  ready: boolean;
  biliup: BiliTool;
  ffmpeg: BiliTool;
  login: BiliLogin;
  blockers: BiliAdvice[];
  partitions: BiliPartition[];
  limits: BiliLimits;
  gap_seconds: number;
  uploads_in_flight: number;
}

export interface BiliQrcode {
  qrcode_key: string;
  url: string;
  svg: string | null;
  note: string | null;
  expires_in: number;
}

export interface BiliLoginPoll {
  state: string;
  message: string;
  logged_in: boolean;
  account: BiliAccount | null;
}

export interface BiliMetadata {
  title: string;
  desc: string;
  tags: string;
  tid: string;
  /** 版权：`"1"` 自制 / `"2"` 转载。与 B站 接口同值域，所以不需要翻译层。 */
  copyright: BiliCopyright;
  /**
   * 转载来源链接。
   *
   * **合集模板上它恒为空**：合集里每条视频的来源是各自的原始链接，由服务端逐条渲染时填。
   * 界面因此只在**单视频**场景显示这个输入框。
   */
  source: string;
}

export interface BiliLimits {
  title: number;
  desc: number;
  tags: number;
  tag_count: number;
  tag_item: number;
  /** 报「某个标签太长」时回显多少个字。**不要在前端另写一个数** —— 那会让弹窗与服务端
   *  的 400 对同一件事给出两种截断结果。 */
  tag_preview: number;
  comment: number;
}

export interface BiliPrefillItem {
  index: number;
  item_id: string | null;
  source_title: string;
  source_url: string;
  title: string;
  desc: string;
  tags: string;
  ready: boolean;
  /** 这一条会不会带字幕（如 `en · 人工` / `无字幕`）。空串 = 还没有字幕结论。文案由服务端给。 */
  subtitle: string;
}

export interface BiliPrefill {
  job_id: string;
  job_title: string;
  kind: "single" | "collection";
  comment: string;
  metadata: BiliMetadata | null;
  template: BiliMetadata | null;
  items: BiliPrefillItem[];
  problems: string[];
  partitions: BiliPartition[];
  limits: BiliLimits;
  make_cover: boolean;
  make_comment: boolean;
  /** 合集简介的默认值（单视频恒为空）。界面没有这个输入框，提交时原样回传。 */
  season_desc: string;
  /**
   * 「归集方式」的初始选中项。**由服务端给**（合集 `new` / 单视频 `none`）：
   * 这个默认值按场景而不同，在前端按 `kind` 重推一遍就会有两份真相，
   * 而漂移的表现是「升级后投稿悄悄多出一批合集」，不会报错。
   */
  default_group_mode: BiliGroupMode;
}

export interface BiliUpload {
  id: string;
  job_id: string;
  item_id: string | null;
  index: number;
  title: string;
  desc: string;
  tags: string;
  tid: string;
  tid_label: string;
  /** 实际提交出去的版权（`"1"` 自制 / `"2"` 转载）与来源链接（提交时的快照）。
   *  界面显示「转载 / 自制」用它，也是「重试没有把稿件悄悄换成自制」可被核对的那一份证据。 */
  copyright: BiliCopyright | "";
  copyright_label: string;
  source: string;
  status: BiliUploadStatus;
  status_text: string;
  badge: BiliBadge;
  badge_text: string;
  badge_tone: BiliBadgeTone;
  progress: number;
  bvid: string | null;
  aid: number | null;
  video_url: string | null;
  cover_path: string | null;
  verified_state: string | null;
  verified_note: string | null;
  /**
   * CC 字幕这一步的结论。**它与 `status` 是两件事**：字幕没传上去时 `status` 照样是
   * `published`，界面要能同时说清这两件事，而不是二选一。
   */
  subtitle_state: BiliSubtitleState;
  subtitle_lan: string;
  subtitle_message: string;
  error: string | null;
  advice: string | null;
  attempt: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
}

export interface BiliSeason {
  id: string;
  job_id: string;
  title: string;
  desc: string;
  season_id: number | null;
  status: BiliSeasonStatus;
  status_text: string;
  upload_ids: string[];
  error: string | null;
  advice: string | null;
}

/**
 * 账号下的一个**已有合集**，供投稿界面下拉选择。
 *
 * 与 `BiliSeason` 是两回事：那个是「本机这次投稿归集到哪里」的记录，
 * 这个是「平台上已经存在的合集」，由 `GET /api/bilibili/seasons` 读回。
 */
export interface BiliSeasonOption {
  season_id: number;
  title: string;
  desc: string;
  cover_url: string;
  /** 该合集**目前**的视频条数。纯显示用，与「这次要归入几条」无关。 */
  total: number;
}

export interface BiliSeasonList {
  seasons: BiliSeasonOption[];
  /** 列不出来时的原因（未登录 / 登录信息不完整）。空串表示「确实没有合集」。 */
  error: string;
  advice: string;
}

/** 归入方式。取值与服务端 `bili_meta.GROUP_MODE_*` 一致。 */
export type BiliGroupMode = "none" | "existing" | "new";

export interface BiliJobUploads {
  job_id: string;
  badge: BiliBadge;
  badge_text: string;
  badge_tone: BiliBadgeTone;
  uploads: BiliUpload[];
  season: BiliSeason | null;
}

export interface BiliJobUploadsSummary {
  jobs: BiliJobUploads[];
}

export interface BiliRetry {
  action: "reupload" | "reverify" | "none";
  message: string;
  upload: BiliUpload | null;
}

export interface BiliSeasonRetry {
  message: string;
  season: BiliSeason;
}
