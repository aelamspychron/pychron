import unittest
from types import SimpleNamespace
from unittest import mock

from pychron.spectrometer.thermo.magnet.base import ThermoMagnet


def det(name, deflection_name=None, threshold=0.1):
    return SimpleNamespace(
        name=name,
        deflection_name=deflection_name or name,
        protection_threshold=threshold,
    )


class FakeSpectrometer:
    def __init__(self, detectors):
        self.detectors = detectors

    def get_detector(self, name):
        return next((d for d in self.detectors if d.name == name), None)


def make_magnet(protected, detectors):
    m = ThermoMagnet()
    m.spectrometer = FakeSpectrometer(detectors)
    m.use_detector_protection = True
    m.protected_detectors = protected
    m.settling_time = 0
    m.ask = mock.Mock(
        side_effect=lambda cmd, **kw: "False" if cmd == "GetMagnetMoving" else "OK"
    )
    return m


def asked(m):
    return [c.args[0] for c in m.ask.call_args_list]


class ProtectedDetectorLookupTestCase(unittest.TestCase):
    def test_exact_name(self):
        m = make_magnet([], [det("AX(CDD)")])
        self.assertEqual(m._get_protected_detector("AX(CDD)").name, "AX(CDD)")

    def test_whitespace_around_name(self):
        # "H2, AX(CDD)" in config.cfg splits to " AX(CDD)"
        m = make_magnet([], [det("AX(CDD)")])
        self.assertEqual(m._get_protected_detector(" AX(CDD)").name, "AX(CDD)")

    def test_qtegra_deflection_name(self):
        m = make_magnet([], [det("AX(CDD)", "AX (CDD)")])
        self.assertEqual(m._get_protected_detector("AX (CDD)").name, "AX(CDD)")

    def test_space_insensitive(self):
        m = make_magnet([], [det("AX(CDD)")])
        self.assertEqual(m._get_protected_detector("AX (CDD)").name, "AX(CDD)")

    def test_unknown(self):
        m = make_magnet([], [det("AX(CDD)")])
        self.assertIsNone(m._get_protected_detector("L9"))


class SetDacProtectionTestCase(unittest.TestCase):
    def test_unknown_protected_detector_does_not_crash(self):
        m = make_magnet(["L9"], [det("AX(CDD)")])
        m.set_dac(1.0)
        self.assertIn("SetMagnetDAC 1.0", asked(m))

    def test_protect_uses_qtegra_deflection_name(self):
        m = make_magnet(["AX(CDD)"], [det("AX(CDD)", "AX (CDD)")])
        with mock.patch("pychron.spectrometer.thermo.magnet.base.time.sleep"):
            m.set_dac(1.0)
        cmds = asked(m)
        self.assertIn("ProtectDetector AX (CDD),On", cmds)
        self.assertIn("ProtectDetector AX (CDD),Off", cmds)


if __name__ == "__main__":
    unittest.main()
