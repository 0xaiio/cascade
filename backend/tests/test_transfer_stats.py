from app.transfer_stats import TransferStats


def test_transfer_stats_calculates_average_speed_from_download_progress() -> None:
    times = iter([10.0, 11.0, 13.0])
    stats = TransferStats(clock=lambda: next(times))

    stats.record(downloaded_bytes=0)
    stats.record(downloaded_bytes=4096)
    stats.record(downloaded_bytes=8192)

    assert stats.average_speed() == 8192 / 3


def test_transfer_stats_keeps_samples_before_non_monotonic_progress_reset() -> None:
    times = iter([1.0, 2.0, 3.0, 4.0])
    stats = TransferStats(clock=lambda: next(times))

    stats.record(downloaded_bytes=0)
    stats.record(downloaded_bytes=4096)
    stats.record(downloaded_bytes=1024)
    stats.record(downloaded_bytes=2048)

    assert stats.average_speed() == 5120 / 2
