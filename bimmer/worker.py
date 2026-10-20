"""The single worker thread that every EDIABAS call goes through."""

from __future__ import annotations

import queue
import threading


class Worker(threading.Thread):
    def __init__(self, out_q: queue.Queue):
        super().__init__(daemon=True)
        self.in_q: queue.Queue = queue.Queue()
        self.out_q = out_q

    def submit(self, fn, *args, cb=None):
        self.in_q.put((fn, args, cb))

    def stop(self):
        self.in_q.put((None, (), None))

    def run(self):
        while True:
            fn, args, cb = self.in_q.get()
            if fn is None:
                break
            try:
                res, err = fn(*args), None
            except Exception as e:  # noqa: BLE001 - report everything to the UI
                res, err = None, e
            self.out_q.put((cb, res, err))

    @property
    def busy(self):
        return not self.in_q.empty()
