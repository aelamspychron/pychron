# ===============================================================================
# Copyright 2011 Jake Ross
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================

# =============enthought library imports=======================
# ============= standard library imports ========================
import logging
import time
from threading import Event

# try:
#     from pyface.qt.QtCore import QThread
# except ImportError:
from threading import Thread

# ============= local library imports  ==========================

logger = logging.getLogger("Timer")


class Timer(Thread):
    def __init__(self, period, func, delay=0, *args, **kw):
        super(Timer, self).__init__()
        self._period = period / 1000.0
        self.func = func

        self._flag = Event()
        self._flag.clear()

        self._delay = delay / 1000.0
        self._args = args
        self._kwargs = kw

        # instrumentation: expose tick health so consumers/logs can detect a
        # dead or stalled timer thread
        self.tick_count = 0
        self.last_tick = None
        self.died = False

        self.start()

    def run(self):
        func = self.func
        flag = self._flag
        args = self._args
        kwargs = self._kwargs
        delay = self._delay

        if delay:
            flag.wait(delay)

        flag.clear()
        while not flag.is_set():
            st = time.time()
            try:
                func(*args, **kwargs)
            except BaseException:
                # an uncaught exception previously killed this thread silently,
                # leaving the scan stopped with no log trace
                self.died = True
                logger.exception(
                    "timer func %r raised after %s ticks. timer thread stopping",
                    func,
                    self.tick_count,
                )
                raise
            self.tick_count += 1
            self.last_tick = time.time()
            t = max(0, self._period - time.time() + st)
            if t:
                flag.wait(t)
        logger.debug(
            "timer func %r stopped cleanly after %s ticks", func, self.tick_count
        )

    def wait_for_completion(self, timeout=None):
        st = time.time()
        while 1:
            if timeout:
                if time.time() - st > timeout:
                    return "timeout"

            if not self.isActive():
                break

            self._flag.wait(0.25)
            # time.sleep(0.01)

    def Stop(self):
        self._flag.set()

    stop = Stop

    def isActive(self):
        return not self._flag.is_set()

    #         # need to wait unit
    #         self.f
    #         return not self._flag.is_set()

    # and not self._completed

    def set_interval(self, v):
        self._period = v / 1000.0

    def get_interval(self):
        """
        return period in s
        """
        return self._period


# ============= EOF =====================================
