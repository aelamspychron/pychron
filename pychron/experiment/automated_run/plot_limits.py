# ===============================================================================
# Copyright 2026 ross
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================
"""
x-axis limits for the run plots.

Plotted x values are wall-clock seconds since time zero, so anything that runs
between time zero and collection (peak center, magnet moves, settling) pushes the
data out in time. Limits must be based on elapsed time, not just the sum of
counts * integration time.
"""

from math import isfinite

XPAD = 1.25


def calculate_xmax(current_max, total_seconds, elapsed, remaining_seconds, pad=XPAD):
    """
    x max needed to show a collection that is about to start.

    current_max: current x max of the graph (may be inf)
    total_seconds: counts-based estimate (plot_panel.total_seconds)
    elapsed: wall-clock seconds since time zero, i.e. the x of the next point
    remaining_seconds: expected duration of this collection
    """
    needed = max(total_seconds, elapsed + remaining_seconds) * pad
    if current_max is None or not isfinite(current_max):
        return needed
    return max(current_max, needed)


def extend_xmax(current_max, x, pad=XPAD):
    """
    new x max if x is beyond the current x max, otherwise None.
    an infinite x max means auto-scaling, so leave it alone.
    """
    if current_max is None or not isfinite(current_max) or x <= current_max:
        return None
    return x * pad


# ============= EOF =============================================
