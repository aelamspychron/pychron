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
from __future__ import print_function
import re
import time

from chaco.array_data_source import ArrayDataSource
from numpy import array, Inf, arange
from traits.api import Array
from uncertainties import nominal_value, std_dev

from pychron.experiment.utilities.identifier import ANALYSIS_MAPPING_INTS
from pychron.pipeline.plot.flow_label import FlowPlotLabel
from pychron.pipeline.plot.plotter.arar_figure import BaseArArFigure
from pychron.pipeline.plot.plotter.ticks import TICKS
from pychron.pychron_constants import PLUSMINUS, SIGMA

N = 500

PEAK_CENTER = 'Peak Center'
ANALYSIS_TYPE = 'Analysis Type'
RADIOGENIC_YIELD = 'RadiogenicYield'
LAB_TEMP = 'Lab Temperature'
LAB_HUM = 'Lab Humidity'
LAB_AIRPRESSUE = 'Lab Air Pressure'
AGE = 'Age'

ATTR_MAPPING = {PEAK_CENTER: 'peak_center',
                AGE: 'uage',
                RADIOGENIC_YIELD: 'rad40_percent',
                LAB_TEMP: 'lab_temperature',
                LAB_HUM: 'lab_humidity',
                LAB_AIRPRESSUE: 'lab_airpressure'}

AR4039 = 'Ar40/Ar39'
UAR4039 = 'uAr40/Ar39'
AR3839 = 'Ar38/Ar39'
UAR3839 = 'uAr38/Ar39'
AR3739 = 'Ar37/Ar39'
UAR3739 = 'uAr37/Ar39'
AR3639 = 'Ar36/Ar39'
UAR3639 = 'uAr36/Ar39'

AR4038 = 'Ar40/Ar38'
UAR4038 = 'uAr40/Ar38'
AR3738 = 'Ar37/Ar38'
UAR3738 = 'uAr37/Ar38'
AR3638 = 'Ar36/Ar38'
UAR3638 = 'uAr36/Ar38'

AR4037 = 'Ar40/Ar37'
UAR4037 = 'uAr40/Ar37'
AR3637 = 'Ar36/Ar37'
UAR3637 = 'uAr36/Ar37'

AR4036 = 'Ar40/Ar36'
UAR4036 = 'uAr40/Ar36'


class BaseSeries(BaseArArFigure):
    xs = Array

    def max_x(self, *args):
        if len(self.xs):
            return max(self.xs)
        return -Inf

    def min_x(self, *args):
        if len(self.xs):
            return min(self.xs)
        return Inf

    def mean_x(self, *args):
        if len(self.xs):
            return self.xs.mean()
        return 0

    def _get_xs(self, plots, ans, tzero=None):

        if self.options.use_time_axis:
            xs = array([ai.timestamp for ai in ans])
            px = plots[0]
            if tzero is None:
                if px.normalize == 'now':
                    tzero = time.time()
                else:
                    tzero = xs[-1]

            xs -= tzero
            if not px.use_time_axis:
                xs /= 3600.
            else:
                self.graph.convert_index_func = lambda x: '{:0.2f} hrs'.format(x / 3600.)
        else:
            xs = arange(len(ans))

        return xs

    def _handle_limits(self):
        self.graph.refresh()


RATIO_RE = re.compile(r'(?P<ni>[A-Za-z]+)(?P<nd>\d+)\/(?P<di>[A-Za-z]+)(?P<dd>\d+)(?P<rem>[\S\s]*)')
ISOTOPE_RE = re.compile(r'(?P<ni>[A-Za-z]+)(?P<nd>\d+)(?P<rem>[\S\s]*)')


