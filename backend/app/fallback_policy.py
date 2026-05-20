from .schemas import ResolutionFallback


REQUESTED_RESOLUTION_MISSING = "requested_resolution_missing"
SOURCE_BELOW_720_ONLY = "source_below_720_only"
REQUESTED_RESOLUTION_UNSELECTABLE = "requested_resolution_unselectable"
MEDIA_STREAM_BLOCKED = "media_stream_blocked"


def build_resolution_fallback(
    requested_resolution: str | None,
    fallback_resolution: str | None,
    status: str | None = None,
    reason: str | None = None,
) -> ResolutionFallback | None:
    if not requested_resolution or not fallback_resolution:
        return None

    restart_resolution: str | None = None
    if reason == REQUESTED_RESOLUTION_MISSING:
        message = f"视频本来没有 {requested_resolution}，已自动降级到 {fallback_resolution}。"
    elif reason == SOURCE_BELOW_720_ONLY:
        message = f"视频本身没有 720p 或更高清晰度，已自动降级到最高可用的 {fallback_resolution}。"
    elif reason == REQUESTED_RESOLUTION_UNSELECTABLE:
        message = (
            f"检测到 {requested_resolution} 清晰度，但该清晰度当前没有可下载的视频/音频组合，"
            f"已自动降级到 {fallback_resolution}。"
        )
        restart_resolution = requested_resolution
    elif reason == MEDIA_STREAM_BLOCKED:
        message = f"当前 {requested_resolution} 媒体流下载被 YouTube 拒绝或连接重置，可尝试以 {fallback_resolution} 重启。"
        restart_resolution = fallback_resolution
    elif status != "failed":
        message = f"已从 {requested_resolution} 自动降级到 {fallback_resolution}。"
    else:
        message = f"当前下载未能在 {requested_resolution} 下完成，可尝试以 {fallback_resolution} 重启。"
        restart_resolution = fallback_resolution

    return ResolutionFallback(
        requested_resolution=requested_resolution,
        fallback_resolution=fallback_resolution,
        reason=reason,
        restart_resolution=restart_resolution,
        message=message,
    )

