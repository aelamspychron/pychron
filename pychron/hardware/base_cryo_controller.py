# ===============================================================================
# Copyright 2023 Jake Ross
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
import string
import time

from traits.api import Float

from pychron.hardware import get_float
from pychron.hardware.core.core_device import CoreDevice


class BaseCryoController(CoreDevice):
    verify_setpoint = True

    # configurable setpoint limits. defaults permissive (no limit)
    setpoint_min = Float(float("-inf"))
    setpoint_max = Float(float("inf"))

    def load_additional_args(self, config):
        self._load_setpoint_limits(config)
        return super().load_additional_args(config)

    def _load_setpoint_limits(self, config):
        # [Setpoint]
        # min=0
        # max=325
        self.set_attribute(
            config,
            "setpoint_min",
            "Setpoint",
            "min",
            cast="float",
            optional=True,
            default=float("-inf"),
        )
        self.set_attribute(
            config,
            "setpoint_max",
            "Setpoint",
            "max",
            cast="float",
            optional=True,
            default=float("inf"),
        )

    def _setpoint_limits(self, output=1):
        """Return (min, max) for the given output. Override for per-channel."""
        return self.setpoint_min, self.setpoint_max

    def _validate_setpoint(self, v, output=1):
        """Return float v if within the output's [min, max], else None.

        Warns user on out-of-range. Used to gate programmatic set_setpoint.
        """
        try:
            v = float(v)
        except (TypeError, ValueError):
            self.warning("invalid setpoint value: {}".format(v))
            return

        lo, hi = self._setpoint_limits(output)
        if v < lo or v > hi:
            self.warning_dialog(
                "Setpoint {} (output {}) out of range. "
                "Must be between {} and {}".format(v, output, lo, hi)
            )
            return

        return v

    def setpoints_achieved(self, setpoints, tol=1):
        pass

    def _block(self, setpoints, delay, block):
        if block:
            delay = max(0.5, delay)
            tol = 1
            if isinstance(block, (int, float)):
                tol = block

            while 1:
                if self.setpoints_achieved(setpoints, tol):
                    break
                time.sleep(delay)

    @get_float(default=0)
    def read_setpoint(self, output, verbose=False):
        return self._read_setpoint(output, verbose=verbose)

    @get_float(default=0)
    def read_input(self, v, **kw):
        if isinstance(v, int):
            v = string.ascii_lowercase[v - 1]
        return self._read_input(v, **kw)

    def set_setpoint(self, v, output=1, retries=3):
        v = self._validate_setpoint(v, output)
        if v is None:
            return

        sp = None
        for i in range(retries):
            self._write_setpoint(v, output)
            if not self.verify_setpoint:
                break

            time.sleep(0.25)  # wait for setpoint to be set

            sp = self.read_setpoint(output, verbose=True)
            if sp == v:
                break
        else:
            if self.verify_setpoint:
                self.warning_dialog(f"Failed setting setpoint to {v}. Got={sp}")

    def _write_setpoint(self, v, output):
        raise NotImplementedError

    def set_setpoints(self, *setpoints, block=False, delay=1):
        raise NotImplementedError

    def _read_setpoint(self, output, verbose=False):
        raise NotImplementedError

    def _read_input(self, v, **kw):
        raise NotImplementedError


# ============= EOF =============================================
