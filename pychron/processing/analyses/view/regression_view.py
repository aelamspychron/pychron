# ===============================================================================
# Copyright 2018 ross
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
from chaco.plot_containers import HPlotContainer
from enable.component_editor import ComponentEditor
from traits.api import HasTraits, Instance
from traitsui.api import View, UItem

from pychron.graph.stacked_graph import StackedGraph
from pychron.graph.stacked_regression_graph import StackedRegressionGraph


class RegressionView(HasTraits):
    name = 'Regressions'
    container = Instance(HPlotContainer)

    def initialize(self, an):
        an.load_raw_data()
        self.setup_graph(an)

    def setup_graph(self, an):

        container = HPlotContainer()

        container_dict = {'spacing': 5, 'stack_order': 'top_to_bottom'}
        sg = StackedGraph(container_dict=container_dict)
        sg.plotcontainer.spacing = 5
        sg.plotcontainer.stack_order = 'top_to_bottom'

        isos = an.sorted_values(reverse=False)
        add_sniff = True

        sisos = [iso for iso in isos if iso.sniff.offset_xs.shape[0]]
        for i, iso in enumerate(sisos):
            sniff = iso.sniff
            sg.new_plot(ytitle=iso.name, xtitle='Time (s)', title='Equilibration')
            sg.new_series(sniff.offset_xs, sniff.ys, marker='circle', type='scatter')
            sg.set_y_limits(pad='0.1', plotid=i)
            sg.set_x_limits(min_=0, max_=max(sniff.offset_xs) * 1.05, plotid=i)

        bg = StackedRegressionGraph(container_dict=container_dict)
        add_baseline = True

        ig = StackedRegressionGraph(container_dict=container_dict)

        iisos = [iso for iso in isos if iso.offset_xs.shape[0]]
        baselines = []
        for i, iso in enumerate(iisos):
            if iso.baseline.offset_xs.shape[0]:
                baselines.append(iso.baseline)
            ig.new_plot(ytitle='{}({})'.format(iso.name, iso.detector), xtitle='Time (s)', title='Isotope')
            ig.new_series(iso.offset_xs, iso.ys,
                          display_filter_bounds=True,
                          filter_outliers_dict=iso.filter_outliers_dict,
                          color='blue', type='scatter', fit=iso.efit)
            ig.set_regressor(iso.regressor, i)
            ig.set_y_limits(pad='0.1', plotid=i)
            ig.set_x_limits(min_=0, max_=max(iso.offset_xs) * 1.05, plotid=i)

        ig.refresh()

        # bisos = [iso for iso in isos if iso.baseline.offset_xs.shape[0]]
        # plotted_baselines = []
        # for i, iso in enumerate(bisos):
            # baseline = iso.baseline
            # if baseline.detector in plotted_baselines:
            #     continue
            # plotted_baselines.append(baseline.detector)

        # for iso in bisos:
        for i, baseline in enumerate(baselines):

            bg.new_plot(ytitle=baseline.detector, xtitle='Time (s)', title='Baseline')
            bg.new_series(baseline.offset_xs, baseline.ys,
                          filter_outliers_dict=baseline.filter_outliers_dict,
                          display_filter_bounds=True,
                          color='red', type='scatter', fit=baseline.efit)
            bg.set_regressor(baseline.regressor, i)
            bg.set_y_limits(pad='0.1', plotid=i)
            bg.set_x_limits(pad='0.025', plotid=i)

        bg.refresh()

        container.add(sg.plotcontainer)
        container.add(ig.plotcontainer)
        container.add(bg.plotcontainer)

        self.container = container

    def traits_view(self):
        v = View(UItem('container', style='custom', editor=ComponentEditor()),
                 resizable=True)
        return v

# ============= EOF =============================================
