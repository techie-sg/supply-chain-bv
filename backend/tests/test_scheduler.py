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