class Series(BaseSeries):
    # _omit_key = 'omit_series'

    # def _has_attr(self, name):
    #     a = name in (ANALYSIS_TYPE, PEAK_CENTER, AGE, RADIOGENIC_YIELD)
    #     if not a:
    #         if self.sorted_analyses:
    #             ai = self.sorted_analyses[0]
    #             a = bool(ai.get_value(name))
    #     return a

    def build(self, plots):

        graph = self.graph
        # plots = (pp for pp in plots if self._has_attr(pp.name))

        for i, po in enumerate(plots):
            ytitle = po.name
            if po.use_dev:
                ytitle = '{} Dev'.format(ytitle)
            elif po.use_percent_dev:
                ytitle = '{} Dev %'.format(ytitle)

            kw = {'padding': self.options.paddings(),
                  'ytitle': ytitle}

            if self.options.use_time_axis:
                kw['xtitle'] = 'Time (hrs)'
            else:
                kw['xtitle'] = 'N'

            p = graph.new_plot(**kw)

            if i == 0:
                self._add_info(p)

            if po.name == ANALYSIS_TYPE:
                from pychron.pipeline.plot.plotter.ticks import tick_formatter, StaticTickGenerator

                p.y_axis.tick_label_formatter = tick_formatter
                p.y_axis.tick_generator = StaticTickGenerator()
                # p.y_axis.tick_label_rotate_angle = 45
                graph.set_y_limits(-0.5, len(TICKS) - 0.5, plotid=i)
                # graph.set_y_limits(min_=-1, max_=7, plotid=i)

            p.value_range.tight_bounds = False
            self._setup_plot(i, p, po, ytitle)

    def _setup_plot(self, pid, pp, po, ytitle):

        match = RATIO_RE.match(ytitle)
        if match:
            ytitle = '<sup>{}</sup>{}/<sup>{}</sup>{}'.format(match.group('nd'),
                                                              match.group('ni'),
                                                              match.group('dd'),
                                                              match.group('di'))
            if match.group('rem'):
                ytitle = '{}{}'.format(ytitle, match.group('rem'))
        else:
            match = ISOTOPE_RE.match(ytitle)
            if match:
                ytitle = '<sup>{}</sup>{}'.format(match.group('nd'), match.group('ni'))
                if match.group('rem'):
                    ytitle = '{}{}'.format(ytitle, match.group('rem'))

        super(Series, self)._setup_plot(pid, pp, po)
        if '<sup>' in ytitle or '<sub>' in ytitle:
            self._set_ml_title(ytitle, pid, 'y')
        else:
            self.graph.set_y_title(ytitle, plotid=pid)

    def plot(self, plots, legend=None):
        """
            plot data on plots
        """
        # plots = (pp for pp in plots if self._has_attr(pp.name))

        omits = self._get_omitted_by_tag(self.sorted_analyses)
        for o in omits:
            self.sorted_analyses[o].set_temp_status('omit')

        if plots:
            self.xs = self._get_xs(plots, self.sorted_analyses)
            # with graph.no_regression(refresh=True):
            # plots = [po for po in plots if po.use]
            for i, po in enumerate(plots):
                self._plot_series(po, i, omits)

            self.xmi, self.xma = self.min_x(), self.max_x()
            self.xpad = '0.1'

    def _plot_series(self, po, pid, omits):
        graph = self.graph
        try:
            if po.name == ANALYSIS_TYPE:
                from pychron.pipeline.plot.plotter.ticks import analysis_type_formatter

                ys = list(self._unpack_attr(po.name))
                kw = dict(y=ys, colors=ys, type='cmap_scatter',
                          fit='',
                          color_map_name='gist_rainbow')
                yerr = None
                value_format = analysis_type_formatter
                set_ylimits = False

            else:
                set_ylimits = True
                value_format = None
                ys = array([nominal_value(ai) for ai in self._unpack_attr(po.name)])
                yerr = array([std_dev(ai) for ai in self._unpack_attr(po.name)])

                if po.use_dev or po.use_percent_dev:
                    graph.add_horizontal_rule(0, plotid=pid, color='black', line_style='solid')
                    m = ys.mean()
                    ys = ys - m
                    if po.use_percent_dev:
                        ys = ys / m * 100
                        yerr = yerr / m * 100

                kw = dict(y=ys, yerror=yerr, type='scatter',
                          fit='{}_{}'.format(po.fit, po.error_type),
                          filter_outliers_dict=po.filter_outliers_dict)

            n = [ai.record_id for ai in self.sorted_analyses]
            args = graph.new_series(x=self.xs,
                                    display_index=ArrayDataSource(data=n),
                                    plotid=pid,
                                    add_tools=True,
                                    add_inspector=True,
                                    add_point_inspector=False,
                                    marker=po.marker,
                                    marker_size=po.marker_size,
                                    **kw)
            if len(args) == 2:
                scatter, p = args
            else:
                p, scatter, l = args

            sel = scatter.index.metadata.get('selections', [])
            sel += omits
            scatter.index.metadata['selections'] = list(set(sel))

            def af(i, x, y, analysis):
                return ('Run Date: {}'.format(analysis.rundate.strftime('%m-%d-%Y %H:%M')),
                        'Rel. Time: {:0.4f}'.format(x))

            self._add_scatter_inspector(scatter,
                                        add_selection=False,
                                        additional_info=af,
                                        value_format=value_format)

            # if po.use_time_axis:
            #     p.x_axis.tick_generator = ScalesTickGenerator(scale=CalendarScaleSystem())

            if po.y_error and yerr is not None:
                s = self.options.error_bar_nsigma
                ec = self.options.end_caps
                self._add_error_bars(scatter, yerr, 'y', s, ec, visible=True)

            if set_ylimits:
                mi, mx = min(ys - 2 * yerr), max(ys + 2 * yerr)
                graph.set_y_limits(min_=mi, max_=mx, pad='0.1', plotid=pid)

        except (KeyError, ZeroDivisionError, AttributeError) as e:
            import traceback

            traceback.print_exc()
            print('Series', e)

    def update_graph_metadata(self, obj, name, old, new):
        sorted_ans = self.sorted_analyses
        if obj:
            sel = self._filter_metadata_changes(obj, sorted_ans)
            for p in self.graph.plots:
                p.default_index.metadata['selections'] = sel

    # private
    def _add_info(self, plot):
        if self.group_id == 0:
            if self.options.show_info:
                ts = [u'Data {}{}{}'.format(PLUSMINUS, self.options.error_bar_nsigma, SIGMA)]

                if ts:
                    pl = FlowPlotLabel(text='\n'.join(ts),
                                       overlay_position='inside top',
                                       hjustify='left',
                                       component=plot)
                    plot.overlays.append(pl)

    def _unpack_attr(self, attr):
        if attr == ANALYSIS_TYPE:
            def f(x):
                x = x.analysis_type
                return ANALYSIS_MAPPING_INTS[x] if x in ANALYSIS_MAPPING_INTS else -1

            return (f(ai) for ai in self.sorted_analyses)

        elif attr in ATTR_MAPPING:
            attr = ATTR_MAPPING[attr]

        return super(Series, self)._unpack_attr(attr)

# ============= EOF =============================================
