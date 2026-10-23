"""排隊：一次只處理一張圖（architecture.md 第 7 節）。

為什麼只用一個工作員：
- 同時開很多個 `claude -p` 會一瞬間吃掉很多額度；
- 只有一個人在寫 Excel，檔案不會壞、「當日已紀錄音樂數」不會算錯。
代價是人多時比較慢，這一版接受這個取捨。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_TOTAL = 30  # 排隊中加處理中的總張數上限
DEFAULT_MAX_PER_USER = 5  # 同一個人同時在排隊（含處理中）的張數上限


class JobQueue(Generic[T]):
    def __init__(
        self,
        handler: Callable[[T], Awaitable[None]],
        max_total: int = DEFAULT_MAX_TOTAL,
        max_per_user: int = DEFAULT_MAX_PER_USER,
    ) -> None:
        self._handler = handler
        self._max_total = max_total
        self._max_per_user = max_per_user
        self._queue: asyncio.Queue[tuple[str, T]] = asyncio.Queue()
        self._per_user: Counter[str] = Counter()
        self._pending = 0
        self._task: asyncio.Task[None] | None = None

    @property
    def pending(self) -> int:
        """排隊中加處理中的總張數。"""
        return self._pending

    def submit(self, user_id: str, job: T, enforce_limits: bool = True) -> bool:
        """放進隊伍。已滿（總量或這個人的份額）就回傳 False，什麼都不做。

        enforce_limits=False 只給開機補處理用：關機期間積壓的圖不能因為上限被拒絕，
        否則它們會被貼上 ❌，之後再也不會被補回來。
        """
        if enforce_limits and (self._pending >= self._max_total or self._per_user[user_id] >= self._max_per_user):
            return False
        self._pending += 1
        self._per_user[user_id] += 1
        self._queue.put_nowait((user_id, job))
        return True

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="job-queue-worker")

    async def join(self) -> None:
        """等到隊伍裡的工作都處理完（測試用）。"""
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            user_id, job = await self._queue.get()
            try:
                await self._handler(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 單張出錯不能讓工作員死掉，否則後面所有人都不會被處理
                logger.exception("處理工作時發生未預期的錯誤，已略過這一張")
            finally:
                self._per_user[user_id] -= 1
                if self._per_user[user_id] <= 0:
                    del self._per_user[user_id]
                self._pending -= 1
                self._queue.task_done()
