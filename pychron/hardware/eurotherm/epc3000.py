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
from traits.api import Int, Float
from traitsui.api import View, Item, UItem, HGroup, VGroup

from pychron.core.ui.lcd_editor import LCDEditor
from pychron.hardware import get_float
from pychron.hardware.core.core_device import CoreDevice
from pychron.hardware.core.modbus import ModbusMixin


class EPC3000(CoreDevice, ModbusMixin):
    """
    Eurotherm / Carbolite EPC3000 series temperature controller
    (EPC3016, EPC3008, EPC3004) Modbus driver.

    Implements basic process-value (temperature) readout. EPC3000
    Modbus addresses are 2400-series compatible: register 1 holds
    the Process Value as a 16-bit signed integer scaled by the
    instrument's configured decimal places (e.g. DecP=1 -> divide
    raw value by 10).

    Pymodbus is used for transport; either Modbus TCP (Ethernet
    option) or Modbus RTU (RS232/RS485, EPC3008/3004 standard) may
    be configured.

    Example device config file (`EPC3000.cfg`) - Modbus TCP::

        [General]
        name=EPC3000

        [Communications]
        type=modbustcp
        host=192.168.1.50
        port=502
        timeout=2
        byteorder=big
        wordorder=little

        [Register]
        process_value=1
        setpoint=2
        output=3
        working_setpoint=5

        [Scaling]
        decimal_places=1

    Example device config file (`EPC3000.cfg`) - Modbus RTU::

        [General]
        name=EPC3000

        [Communications]
        type=modbus
        port=/dev/tty.usbserial-EPC
        baudrate=9600
        bytesize=8
        parity=NONE
        stopbits=1
        timeout=2
        slave_address=01

        [Register]
        process_value=1
        setpoint=2
        output=3
        working_setpoint=5

        [Scaling]
        decimal_places=1
    """

    process_value_address = Int(1)
    setpoint_address = Int(2)
    output_address = Int(3)
    working_setpoint_address = Int(5)
    decimal_places = Int(1)
    output_decimal_places = Int(1)

    process_value = Float
    setpoint = Float(enter_set=True, auto_set=False)
    setpoint_readback = Float
    output = Float

    scan_func = "get_process_value"

    def load_additional_args(self, config):
        for attr in ("process_value", "setpoint", "output", "working_setpoint"):
            self.set_attribute(
                config,
                "{}_address".format(attr),
                "Register",
                attr,
                cast="int",
                optional=True,
            )
        self.set_attribute(
            config,
            "decimal_places",
            "Scaling",
            "decimal_places",
            cast="int",
            optional=True,
        )
        self.set_attribute(
            config,
            "output_decimal_places",
            "Scaling",
            "output_decimal_places",
            cast="int",
            optional=True,
        )
        return True

    _comms_report_attrs = (
        "process_value_address",
        "setpoint_address",
        "output_address",
        "working_setpoint_address",
        "decimal_places",
        "output_decimal_places",
    )

    def initialize(self, *args, **kw):
        ok = super(EPC3000, self).initialize(*args, **kw)
        if self.communicator is not None:
            self.communicator.report()
        self.report()
        return ok

    def report(self):
        self.debug("============ EPC3000 Communications Report ============")
        self._generate_comms_report()
        self.debug("=======================================================")

    def _generate_comms_report(self):
        self.debug("{:<28s} {}".format("Param:", "Value:"))
        for key in self._comms_report_attrs:
            self.debug("{:<28s} {}".format("{}:".format(key), getattr(self, key)))

        self.debug("{:<28s} {}".format("Param:", "Reading:"))
        for label, fn in (
            ("process_value", self.get_process_value),
            ("setpoint", self.get_setpoint),
            ("output", self.get_output),
            ("working_setpoint", self.get_working_setpoint),
        ):
            try:
                v = fn()
            except Exception as e:
                v = "error: {}".format(e)
            self.debug("{:<28s} {}".format("{}:".format(label), v))

    @get_float(default=0)
    def get_process_value(self, **kw):
        return self._read_scaled(self.process_value_address, **kw)

    @get_float(default=0)
    def get_setpoint(self, **kw):
        return self._read_scaled(self.setpoint_address, **kw)

    @get_float(default=0)
    def get_working_setpoint(self, **kw):
        return self._read_scaled(self.working_setpoint_address, **kw)

    @get_float(default=0)
    def get_output(self, **kw):
        return self._read_scaled(
            self.output_address,
            decimal_places=self.output_decimal_places,
            **kw,
        )

    # furnace controller api (IFurnaceController / BaseFurnaceManager)
    def test_connection(self):
        """
        return (connected, error) as expected by BaseFurnaceManager and the
        experiment executor's pre execute check
        """
        if self.communicator is None:
            return False, "no communicator"

        if self.communicator.simulation:
            return False, "communicator in simulation mode"

        pv = self._read_scaled(self.process_value_address, verbose=True)
        if pv is None:
            return False, "failed reading process value"
        return True, None

    def read_setpoint(self, **kw):
        return self.get_setpoint(**kw)

    def read_temperature(self, force=False, **kw):
        return self.get_process_value(**kw)

    def read_output_percent(self, force=False, **kw):
        return self.get_output(**kw)

    def get_response(self, force=False, **kw):
        return self.get_process_value(**kw)

    def set_pid(self, param_str):
        self.warning(
            "set_pid not supported by EPC3000 driver. ignoring {}".format(param_str)
        )
        return False

    def set_setpoint(self, v):
        if self.setpoint_address is None:
            self.debug("setpoint_address not set")
            return

        scale = 10**self.decimal_places if self.decimal_places else 1
        raw = int(round(v * scale))
        if raw < 0:
            raw += 0x10000
        raw &= 0xFFFF
        self.debug(
            "set setpoint addr={}, value={}, raw={}".format(
                self.setpoint_address, v, raw
            )
        )
        self._write_register(int(self.setpoint_address), raw)

    def _setpoint_changed(self, new):
        self.set_setpoint(new)

    def _scan_hook(self, v):
        if isinstance(v, tuple):
            v = v[0] if v else None

        if v is not None:
            self.process_value = v

        sp = self.get_setpoint()
        if sp is not None:
            self.setpoint_readback = sp

        out = self.get_output()
        if out is not None:
            self.output = out

    def heater_view(self):
        return View(
            VGroup(
                HGroup(
                    UItem("name", style="readonly"),
                ),
                HGroup(
                    Item(
                        "process_value",
                        style="readonly",
                        editor=LCDEditor(width=120, height=30),
                        label="PV",
                    ),
                ),
                HGroup(
                    Item("setpoint", label="Setpoint"),
                    UItem(
                        "setpoint_readback",
                        editor=LCDEditor(width=120, height=30),
                        style="readonly",
                    ),
                ),
                HGroup(
                    Item(
                        "output",
                        style="readonly",
                        editor=LCDEditor(width=120, height=30),
                        label="Output",
                    ),
                ),
            )
        )

    def _read_scaled(self, address, decimal_places=None, **kw):
        result = self._read_holding_registers(address=int(address), count=1, **kw)
        if result is None:
            return None
        try:
            raw = result.registers[0]
        except (AttributeError, IndexError):
            self.warning(
                "invalid modbus result for address {}: {!r}".format(address, result)
            )
            return None

        # 16-bit two's complement
        if raw & 0x8000:
            raw -= 0x10000

        if decimal_places is None:
            decimal_places = self.decimal_places
        scale = 10**decimal_places if decimal_places else 1
        return raw / scale


# ============= EOF =============================================
