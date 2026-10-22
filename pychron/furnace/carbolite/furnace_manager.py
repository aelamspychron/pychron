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
from traits.api import provides

from pychron.furnace.base_furnace_manager import BaseFurnaceManager
from pychron.furnace.ifurnace_manager import IFurnaceManager
from pychron.hardware.eurotherm.epc3000 import EPC3000


@provides(IFurnaceManager)
class CarboliteFurnaceManager(BaseFurnaceManager):
    """
    Carbolite furnace controlled by a Eurotherm EPC3000 series controller.

    Controller config: setupfiles/devices/furnace/controller.cfg
    (see EPC3000 docstring for format)
    """

    controller_klass = EPC3000

    def set_pid_parameters(self, v):
        # EPC3000 manages its own PID. no eurotherm_control_parameters table
        self.debug("set pid parameters not supported. setpoint={}".format(v))

    def end_extract(self, *args, **kw):
        self.debug("end extract")
        self.setpoint = 0

    def disable_device(self, *args, **kw):
        self.debug("disable device")
        self.setpoint = 0

    def prepare(self, *args, **kw):
        return True

    def is_ready(self):
        return True


# ============= EOF =============================================
