# ===============================================================================
# Copyright 2013 Jake Ross
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

# ============= enthought library imports =======================
from __future__ import absolute_import
import os
from configparser import ConfigParser, Error as ConfigParserError

from traits.api import List, Int, Instance

from pychron.core.helpers.color_generators import colornames
from pychron.core.helpers.filetools import add_extension
from pychron.experiment.automated_run.data_collector import DataCollector
from pychron.experiment.automated_run.hop_util import generate_hops
from pychron.pychron_constants import FAILED
from pychron.spectrometer import (
    get_spectrometer_config_name,
    get_spectrometer_config_path,
    set_spectrometer_config_name,
)
from six.moves import zip


class PeakHopCollector(DataCollector):
    """
    Collector class for doing a peak hop measurement. Measure one or more intensities at given mass for ncounts then
    jump magnet to next new mass.
    """

    hops = List
    settling_time = 0
    ncycles = Int
    hop_generator = None

    # seconds to wait after a spectrometer configuration change for source
    # parameters (HV, trap, deflections) to settle before measuring
    configuration_settle = Int(3)

    _was_deflected = False
    _detectors = None
    _current_configuration = None
    _original_configuration = None

    def set_hops(self, hops):
        self.hops = hops
        self.debug("make new hop generatior")
        self.hop_generator = generate_hops(self.hops)

    def measure(self):
        # validate every hop's spectrometer configuration before starting.
        # abort the run on an unknown configuration rather than failing mid-hop.
        if not self._validate_configurations():
            self.canceled = True
            if self.automated_run is not None:
                self.automated_run.cancel_run(state=FAILED, do_post_equilibration=False)
            return

        # remember the active configuration. each hop must set its configuration
        # explicitly; a hop without one rolls back to this original. the original
        # is also restored after the peak hop so the change doesn't leak into
        # subsequent runs/manual use.
        self._original_configuration = get_spectrometer_config_name()
        self._current_configuration = self._original_configuration
        try:
            return super(PeakHopCollector, self).measure()
        finally:
            self._restore_configuration()

    @staticmethod
    def _norm_configuration(name):
        return os.path.splitext(name)[0] if name else ""

    def _validate_configurations(self):
        """Return True if every hop's spectrometer configuration is available.

        Hops without a configuration are ignored. When no spectrometer is
        present (e.g. tests) validation is skipped.
        """
        spec = None
        arun = self.automated_run
        if arun is not None and arun.spectrometer_manager:
            spec = arun.spectrometer_manager.spectrometer

        available = set(spec.spectrometer_configurations) if spec else set()
        if not available:
            return True

        invalid = []
        for args in self.hops:
            name = args.get("configuration") if isinstance(args, dict) else None
            if name:
                base = os.path.splitext(name)[0]
                if base not in available:
                    invalid.append(name)

        if invalid:
            self.warning_dialog(
                "Unknown spectrometer configuration(s) in peak hop: {}\n\n"
                "Available configurations: {}".format(
                    ", ".join(sorted(set(invalid))), ", ".join(sorted(available))
                )
            )
            return False

        return True

    def _restore_configuration(self):
        """Restore the spectrometer configuration active before the peak hop.

        No-op when the current configuration already matches the original.
        """
        name = self._original_configuration
        if name is None:
            return
        if self._norm_configuration(self._current_configuration) == self._norm_configuration(name):
            return

        self.info("restoring spectrometer configuration '{}'".format(name))
        self._send_configuration(name)

    def _pre_trigger_hook(self):
        args = self._do_hop()

        if args:
            is_baseline, dets, isos = args
            self._detectors = dets
            return True

    def _iter_hook(self, i):
        return self._iteration(i, detectors=self._detectors)

        # args = self._do_hop()
        #
        # if args:
        #     is_baseline, dets, isos = args
        #     if not is_baseline:
        #         return self._iteration(i, detectors=dets)

    def _do_hop(self):
        """
        is it time for a magnet move
        """
        # from pychron.core.ui.gui import invoke_in_main_thread

        hop = next(self.hop_generator)
        hop_idx = hop["idx"]
        cycle = hop["cycle"]
        is_baseline = hop["is_baseline"]
        dets = hop["detectors"]
        isos = hop["isotopes"]
        defls = hop["deflections"]
        settle = hop["settle"]
        count = hop["count"]
        pdets = hop["protect_detectors"]
        active_dets = hop["active_detectors"]

        current_color = colornames[hop_idx]

        use_dac = False
        positioning = hop["positioning"]
        if positioning:
            if "dac" in positioning:
                use_dac = True
                isotope = positioning["dac"]
                detector = ""
            else:
                detector = positioning["detector"]
                isotope = positioning["isotope"]
        else:
            detector = active_dets[0]
            isotope = isos[0]

        if count == 0:
            self.debug("$$$$$$$$$$$$$$$$$ SETTING is_baseline {}".format(is_baseline))

        arun = self.automated_run

        # apply this hop's spectrometer configuration before positioning/deflecting.
        # a hop without a configuration rolls back to the original. only re-sends
        # when the target configuration differs from the current one.
        self._set_configuration(hop.get("configuration"))

        if is_baseline:
            arun.is_peak_hop = False
            # remember original settings. return to these values after baseline finished
            ocounts = self.measurement_script.ncounts
            arun.measurement_script.increment_series_count(2, 1)
            ocycles = self.plot_panel.ncycles
            pocounts = self.plot_panel.ncounts

            self.debug("START BASELINE MEASUREMENT {} {}".format(isotope, detector))
            arun.measurement_script.baselines(count, mass=isotope, detector=detector)
            self.debug("BASELINE MEASUREMENT COMPLETE")

            arun.measurement_script.increment_series_count(-2, -1)

            self._protect_detectors(pdets, True)
            change = arun.set_magnet_position(
                isotope,
                detector,
                use_dac=use_dac,
                update_detectors=False,
                update_labels=False,
                update_isotopes=True,
                remove_non_active=False,
            )
            if change:
                msg = "delaying {} for detectors to settle after peak hop".format(settle)
                arun.wait(settle, msg)
                self.debug(msg)
            self._protect_detectors(pdets, False)

            self.plot_panel._ncounts = pocounts
            self.measurement_script.ncounts = ocounts
            self.plot_panel.ncycles = ocycles
            arun.plot_panel.is_peak_hop = True
            arun.is_peak_hop = True
        else:
            # self.debug('c={} pc={} nc={}'.format(cycle, self.plot_panel.ncycles, self.ncycles))
            if self.plot_panel.ncycles != self.ncycles:
                if cycle >= self.plot_panel.ncycles:
                    self.info(
                        "user termination. measurement iteration executed {}/{} cycles".format(
                            cycle, self.ncycles
                        )
                    )
                    self.stop()
                    return
            elif cycle >= self.ncycles:
                return

            if count == 0:
                zd = list(zip(dets, defls))
                self.debug("Peak hop Detectors={}".format(dets))
                self.debug("Peak hop Deflections={}".format(defls))
                self.debug("Peak hop DeflectionsPairs={}".format(zd))
                # set deflections
                # only set deflections deflections were changed or need changing
                deflect = len([d for d in defls if d is not None])
                if deflect or self._was_deflected:
                    self._was_deflected = False
                    for det, defl in zd:
                        # use the measurement script to set the deflections
                        # this way defaults from the config can be used
                        if defl is not None:
                            self._was_deflected = True
                            arun.set_deflection(det, defl)

                self._protect_detectors(pdets)
                self.debug("----------------------- HOP {} {}".format(isotope, detector))
                change = arun.set_magnet_position(
                    isotope,
                    detector,
                    update_detectors=False,
                    update_labels=False,
                    update_isotopes=False,
                    # update_isotopes=not is_baseline,
                    remove_non_active=False,
                )

                self._protect_detectors(pdets, False)

                arun.update_detector_isotope_pairing(active_dets, isos)
                if change:
                    g = self.plot_panel.isotope_graph
                    if hop_idx:
                        for d in active_dets:
                            det = arun.get_detector(d)

                            plot = g.get_plot_by_ytitle("{}{}".format(det.isotope, det.name))
                            if not plot:
                                plot = g.get_plot_by_ytitle(det.isotope)

                            if plot:
                                scatter = plot.plots["data{}".format(self.fit_series_idx)][0]
                                scatter.color = current_color
                                scatter.outline_color = current_color
                            else:
                                self.debug("could not locate det={} iso={}".format(d, det.isotope))

                    try:
                        arun.plot_panel.counts += int(settle)
                    except AttributeError:
                        pass

                    msg = "delaying {} for detectors to settle after peak hop".format(settle)
                    arun.wait(settle, msg)
                    self.debug(msg)

            # self.debug('cycle {} count {} {}'.format(cycle, count, id(self)))
            if self.plot_panel.is_baseline:
                isotope = "{}bs".format(isotope)

            dac = arun.get_current_dac()
            # invoke_in_main_thread(self.plot_panel.trait_set,
            #                       current_cycle='{}({:0.6f}) - {} cyc={} cnt={}'.format(isotope, dac, detector,
            #                                                                             cycle + 1, count + 1),
            #                       current_color=current_color)
            self.plot_panel.trait_set(
                current_cycle="{}({:0.6f}) - {} cyc={} cnt={}".format(
                    isotope, dac, detector, cycle + 1, count + 1
                ),
                current_color=current_color,
            )
        return is_baseline, active_dets, isos

    def _set_configuration(self, name):
        """Apply this hop's spectrometer configuration.

        A hop must set its configuration explicitly; when ``name`` is empty the
        configuration rolls back to the original active at the start of the peak
        hop. No-op when the target already matches the current configuration.
        """
        target = name or self._original_configuration
        if not target or self._norm_configuration(target) == self._norm_configuration(
            self._current_configuration
        ):
            return

        self.info("setting spectrometer configuration '{}'".format(target))
        self._send_configuration(target)

    def _send_configuration(self, name):
        set_spectrometer_config_name(name)
        arun = self.automated_run
        try:
            arun.py_clear_cached_configuration()
            arun.py_send_spectrometer_configuration()
        except BaseException as e:
            self.warning("failed sending spectrometer configuration '{}': {}".format(name, e))
            return

        self._current_configuration = name

        # the configuration's mftable takes precedence over the peak_hop-level
        # mftable. apply it explicitly here so this holds on every spectrometer
        # driver (e.g. the isotopx send_configuration is a no-op).
        self._apply_configuration_mftable(name)

        settle = self.configuration_settle
        if settle:
            msg = "delaying {} for spectrometer to settle after " "configuration change".format(
                settle
            )
            self.debug(msg)
            arun.wait(settle, msg)

    def _apply_configuration_mftable(self, name):
        """Apply the mftable declared in the configuration, if any.

        Makes the configuration's mftable win over the peak_hop-level mftable
        regardless of spectrometer driver.
        """
        mftable = self._configuration_mftable(name)
        if not mftable:
            return

        self.info("configuration '{}' mftable '{}' takes precedence".format(name, mftable))
        self.automated_run.ion_optics_manager.set_mftable(mftable)

    def _configuration_mftable(self, name):
        """Read [Magnet] mftable from the configuration file. None if absent."""
        p = get_spectrometer_config_path(add_extension(name, ".cfg"))
        if not p or not os.path.isfile(p):
            return

        config = ConfigParser()
        try:
            config.read(p)
        except (OSError, ConfigParserError):
            self.warning("failed reading configuration '{}'".format(p))
            return

        if config.has_option("Magnet", "mftable"):
            return config.get("Magnet", "mftable")

    def _protect_detectors(self, pdets, protect=True):
        for pd in pdets:
            self.automated_run.protect_detector(pd, protect)


# ============= EOF =============================================
