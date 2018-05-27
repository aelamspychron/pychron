# ===============================================================================
# Copyright 2015 Jake Ross
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

from itertools import groupby
# ============= enthought library imports =======================
from operator import attrgetter

from numpy import array, array_split
from traits.api import Str, Enum
from traitsui.api import View, UItem, EnumEditor, VGroup, Item, HGroup, spring

from pychron.core.helpers.datetime_tools import bin_timestamps
from pychron.pipeline.nodes.base import BaseNode
from pychron.pipeline.subgrouping import apply_subgrouping, compress_groups
from pychron.pychron_constants import AGE_SUBGROUPINGS, SUBGROUPINGS, ERROR_TYPES, SUBGROUPING_ATTRS


def group_analyses_by_key(items, key, attr='group_id', id_func=None, sorting_enabled=True):
    if isinstance(key, str):
        keyfunc = lambda x: getattr(x, key)
    else:
        keyfunc = key

    ids = []
    for it in items:
        v = keyfunc(it)
        if v not in ids:
            ids.append(v)

    if sorting_enabled:
        items = sorted(items, key=keyfunc)

    for k, analyses in groupby(items, key=keyfunc):
        gid = ids.index(k)
        if id_func:
            gid = id_func(gid, analyses)
        for it in analyses:
            setattr(it, attr, gid)


class GroupingNode(BaseNode):
    by_key = Str
    keys = ('Aliquot', 'Identifier', 'Step', 'Comment', 'SubGroup', 'No Grouping')
    analysis_kind = 'unknowns'
    name = 'Grouping'
    title = 'Edit Grouping'

    _attr = 'group_id'
    _id_func = None

    sorting_enabled = True

    def load(self, nodedict):
        self.by_key = nodedict.get('key', 'Identifier')

    def _to_template(self, d):
        d['key'] = self.by_key

    def _generate_key(self):
        if self.by_key != 'No Grouping':
            return attrgetter(self.by_key.lower())

    def run(self, state):
        if self.by_key != 'No Grouping':
            unks = getattr(state, self.analysis_kind)
            for unk in unks:
                setattr(unk, self._attr, 0)

            group_analyses_by_key(unks, key=self._generate_key(), attr=self._attr, id_func=self._id_func,
                                  sorting_enabled=self.sorting_enabled)

    def traits_view(self):
        v = View(UItem('by_key',
                       style='custom',
                       editor=EnumEditor(name='keys')),
                 width=300,
                 title=self.title,
                 buttons=['OK', 'Cancel'],
                 kind='livemodal')
        return v


class GraphGroupingNode(GroupingNode):
    title = 'Edit Graph Grouping'
    name = 'Graphing Group'
    _attr = 'graph_id'


class SubGroupingNode(GroupingNode):
    title = 'Edit SubGrouping'
    keys = ('Aliquot', 'Identifier', 'Step', 'Comment', 'No Grouping')
    name = 'SubGroup'
    by_key = 'Aliquot'
    _attr = 'subgroup'

    age_kind = Enum(*AGE_SUBGROUPINGS)
    kca_kind = Enum(*SUBGROUPINGS)
    kcl_kind = Enum(*SUBGROUPINGS)
    rad40_percent_kind = Enum(*SUBGROUPINGS)
    moles_k39_kind = Enum(*SUBGROUPINGS)

    age_error_kind = Enum(*ERROR_TYPES)
    kca_error_kind = Enum(*ERROR_TYPES)
    kcl_error_kind = Enum(*ERROR_TYPES)
    rad40_percent_error_kind = Enum(*ERROR_TYPES)
    moles_k39_error_kind = Enum(*ERROR_TYPES)

    sorting_enabled = False

    def load(self, nodedict):
        self.by_key = nodedict.get('key', 'Aliquot')

    def _id_func(self, gid, analyses):
        attrs = ['{}_{}'.format(attr, tag) for attr in SUBGROUPING_ATTRS for tag in ('kind', 'error_kind')]

        grouping = {attr: getattr(self, attr) for attr in attrs}
        apply_subgrouping(grouping, list(analyses), gid=gid)

    def run(self, state):
        super(SubGroupingNode, self).run(state)

        ans = getattr(state, self.analysis_kind)
        compress_groups(ans)

    def traits_view(self):
        v = View(VGroup(VGroup(UItem('by_key',
                                     style='custom',
                                     editor=EnumEditor(name='keys')),
                               show_border=True, label='Grouping'),
                        VGroup(HGroup(Item('age_kind', label='Age'),
                                      spring,
                                      Item('age_error_kind', label='Error')),
                               HGroup(Item('kca_kind', label='K/Ca'),
                                      spring,
                                      Item('kca_error_kind', label='Error')),
                               HGroup(Item('kcl_kind', label='K/Cl'),
                                      spring,
                                      Item('kcl_error_kind', label='Error')),
                               HGroup(Item('rad40_percent_kind', label='%40Ar*'),
                                      spring,
                                      Item('rad40_percent_error_kind', label='Error')),
                               HGroup(Item('moles_k39_kind', label='mol 39K'),
                                      spring,
                                      Item('moles_k39_error_kind', label='Error')),
                               label='Types',
                               show_border=True)),
                 width=500,
                 resizable=True,
                 title=self.title,
                 buttons=['OK', 'Cancel'],
                 kind='livemodal')
        return v


class BinNode(BaseNode):
    analysis_kind = 'unknowns'

    def run(self, state):
        unks = getattr(state, self.analysis_kind)

        key = attrgetter('timestamp')
        unks = sorted(unks, key=key)

        tol_hrs = 1

        ts = array([ai.timestamp for ai in unks])

        idxs = bin_timestamps(ts, tol_hrs)
        if idxs:
            unks = array(unks)
            for i, ais in enumerate(array_split(unks, idxs + 1)):
                for ai in ais:
                    ai.group_id = i
        else:
            for ai in unks:
                ai.group_id = 0

# ============= EOF =============================================
