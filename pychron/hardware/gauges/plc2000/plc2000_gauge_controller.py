# ===============================================================================
# Copyright 2021 ross
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
import time

from pychron.hardware.core.core_device import CoreDevice
from pychron.hardware.core.modbus import ModbusMixin
from pychron.hardware.gauges.base_controller import BaseGaugeController


class PLC2000GaugeController(BaseGaugeController, CoreDevice, ModbusMixin):
    _readback_state = None

    def load_additional_args(self, config, *args, **kw):
        self.display_name = self.config_get(
            config, "General", "display_name", default=self.name
        )
        self._load_gauges(config)
        return True

    def get_pressures(self, *args, **kw):
        kw["force"] = True
        return super(PLC2000GaugeController, self).get_pressures(*args, **kw)

    def _record_readback(self, gname, register, pressure):
        """Edge-triggered logging of per-gauge readback health.

        Logs once on ok->fail and fail->ok transitions (with counts and the
        time of the last good read) instead of spamming every scan period.
        """
        states = self._readback_state
        if states is None:
            states = self._readback_state = {}

        state = states.get(gname)
        if state is None:
            state = states[gname] = {
                "ok": None,
                "reads": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_good_time": None,
                "last_good_value": None,
            }

        state["reads"] += 1
        ok = isinstance(pressure, float)
        now = time.time()
        if ok:
            if state["ok"] is False:
                self.info(
                    f"gauge {gname} (register={register}) readback recovered "
                    f"after {state['consecutive_failures']} consecutive failures. "
                    f"pressure={pressure:0.2e}"
                )
            state["consecutive_failures"] = 0
            state["last_good_time"] = now
            state["last_good_value"] = pressure
        else:
            state["failures"] += 1
            state["consecutive_failures"] += 1
            if state["ok"] in (True, None):
                lt = state["last_good_time"]
                age = f"{now - lt:0.1f}s ago" if lt else "never"
                self.warning(
                    f"gauge {gname} (register={register}) readback failed. "
                    f"got {pressure!r}. last good read: {age} "
                    f"(value={state['last_good_value']}). "
                    f"totals: {state['failures']} failures/{state['reads']} reads. "
                    f"display will hold last value until recovery"
                )
        state["ok"] = ok

    def _read_pressure(self, name=None, verbose=False):
        pressure = "err"

        gname = name
        if isinstance(name, str):
            gauge = self.get_gauge(name)
            channel = gauge.channel
        else:
            channel = name.channel
            gname = name.name

        if name is not None:
            # register = channel-1
            register = int(channel) - 1
            try:
                pressure = self._read_float(register)
            except BaseException as e:
                self.debug_exception()
                self.debug(f"failed reading register={register}, error={e}")

            if verbose:
                self.debug(
                    f"read gauge={gname} channel={channel} "
                    f"register={register} pressure={pressure!r}"
                )

            self._record_readback(gname, register, pressure)

        return pressure


# ============= EOF =============================================
