"""Tests for the custom executor used to run image download workers"""

from sotoki.utils.executor import SotokiExecutor


def test_join_before_start_is_safe():
    """join() must not fail when the executor was never started

    The scraper joins the images executor in its error path, even when the error
    happened before image downloads were started."""
    executor = SotokiExecutor(queue_size=10, nb_workers=2, prefix="T-TEST-")
    executor.join()  # must not raise
    assert executor.no_more is True


def test_join_after_start_awaits_workers():
    executor = SotokiExecutor(queue_size=10, nb_workers=2, prefix="T-TEST-")
    done = []
    executor.start()
    executor.submit(lambda: done.append(True))
    executor.join()
    executor.shutdown()
    assert done == [True]
