import threading

from service.scheduler import IntervalJob


def test_a_failed_run_is_logged_and_the_schedule_continues() -> None:
    runs = []

    def flaky():
        runs.append(True)
        if len(runs) == 1:
            raise RuntimeError("provider down")
        return "ok"

    job = IntervalJob("test", 60, flaky)
    job.run_once()
    job.run_once()
    assert len(runs) == 2


def test_job_runs_on_its_interval_until_stopped() -> None:
    ran = threading.Event()
    job = IntervalJob("test", 0.01, ran.set)
    job.start()
    first_thread = job._thread
    job.start()
    assert job._thread is first_thread
    assert ran.wait(timeout=2)
    job.stop()
    assert job._thread is None


def test_daily_job_waits_until_the_next_run_time() -> None:
    from datetime import datetime, time

    from service.scenarios import TIMEZONE
    from service.scheduler import DailyJob

    job = DailyJob("daily", time(23, 30), TIMEZONE, lambda: None)
    before = datetime(2026, 10, 8, 23, 0, tzinfo=TIMEZONE)
    after = datetime(2026, 10, 8, 23, 45, tzinfo=TIMEZONE)
    assert job.seconds_until_next(before) == 30 * 60
    assert job.seconds_until_next(after) == (23 * 60 + 45) * 60
    assert job.seconds_until_next() > 0


def test_daily_job_runs_when_its_wait_ends(monkeypatch) -> None:
    from datetime import time

    from service.scenarios import TIMEZONE
    from service.scheduler import DailyJob

    ran = threading.Event()
    job = DailyJob("daily", time(23, 30), TIMEZONE, ran.set)
    monkeypatch.setattr(job, "seconds_until_next", lambda now=None: 0.01)
    job.start()
    assert ran.wait(timeout=2)
    job.stop()
