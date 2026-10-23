"""queue_worker.py：一次處理一張、先進先出、有上限、單張出錯不影響後面（architecture.md 第 7 節）。"""

import asyncio

import pytest

from music_bot.queue_worker import JobQueue


async def settle():
    """讓事件迴圈跑幾輪，讓背景工作員有機會動作。"""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_jobs_run_one_at_a_time_in_fifo_order():
    running = 0
    peak = 0
    order = []

    async def handler(job):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        order.append(job)
        running -= 1

    q = JobQueue(handler)
    q.start()
    for i in range(5):
        assert q.submit(f"user{i}", i) is True
    await q.join()
    await q.stop()
    assert order == [0, 1, 2, 3, 4]
    assert peak == 1


async def test_total_limit_rejects_extra_jobs():
    gate = asyncio.Event()

    async def handler(job):
        await gate.wait()

    q = JobQueue(handler, max_total=3, max_per_user=99)
    q.start()
    results = [q.submit(f"u{i}", i) for i in range(5)]
    assert results == [True, True, True, False, False]
    gate.set()
    await q.join()
    await q.stop()


async def test_per_user_limit_counts_the_running_job_too():
    gate = asyncio.Event()

    async def handler(job):
        await gate.wait()

    q = JobQueue(handler, max_total=99, max_per_user=2)
    q.start()
    assert q.submit("alice", 1) is True
    await settle()  # 第一張已經開始處理，仍算在 alice 頭上
    assert q.submit("alice", 2) is True
    assert q.submit("alice", 3) is False
    assert q.submit("bob", 4) is True  # 別人不受影響
    gate.set()
    await q.join()
    await q.stop()


async def test_capacity_is_freed_after_jobs_finish():
    async def handler(job):
        pass

    q = JobQueue(handler, max_total=99, max_per_user=1)
    q.start()
    assert q.submit("alice", 1) is True
    await q.join()
    assert q.submit("alice", 2) is True
    await q.join()
    await q.stop()


async def test_a_crashing_job_does_not_stop_the_worker():
    done = []

    async def handler(job):
        if job == "bad":
            raise RuntimeError("boom")
        done.append(job)

    q = JobQueue(handler)
    q.start()
    q.submit("u", "a")
    q.submit("u", "bad")
    q.submit("u", "b")
    await q.join()
    await q.stop()
    assert done == ["a", "b"]


async def test_a_crashing_job_still_frees_the_users_slot():
    async def handler(job):
        raise RuntimeError("boom")

    q = JobQueue(handler, max_per_user=1)
    q.start()
    q.submit("alice", 1)
    await q.join()
    assert q.submit("alice", 2) is True
    await q.join()
    await q.stop()


async def test_pending_count_reflects_waiting_and_running_jobs():
    gate = asyncio.Event()

    async def handler(job):
        await gate.wait()

    q = JobQueue(handler)
    q.start()
    assert q.pending == 0
    q.submit("a", 1)
    q.submit("b", 2)
    await settle()
    assert q.pending == 2
    gate.set()
    await q.join()
    assert q.pending == 0
    await q.stop()


async def test_stop_cancels_a_running_job_cleanly():
    started = asyncio.Event()
    cancelled = []

    async def handler(job):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(job)
            raise

    q = JobQueue(handler)
    q.start()
    q.submit("a", 1)
    await started.wait()
    await q.stop()
    assert cancelled == [1]


async def test_submit_before_start_is_still_accepted_and_runs_after_start():
    done = []

    async def handler(job):
        done.append(job)

    q = JobQueue(handler)
    assert q.submit("a", 1) is True
    q.start()
    await q.join()
    await q.stop()
    assert done == [1]


async def test_submit_without_limits_ignores_both_caps():
    """開機補處理用：關機期間積壓的圖不能因為上限被拒絕。"""
    gate = asyncio.Event()

    async def handler(job):
        await gate.wait()

    q = JobQueue(handler, max_total=2, max_per_user=1)
    q.start()
    assert all(q.submit("alice", i, enforce_limits=False) for i in range(5))
    assert q.pending == 5
    assert q.submit("alice", 99) is False  # 一般的送件仍然受上限約束
    gate.set()
    await q.join()
    await q.stop()
