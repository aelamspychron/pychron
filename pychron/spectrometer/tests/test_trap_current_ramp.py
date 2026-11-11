import unittest
from unittest import mock

from pychron.core.ramper import StepRamper
from pychron.spectrometer.thermo.source.base import ThermoSource
from pychron.spectrometer.thermo.spectrometer.base import ThermoSpectrometer


class StepRamperTestCase(unittest.TestCase):
    def _ramp(self, start, end, step, **kw):
        calls = []

        def func(x):
            calls.append(x)
            return True

        with mock.patch("pychron.core.ramper.time.sleep"):
            StepRamper().ramp(func, start, end, step, period=0, **kw)
        return calls

    def test_include_start_default(self):
        self.assertEqual(self._ramp(200, 197, 1), [200, 199, 198, 197])

    def test_exclude_start_down(self):
        self.assertEqual(self._ramp(200, 197, 1, include_start=False), [199, 198, 197])

    def test_exclude_start_up(self):
        self.assertEqual(self._ramp(125, 128, 1, include_start=False), [126, 127, 128])


class FakeSource:
    def __init__(self, setpoint, readback):
        self.setpoint = setpoint
        self.readback = readback
        self.writes = []

    def read_trap_current_setpoint(self):
        return self.setpoint

    def read_trap_current_readback(self):
        return self.readback

    @property
    def trap_current(self):
        return self.writes[-1] if self.writes else None

    @trap_current.setter
    def trap_current(self, v):
        self.writes.append(v)


class FakeSpectrometer:
    def __init__(self, source):
        self.source = source
        self.warnings = []

    def debug(self, *args, **kw):
        pass

    def warning(self, msg, *args, **kw):
        self.warnings.append(msg)

    def confirmation_dialog(self, *args, **kw):
        return True

    _get_trap_ramp_start = ThermoSpectrometer._get_trap_ramp_start


def ramp(spec, v, step=1, tol=10, setpoint_tol=None):
    with mock.patch("pychron.core.ramper.time.sleep"):
        return ThermoSpectrometer._ramp_trap_current(
            spec, v, step, 0, True, tol, setpoint_tol=setpoint_tol
        )


class TrapCurrentRampTestCase(unittest.TestCase):
    def _ramp(self, setpoint, readback, v, **kw):
        src = FakeSource(setpoint=setpoint, readback=readback)
        spec = FakeSpectrometer(src)
        ramp(spec, v, **kw)
        return src.writes, spec.warnings

    def test_ramp_starts_from_setpoint_not_readback(self):
        # Neon -> Argon: setpoint 200, readback overshoots to 201
        writes, _ = self._ramp(200.0, 201.0, 125.0, step=25)
        self.assertEqual(writes, [175.0, 150.0, 125.0])

    def test_never_writes_value_above_start_when_ramping_down(self):
        writes, _ = self._ramp(200.0, 201.0, 125.0)
        self.assertTrue(max(writes) < 200.0)
        self.assertEqual(writes[-1], 125.0)

    def test_setpoint_and_readback_disagree_ramps_from_readback(self):
        # setpoint says 200 but the trap is really at 100
        writes, warnings = self._ramp(200.0, 100.0, 125.0, step=5)
        self.assertEqual(writes, [105.0, 110.0, 115.0, 120.0, 125.0])
        self.assertTrue(warnings)

    def test_transient_readback_above_setpoint_ramping_down(self):
        # log 14:17:21: He -> Ar, trap voltage just changed, readback spiked to 459
        writes, warnings = self._ramp(400.0, 459.173, 200.0, step=10)
        self.assertEqual(writes[0], 390.0)
        self.assertTrue(max(writes) < 400.0)
        self.assertEqual(writes[-1], 200.0)
        self.assertTrue(warnings)

    def test_transient_readback_below_setpoint_ramping_up(self):
        # log 14:16:16: setpoint 125, readback dipped to 107.5, target 400
        writes, _ = self._ramp(125.0, 107.521, 400.0, step=20)
        self.assertEqual(writes[0], 145.0)
        self.assertTrue(min(writes) > 125.0)
        self.assertEqual(writes[-1], 400.0)

    def test_writes_are_rounded(self):
        writes, _ = self._ramp(107.521, 107.521, 400.0, step=20)
        for w in writes:
            self.assertEqual(w, round(w, 3))
        self.assertIn(147.521, writes)

    def test_setpoint_tolerance_is_configurable(self):
        writes, _ = self._ramp(200.0, 190.0, 125.0, step=25, setpoint_tol=5)
        self.assertEqual(writes, [165.0, 125.0])

    def test_unreadable_setpoint_ramps_from_readback(self):
        writes, _ = self._ramp(None, 190.0, 125.0, step=25)
        self.assertEqual(writes, [165.0, 125.0])

    def test_unreadable_readback_ramps_from_setpoint(self):
        writes, _ = self._ramp(200.0, None, 125.0, step=25)
        self.assertEqual(writes, [175.0, 150.0, 125.0])

    def test_nothing_readable_skips_ramp(self):
        writes, warnings = self._ramp(None, None, 125.0)
        self.assertEqual(writes, [])
        self.assertTrue(warnings)

    def test_close_to_target_but_setpoint_stale_sets_directly(self):
        # trap really at 100, target 105 (within ramp tolerance), setpoint stale at 200
        writes, _ = self._ramp(200.0, 100.0, 105.0)
        self.assertEqual(writes, [105.0])

    def test_already_at_target_does_nothing(self):
        writes, _ = self._ramp(125.0, 125.4, 125.0)
        self.assertEqual(writes, [])


class ThermoSourceSetpointTestCase(unittest.TestCase):
    def _source(self, response):
        src = ThermoSource()
        src.ask = mock.Mock(return_value=response)
        return src

    def test_reads_trap_current_set(self):
        src = self._source("200.0")
        self.assertEqual(src.read_trap_current_setpoint(), 200.0)
        src.ask.assert_called_once_with("GetParameter Trap Current Set", verbose=True)

    def test_bad_response_returns_none(self):
        for resp in (None, "Error: invalid parameter", ""):
            self.assertIsNone(self._source(resp).read_trap_current_setpoint())
            self.assertIsNone(self._source(resp).read_trap_current_readback())

    def test_reads_trap_current_readback(self):
        src = self._source("100.5")
        self.assertEqual(src.read_trap_current_readback(), 100.5)
        src.ask.assert_called_once_with(
            "GetParameter Trap Current Readback", verbose=True
        )
        self.assertEqual(src.trap_current, 100.5)


if __name__ == "__main__":
    unittest.main()
