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

from pymodbus.exceptions import ConnectionException, ModbusException

# emit a stats summary to the log every N modbus calls
STATS_SUMMARY_INTERVAL = 100

# log any call that takes longer than this (seconds)
SLOW_CALL_THRESHOLD = 1.0

# minimum seconds between reconnect attempts so a dead PLC isn't hammered
RECONNECT_THROTTLE = 10.0


class ModbusMixin:
    """
    simple mapper of the Modbus commands
    """

    _modbus_stats = None
    _reconnect_last_attempt = None

    def _get_wordorder(self):
        if hasattr(self.communicator, "wordorder"):
            return self.communicator.wordorder.lower()
        return "little"

    def _read_float(self, register, *args, **kw):
        result = self._read_holding_registers(
            address=int(register), count=2, *args, **kw
        )
        return self._decode_float(result)

    def _read_int(self, register, *args, **kw):
        result = self._read_holding_registers(
            address=int(register), count=2, *args, **kw
        )
        if result and not result.isError():
            return self.communicator.convert_from_registers(
                result.registers,
                data_type=self.communicator.DATATYPE.INT32,
                word_order=self._get_wordorder(),
            )

    def _read_input_float(self, register, *args, **kw):
        result = self._read_input_registers(address=int(register), count=2, *args, **kw)
        return self._decode_float(result)

    def _read_input_int(self, register, *args, **kw):
        result = self._read_input_registers(address=int(register), count=2, *args, **kw)
        if result and not result.isError():
            return self.communicator.convert_from_registers(
                result.registers,
                data_type=self.communicator.DATATYPE.UINT32,
                word_order=self._get_wordorder(),
            )

    def _decode_float(self, result):
        if result and not result.isError():
            return self.communicator.convert_from_registers(
                result.registers,
                data_type=self.communicator.DATATYPE.FLOAT32,
                word_order=self._get_wordorder(),
            )

    def _get_payload(self, value, is_float=True):
        data_type = (
            self.communicator.DATATYPE.FLOAT32
            if is_float
            else self.communicator.DATATYPE.INT32
        )
        return self.communicator.convert_to_registers(
            value,
            data_type=data_type,
            word_order=self._get_wordorder(),
        )

    def _write_int(self, register, value, *args, **kw):
        payload = self._get_payload(value, is_float=False)
        self.debug(f"writing int register={register} payload={payload} value={value}")
        return self._write_registers(register, payload, *args, **kw)

    def _write_float(self, register, value, *args, **kw):
        payload = self._get_payload(value)
        self.debug(f"writing float register={register} payload={payload} value={value}")
        return self._write_registers(register, payload, *args, **kw)

    # instrumentation ----------------------------------------------------------
    def _modbus_socket_state(self):
        """Best-effort description of the underlying TCP socket state."""
        try:
            comm = self.communicator
            handle = getattr(comm, "handle", None)
            state = []
            if handle is not None:
                if hasattr(handle, "is_socket_open"):
                    state.append(f"socket_open={handle.is_socket_open()}")
                if hasattr(handle, "connected"):
                    state.append(f"connected={handle.connected}")
            state.append(f"simulation={getattr(comm, 'simulation', '?')}")
            return ", ".join(state)
        except BaseException as e:
            return f"unavailable ({e})"

    def _modbus_record(self, funcname, ok, detail=None):
        stats = self._modbus_stats
        if stats is None:
            stats = self._modbus_stats = {
                "calls": 0,
                "errors": 0,
                "consecutive_errors": 0,
                "last_success": None,
                "last_error": None,
                "last_error_detail": None,
            }

        stats["calls"] += 1
        now = time.time()
        if ok:
            if stats["consecutive_errors"]:
                self.info(
                    f"modbus {funcname} recovered after "
                    f"{stats['consecutive_errors']} consecutive errors"
                )
            stats["consecutive_errors"] = 0
            stats["last_success"] = now
        else:
            stats["errors"] += 1
            stats["consecutive_errors"] += 1
            stats["last_error"] = now
            stats["last_error_detail"] = detail
            ls = stats["last_success"]
            age = f"{now - ls:0.1f}s ago" if ls else "never"
            self.warning(
                f"modbus {funcname} failed: {detail}. "
                f"consecutive={stats['consecutive_errors']}, "
                f"total={stats['errors']}/{stats['calls']}, "
                f"last_success={age}, {self._modbus_socket_state()}"
            )

        if stats["calls"] % STATS_SUMMARY_INTERVAL == 0:
            self.debug(f"modbus stats: {stats}")

    # --------------------------------------------------------------------------

    def _modbus_reconnect(self, funcname):
        """Attempt to re-establish the modbus connection.

        Throttled to one attempt per RECONNECT_THROTTLE seconds so a dead or
        rebooting PLC isn't hammered by every scan tick. Returns True when the
        connection was re-established.
        """
        comm = self.communicator
        now = time.time()
        last = self._reconnect_last_attempt
        if last is not None and now - last < RECONNECT_THROTTLE:
            return False
        self._reconnect_last_attempt = now

        self.info(
            f"modbus attempting reconnect (trigger={funcname}, "
            f"{self._modbus_socket_state()})"
        )
        try:
            handle = getattr(comm, "handle", None)
            if handle is not None and hasattr(handle, "close"):
                try:
                    handle.close()
                except BaseException as e:
                    self.debug(f"modbus reconnect: close failed: {e}")

            if comm.initialize():
                self.info("modbus reconnect successful")
                self._reconnect_last_attempt = None
                return True

            self.warning(
                f"modbus reconnect failed: connect() returned False. "
                f"next attempt in {RECONNECT_THROTTLE:0.0f}s"
            )
        except BaseException as e:
            self.warning(
                f"modbus reconnect failed: {e}. "
                f"next attempt in {RECONNECT_THROTTLE:0.0f}s"
            )
        comm.simulation = True
        return False

    def _func(self, funcname, *args, **kw):
        if self.communicator is None:
            self._modbus_record(funcname, False, "no communicator")
            return

        if kw.pop("verbose", False):
            self.debug(f"ModbusMixin: {funcname} {args} {kw}")

        st = time.time()
        try:
            result = getattr(self.communicator, funcname)(*args, **kw)
        except (ConnectionException, OSError) as e:
            kind = (
                "connection lost"
                if isinstance(e, ConnectionException)
                else "socket error"
            )
            self._modbus_record(funcname, False, f"{kind}: {e}")
            self.communicator.simulation = True

            if not self._modbus_reconnect(funcname):
                return

            # single retry on the fresh connection. a second failure waits for
            # the next call/throttle window rather than recursing
            try:
                result = getattr(self.communicator, funcname)(*args, **kw)
            except BaseException as e2:
                self._modbus_record(
                    funcname, False, f"retry after reconnect failed: {e2}"
                )
                self.communicator.simulation = True
                return
        except ModbusException as e:
            # e.g. ModbusIOException on timeout/no response. previously uncaught here
            self._modbus_record(funcname, False, f"modbus exception: {e}")
            return

        elapsed = time.time() - st
        if elapsed > SLOW_CALL_THRESHOLD:
            self.debug(f"modbus {funcname} slow call: {elapsed:0.2f}s")

        if result is None:
            self._modbus_record(funcname, False, "no response (None)")
        elif hasattr(result, "isError") and result.isError():
            self._modbus_record(funcname, False, f"error response: {result}")
        else:
            self._modbus_record(funcname, True)
            # a successful call means the link is alive. clear a stale
            # simulation flag, e.g. set by an earlier failure that pymodbus
            # recovered from internally
            if getattr(self.communicator, "simulation", False):
                self.info(
                    f"modbus {funcname} succeeded while communicator flagged "
                    "simulation=True. clearing flag"
                )
                self.communicator.simulation = False

        return result

    def _read_coils(self, *args, **kw):
        return self._func("read_coils", *args, **kw)

    def _write_coil(self, *args, **kw):
        return self._func("write_coil", *args, **kw)

    def _read_holding_registers(self, *args, **kw):
        return self._func("read_holding_registers", *args, **kw)

    def _read_input_registers(self, *args, **kw):
        return self._func("read_input_registers", *args, **kw)

    def _write_register(self, *args, **kw):
        return self._func("write_register", *args, **kw)

    def _write_registers(self, *args, **kw):
        return self._func("write_registers", *args, **kw)


# ============= EOF =============================================
