# ===============================================================================
# Copyright 2020 ross
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
from traits.api import List, Str, Bool, Event, Property
from traitsui.api import (
    View,
    Group,
    Item,
    UItem,
    ListEditor,
    InstanceEditor,
    ButtonEditor,
    HGroup,
)

from pychron.hardware import get_float
from pychron.hardware.core.core_device import CoreDevice
from pychron.hardware.gauges.base_controller import BaseGauge


class MKSSRG(CoreDevice):
    """
    MKS SRG-3 Spinning Rotor Gauge RS-232 driver.

    Implements basic pressure readout using the SRG-3 instrument
    command language (postfix notation, CR-terminated commands).
    The `VAL` command returns the most recent measured pressure as
    a real number in scientific format (e.g. ` 1.2345E-05`).

    Default serial config per manual: 9600 baud, 8 data bits, no
    parity, 1 stop bit, no handshake.

    Example device config file (`MKSSRG.cfg`)::

        [General]
        name=SRG3

        [Communications]
        type=serial
        port=/dev/tty.usbserial-SRG3
        baudrate=9600
        bytesize=8
        parity=NONE
        stopbits=1
        timeout=2
        read_delay=50

    Remote control: per the SRG-3 RS232 manual (sec 2.6) the instrument
    enters remote control state on the first valid command, which disables
    the front panel ENTER/ON/OFF keys. RTL returns it to local control.
    There is no query for the remote/local state, so it is tracked here.
    While in local, pressure polling is suspended; otherwise the next VAL
    would put the instrument straight back into remote.
    """

    scheme = "ascii"
    scan_func = "update_pressures"

    gauges = List
    display_name = Str

    remote = Bool(True)
    remote_button = Event
    remote_label = Property(depends_on="remote")
    remote_state_label = Property(depends_on="remote")

    def load_additional_args(self, config, *args, **kw):
        self.display_name = self.config_get(
            config, "General", "display_name", default=self.name, optional=True
        )
        gname = self.config_get(
            config, "Gauge", "name", default=self.name, optional=True
        )
        gdisplay = self.config_get(
            config, "Gauge", "display_name", default=self.display_name, optional=True
        )
        low = self.config_get(
            config, "Gauge", "low", cast="float", default=1e-9, optional=True
        )
        high = self.config_get(
            config, "Gauge", "high", cast="float", default=1e-3, optional=True
        )
        color_scalar = self.config_get(
            config, "Gauge", "color_scalar", cast="int", default=1, optional=True
        )

        g = BaseGauge(
            name=gname,
            display_name=gdisplay,
            low=low,
            high=high,
            color_scalar=color_scalar,
        )
        self.gauges = [g]
        return True

    def initialize(self, *args, **kw):
        if self.communicator:
            # SRG-3: input terminated by CR; reply ends with CRLF then a
            # prompt char ('>' = ack, '?' = nak) per manual sec 2.3-2.4.
            # Terminate read on the prompt char so the trailing byte does
            # not defeat a CRLF terminator and force a 1s timeout.
            self.communicator.write_terminator = b"\r"
            self.communicator.read_terminator = (b">", b"?")
        # clear any pending input/prompt
        self.ask("idy")
        return True

    @get_float()
    def get_pressure(self, **kw):
        return self.read_pressure(**kw)

    def read_pressure(self, verbose=False):
        if not self.remote:
            # any command would put the SRG back into remote
            return None

        resp = self.ask("val", verbose=verbose)
        return self._parse_real(resp)

    def get_unit_label(self, verbose=False):
        return self.ask("ulb", verbose=verbose)

    def update_pressures(self, verbose=False):
        if not self.remote:
            # hold the last reading. returning None repeatedly would stop a
            # device-timer scan (see ScanableDevice._scan_)
            return self.gauges[0].pressure if self.gauges else None

        p = self.get_pressure(verbose=verbose)
        if p is not None and self.gauges:
            self.gauges[0].pressure = p
        return p

    def get_pressures(self, force=False, verbose=False):
        """Match the BaseGaugeController API expected by GaugeManager."""
        self.update_pressures(verbose=verbose)
        return [g.pressure for g in self.gauges]

    def return_to_local(self):
        self.info("return to local control")
        with self.lock_scan():
            self.ask("rtl", verbose=True)
            self.remote = False

    def take_remote(self):
        self.info("take remote control")
        with self.lock_scan():
            # first valid command puts the SRG into remote control state
            self.remote = True
            self.ask("idy", verbose=True)

    def set_remote(self, state):
        if state:
            self.take_remote()
        else:
            self.return_to_local()

    def _remote_button_fired(self):
        self.set_remote(not self.remote)

    def _get_remote_label(self):
        return "Return to Local" if self.remote else "Take Remote"

    def _get_remote_state_label(self):
        return "Remote" if self.remote else "Local (polling paused)"

    def gauge_view(self):
        return View(
            Group(
                Item(
                    "gauges",
                    style="custom",
                    show_label=False,
                    editor=ListEditor(
                        mutable=False, style="custom", editor=InstanceEditor()
                    ),
                ),
                HGroup(
                    UItem(
                        "remote_button",
                        editor=ButtonEditor(label_value="remote_label"),
                    ),
                    UItem("remote_state_label", style="readonly"),
                ),
                show_border=True,
                label=self.display_name or self.name,
            )
        )

    def _parse_real(self, resp):
        if resp is None:
            return None
        # SRG-3 reply pattern: "<value> \r\n>" (prompt char trails reply).
        # Strip CR/LF/whitespace and any leading/trailing prompt chars (>, ?).
        cleaned = resp.strip().strip(">?").strip()
        try:
            return float(cleaned)
        except (TypeError, ValueError) as e:
            self.warning(
                "failed parsing SRG-3 pressure response {!r}: {}".format(resp, e)
            )
            return None


# ============= EOF =============================================
