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

from datetime import timedelta, datetime

import six
from sqlalchemy import not_, func, distinct, or_, select, and_, join
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.functions import count
from sqlalchemy.util import OrderedSet
# ============= enthought library imports =======================
from traits.api import HasTraits, Str, List
from traitsui.api import Item

from pychron.core.helpers.datetime_tools import bin_datetimes
from pychron.core.helpers.traitsui_shortcuts import okcancel_view
from pychron.core.spell_correct import correct
from pychron.core.utils import alpha_to_int
from pychron.database.core.database_adapter import DatabaseAdapter, binfunc
from pychron.database.core.query import compile_query, in_func
from pychron.dvc.dvc_orm import AnalysisTbl, ProjectTbl, MassSpectrometerTbl, \
    IrradiationTbl, LevelTbl, SampleTbl, \
    MaterialTbl, IrradiationPositionTbl, UserTbl, ExtractDeviceTbl, \
    LoadTbl, LoadPositionTbl, MeasuredPositionTbl, \
    VersionTbl, RepositoryAssociationTbl, \
    RepositoryTbl, AnalysisChangeTbl, \
    PrincipalInvestigatorTbl, SamplePrepWorkerTbl, SamplePrepSessionTbl, \
    SamplePrepStepTbl, SamplePrepImageTbl, RestrictedNameTbl, AnalysisGroupTbl, AnalysisGroupSetTbl, \
    AnalysisIntensitiesTbl, SimpleIdentifierTbl, SamplePrepChoicesTbl
from pychron.globals import globalv
from pychron.pychron_constants import NULL_STR, EXTRACT_DEVICE, NO_EXTRACT_DEVICE, \
    SAMPLE_PREP_STEPS, SAMPLE_METADATA


def listify(obj):
    if obj:
        if not isinstance(obj, (tuple, list)):
            obj = (obj,)
    return obj


def make_filter(qq, table, col='value'):
    comp = qq.comparator
    v = qq.criterion
    if comp == '<':
        ffunc = lambda col: col.__lt__(v)
    elif comp == '>':
        ffunc = lambda col: col.__gt__(v)
    elif comp == '>=':
        ffunc = lambda col: col.__ge__(v)
    elif comp == '<=':
        ffunc = lambda col: col.__le__(v)
    elif comp == '==':
        ffunc = lambda col: col.__eq__(v)
    elif comp == '!=':
        ffunc = lambda col: col.__ne__(v)

    nclause = ffunc(getattr(table, col))

    chain = ''
    if qq.show_chain:
        chain = qq.chain_operator

    return nclause, chain


def compress_times(times, delta):
    times = sorted(times)

    low = times[0] - delta
    high = times[0] + delta

    for ti in times[1:]:
        if ti - delta < high:
            continue

        yield low, high
        low = high
        high = ti + delta

    yield low, high


def principal_investigator_filter(q, principal_investigator):
    if ',' in principal_investigator:
        try:
            ln, fi = principal_investigator.split(',')
            q = q.filter(PrincipalInvestigatorTbl.last_name == ln.strip())
            q = q.filter(PrincipalInvestigatorTbl.first_initial == fi.strip())
        except ValueError:
            pass
    else:
        q = q.filter(PrincipalInvestigatorTbl.last_name == principal_investigator)

    return q


def make_at_filter(analysis_types):
    if isinstance(analysis_types, (tuple, list)):
        analysis_types = [at.lower() for at in analysis_types]
    else:
        analysis_types = (analysis_types.lower(),)

    analysis_types = [xi.replace(' ', '_') for xi in analysis_types]

    if 'blank' in analysis_types:
        ret = or_(AnalysisTbl.analysis_type.startswith('blank'),
                  AnalysisTbl.analysis_type.in_(analysis_types))
    else:
        ret = AnalysisTbl.analysis_type.in_(analysis_types)

    return ret


def analysis_type_filter(q, analysis_types):
    ret = make_at_filter(analysis_types)
    q = q.filter(ret)
    return q


class NewMassSpectrometerView(HasTraits):
    name = Str
    kind = Str

    def traits_view(self):
        v = okcancel_view(Item('name'),
                          Item('kind'),
                          title='New Mass Spectrometer')
        return v


def exclude_invalid_analyses(q):
    return q.filter(AnalysisChangeTbl.tag != 'invalid')


def extract_devices_query(analysis_types, extract_devices, q):
    if extract_devices and ('air' not in analysis_types and 'cocktail' not in analysis_types):
        a = any((a in analysis_types for a in ('air', 'cocktail', 'blank_air', 'blank_cocktail')))
        if not a:
            extract_devices = listify(extract_devices)
            es = [ei.lower() for ei in extract_devices if ei not in (EXTRACT_DEVICE, NO_EXTRACT_DEVICE, NULL_STR)]
            if es:
                q = in_func(q, AnalysisTbl.extract_device, es)
    return q


class DVCDatabase(DatabaseAdapter):
    """
    mysql2sqlite
    https://gist.github.com/esperlu/943776


    update local database
    when pushing
    1. pull remote database file and merge with local
       a. pull remote to path.remote (rsync remote path.remote)
       b. create merged database at path.merge
       c. rsync path.merge path
    2. push local to remote
       a. rsync lpath remote


    """

    # test_func = 'get_database_version'

    irradiation = Str
    irradiations = List
    level = Str
    levels = List

    def __init__(self, clear=False, auto_add=False, *args, **kw):
        super(DVCDatabase, self).__init__(*args, **kw)

        if auto_add:
            if self.connect():
                with self.session_ctx():
                    if not self.get_mass_spectrometers():
                        if auto_add:
                            self.add_mass_spectrometer('Jan', 'ArgusVI')
                        else:
                            while 1:
                                self.information_dialog(
                                    'No Mass spectrometer in the database. Add one now')
                                nv = NewMassSpectrometerView(name='Jan',
                                                             kind='ArgusVI')
                                info = nv.edit_traits()
                                if info.result:
                                    self.add_mass_spectrometer(nv.name, nv.kind)
                                    break

                    if not self.get_users():
                        self.add_user('root')

    def modify_aliquot_step(self, uuid, aliquot, increment):
        with self.session_ctx() as sess:
            a = self.get_analysis_uuid(uuid)
            a.aliquot = aliquot
            a.increment = increment
            sess.commit()

    def sync_ia_metadata(self, ia):
        identifier = ia.identifier
        info = self.get_identifier_info(identifier)
        if info:
            for attr in SAMPLE_METADATA:
                setattr(ia, attr, info.get(attr))

    def check_restricted_name(self, name, category, check_principal_investigator=False):
        """
        return True is name is restricted

        """
        with self.session_ctx() as sess:
            q = sess.query(RestrictedNameTbl)
            q = q.filter(RestrictedNameTbl.name == name.upper())
            q = q.filter(RestrictedNameTbl.category == category)

            ret = bool(self._query_one(q))
            if check_principal_investigator:
                q = sess.query(PrincipalInvestigatorTbl)
                lname = func.lower(PrincipalInvestigatorTbl.name)
                name = name.lower()
                q = q.filter(func.substring(lname, 2) == name)
                q = q.filter(or_(lname == name))

                print(q)
                pret = bool(self._query_one(q, verbose_query=True))
                ret = pret or ret

            return ret

    def add_simple_identifier(self, sid):
        with self.session_ctx():
            obj = SimpleIdentifierTbl()
            obj.sampleID = sid
            self._add_item(obj)

    def get_simple_identifier(self, sid):
        with self.session_ctx() as sess:
            q = sess.query(SimpleIdentifierTbl)
            q = q.join(SampleTbl)
            q = q.filter(SampleTbl.id == sid)
            return self._query_one(q)

    def get_sample_simple_identifiers(self, sid):
        with self.session_ctx() as sess:
            q = sess.query(SimpleIdentifierTbl)
            q = q.filter(SampleTbl.id == sid)
            return self._query_all(q)

    def get_simple_identifiers(self):
        with self.session_ctx() as sess:
            q = sess.query(SimpleIdentifierTbl)
            return self._query_all(q)

    def get_repository_analyses(self, repo):
        with self.session_ctx():
            r = self.get_repository(repo)
            return [a.analysis for a in r.repository_associations]

    def get_identifier_info(self, li):
        with self.session_ctx():
            dbpos = self.get_identifier(li)
            if not dbpos:
                self.warning('{} is not an identifier in the database'.format(li))
                return None
            else:
                info = {}
                sample = dbpos.sample
                if sample:
                    if sample.project:
                        project = sample.project.name
                        info['project'] = project
                        if sample.project.principal_investigator:
                            pi = sample.project.principal_investigator.name
                            info['principal_investigator'] = pi

                    if sample.material:
                        material = sample.material.name
                        info['material'] = material
                        info['grainsize'] = sample.material.grainsize or ''

                    info['sample'] = sample.name
                    info['latitude'] = sample.lat
                    info['longitude'] = sample.lon
                    info['unit'] = sample.unit
                    info['lithology'] = sample.lithology
                    info['lithology_class'] = sample.lithology_class
                    info['lithology_type'] = sample.lithology_type
                    info['lithology_group'] = sample.lithology_group

                    # todo: add rlocatiion/reference to database
                    info['rlocation'] = ''
                    info['reference'] = ''

                info['irradiation_level'] = dbpos.level.name
                info['irradiation_position'] = dbpos.position
                info['irradiation'] = dbpos.level.irradiation.name

            return info

    def set_analysis_tag(self, item, tagname):
        with self.session_ctx() as sess:
            an = self.get_analysis_uuid(item.uuid)
            if an is None:
                an = self.get_analysis_runid(item.identifier, item.aliquot, item.step)

            change = an.change
            change.tag = tagname
            change.user = self.save_username
            sess.add(change)

    def find_references_by_load(self, load, analysis_types, extract_devices=None, mass_spectrometers=None,
                                exclude_invalid=True):
        with self.session_ctx() as sess:
            self.debug('----------- find references by load ------------')
            self.debug('load={}'.format(load))
            self.debug('analysis_types={}'.format(analysis_types))
            self.debug('extract devices={}'.format(extract_devices))
            self.debug('mass_spectrometers={}'.format(mass_spectrometers))
            self.debug('------------------------------------------------')
            q = sess.query(AnalysisTbl)
            q = q.join(AnalysisChangeTbl)
            q = q.join(MeasuredPositionTbl)

            q = q.filter(MeasuredPositionTbl.loadName == load)

            if mass_spectrometers:
                q = in_func(q, AnalysisTbl.mass_spectrometer, mass_spectrometers)
            if extract_devices:
                q = extract_devices_query(analysis_types, extract_devices, q)
            if analysis_types:
                q = analysis_type_filter(q, analysis_types)

            if exclude_invalid:
                q = exclude_invalid_analyses(q)

            records = self._query_all(q, verbose_query=True)
            return records

    def find_references(self, times, atypes, hours=10, exclude=None,
                        extract_devices=None,
                        mass_spectrometers=None,
                        exclude_invalid=True):

        with self.session_ctx():
            delta = timedelta(hours=hours)
            refs = OrderedSet()
            ex = None

            times = [ti if isinstance(ti, datetime) else ti.rundate for ti in times]
            ctimes = list(bin_datetimes(times, delta))
            self.debug('find references ntimes={} compresstimes={}'.format(len(times), len(ctimes)))

            for low, high in ctimes:
                rs = self.get_analyses_by_date_range(low, high,
                                                     extract_devices=extract_devices,
                                                     mass_spectrometers=mass_spectrometers,
                                                     analysis_types=atypes,
                                                     exclude=ex,
                                                     exclude_uuids=exclude,
                                                     exclude_invalid=exclude_invalid,
                                                     verbose=True)
                refs.update(rs)
                ex = [r.id for r in refs]

            return refs

    def get_blanks(self, ms=None, limit=100):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.filter(AnalysisTbl.analysis_type.like('blank%'))

            if ms:
                q = q.filter(func.lower(AnalysisTbl.mass_spectrometer) == ms.lower())
            q = q.order_by(AnalysisTbl.timestamp.desc())
            q = q.limit(limit)
            return self._query_all(q)

    def retrieve_blank(self, kind, ms, ed, last, repository):
        self.debug('retrieve blank. kind={}, ms={}, '
                   'ed={}, last={}, repository={}'.format(kind, ms, ed, last, repository))
        sess = self.session
        q = sess.query(AnalysisTbl)

        # if repository:
        #     q = q.join(RepositoryAssociationTbl)
        #     q = q.join(RepositoryTbl)

        if last:
            q = q.filter(AnalysisTbl.analysis_type == 'blank_{}'.format(kind))
        else:
            q = q.filter(AnalysisTbl.analysis_type.startswith('blank'))

        if ms:
            q = q.filter(func.lower(AnalysisTbl.mass_spectrometer) == ms.lower())

        if ed and ed not in ('Extract Device', NULL_STR) and kind == 'unknown':
            q = q.filter(func.lower(AnalysisTbl.extract_device) == ed.lower())

        # if repository:
        #     q = q.filter(RepositoryTbl.name == repository)

        q = q.order_by(AnalysisTbl.timestamp.desc())
        return self._query_one(q, verbose_query=True)

    def get_min_max_analysis_timestamp(self, lns=None, projects=None, delta=0):
        """
            lns: list of labnumbers/identifiers
            return: datetime, datetime

            get the min and max analysis_timestamps for all analyses with labnumbers in lns
        """

        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            if lns:
                q = q.join(IrradiationPositionTbl)
                q = q.filter(IrradiationPositionTbl.identifier.in_(lns))
            elif projects:
                q = q.join(IrradiationPositionTbl, SampleTbl, ProjectTbl)
                q = q.filter(ProjectTbl.name.in_(projects))

            return self._get_date_range(q, hours=delta)

    def get_labnumber_mass_spectrometers(self, lns):
        """
            return all the mass spectrometers use to measure these labnumbers analyses

            returns (str, str,...)
        """
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)
            q = q.filter(IrradiationPositionTbl.identifier.in_(lns))
            q = q.filter(distinct(AnalysisTbl.mass_spectrometer.name))
            return self._query_all(q)

    def get_analysis_date_ranges(self, lns, hours):
        """
            lns: list of labnumbers/identifiers
        """

        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)
            q = q.filter(IrradiationPositionTbl.identifier.in_(lns))
            q = q.order_by(AnalysisTbl.timestamp.asc())
            ts = self._query_all(q)
            return list(binfunc(ts, hours))

    def get_production_name(self, irrad, level):
        with self.session_ctx() as sess:
            dblevel = self.get_irradiation_level(irrad, level)
            return dblevel.production.name

    def add_save_user(self):
        with self.session_ctx():
            if not self.get_user(self.save_username):
                obj = UserTbl(name=self.save_username)
                self._add_item(obj)

    def add_measured_position(self, position=None, load=None, **kw):
        with self.session_ctx():
            a = MeasuredPositionTbl(**kw)
            if position:
                a.position = position
            if load:
                a.loadName = load
            return self._add_item(a)

    def add_load(self, name, holder, username):
        with self.session_ctx():
            if not self.get_loadtable(name):
                a = LoadTbl(name=name, holderName=holder, username=username)
                return self._add_item(a)

    def add_user(self, name, **kw):
        with self.session_ctx():
            a = UserTbl(name=name, **kw)
            return self._add_item(a)

    def add_analysis_group(self, ans, name, project, pi=None):
        with self.session_ctx():
            if not isinstance(project, six.text_type):
                pi = project.principal_investigator
                project = project.name

            project = self.get_project(project, pi)
            grp = AnalysisGroupTbl(name=name, user=globalv.username)
            grp.project = project
            self._add_item(grp)
            self.add_analyses_to_group_set(grp, ans)

    def add_analyses_to_group_set(self, grp, ans):
        aids = [s.analysis.id for s in grp.sets]
        for a in ans:
            a = self.get_analysis_uuid(a.uuid)
            if a.id not in aids:
                s = AnalysisGroupSetTbl()
                s.analysis = a
                s.group = grp
                self._add_item(s)

    def add_analysis_result(self, analysis, iso):
        with self.session_ctx():
            result = AnalysisIntensitiesTbl()
            result.isotope = iso.name
            result.detector = iso.detector
            result.blank_value = float(iso.blank.value)
            result.blank_error = float(iso.blank.error)

            attrs = ('value', 'error', 'n', 'fit', 'fit_error_type:error_type')
            for i, tag in ((iso, ''), (iso.baseline, 'baseline_')):

                for a in attrs:
                    if ':' in a:
                        a, b = a.split(':')
                    else:
                        a, b = a, a

                    v = getattr(i, b)
                    if b in ('value', 'error'):
                        v = float(v)
                    elif b == 'n':
                        v = int(v)
                    setattr(result, '{}{}'.format(tag, a), v)

            result.analysis = analysis

            self._add_item(result)

    def get_search_attributes(self):
        with self.session_ctx() as sess:
            s1 = sess.query(distinct(AnalysisIntensitiesTbl.isotope))
            s2 = sess.query(distinct(AnalysisIntensitiesTbl.detector))
            q = s1.union(s2)
            return self._query_all(q)

    def get_analyses_advanced(self, queries, isotopes=None, detectors=None, return_labnumbers=False):
        if isotopes is None:
            with self.session_ctx() as sess:
                s1 = sess.query(distinct(AnalysisIntensitiesTbl.isotope))
                rs = self._query_all(s1)
                isotopes = list(zip(*rs)[0])

        if detectors is None:
            with self.session_ctx() as sess:
                s1 = sess.query(distinct(AnalysisIntensitiesTbl.detector))
                rs = self._query_all(s1)
                detectors = list(zip(*rs)[0])

        def make_query(qq):
            col1 = 'isotope'
            if qq.attribute in detectors:
                col1 = 'detector'

            nclause, chain = make_filter(qq, AnalysisIntensitiesTbl)
            nclause = and_(nclause, getattr(AnalysisIntensitiesTbl, col1) == qq.attribute)
            return nclause, chain

        with self.session_ctx() as sess:
            if return_labnumbers:
                q = sess.query(IrradiationPositionTbl)
                q = q.join(AnalysisTbl, AnalysisIntensitiesTbl)
            else:
                q = sess.query(AnalysisTbl)
                q = q.join(AnalysisIntensitiesTbl)

            qi = queries[0]
            qa, _ = make_query(qi)
            j = join(AnalysisTbl, AnalysisIntensitiesTbl, AnalysisTbl.id == AnalysisIntensitiesTbl.analysisID)
            ff = qa

            bs = select([AnalysisTbl.id]).select_from(j)
            for i, qi in enumerate(queries[1:]):
                qa, chain = make_query(qi)
                if chain == 'and':
                    chain_func = and_
                else:
                    chain_func = or_

                ss = bs.where(qa)  # .alias('{}'.format(i))
                ff = chain_func(ff, AnalysisTbl.id.in_(ss))

            q = q.filter(ff)
            return self._query_all(q, verbose_query=True)

    def add_analysis(self, **kw):
        with self.session_ctx():
            a = AnalysisTbl(**kw)
            return self._add_item(a)

    def add_analysis_change(self, **kw):
        with self.session_ctx():
            a = AnalysisChangeTbl(**kw)
            return self._add_item(a)

    def add_repository_association(self, reponame, analysis):
        with self.session_ctx():
            self.debug('add association {}'.format(reponame))
            repo = self.get_repository(reponame)
            if repo is not None:
                e = RepositoryAssociationTbl()
                e.repository = repo.name
                e.analysis = analysis
                return self._add_item(e)
            else:
                self.warning('No repository named ="{}"'.format(reponame))
                self.debug('adding to repo={} instead')

    def add_material(self, name, grainsize=None):
        with self.session_ctx():
            a = self.get_material(name, grainsize)
            if a is None:
                a = MaterialTbl(name=name, grainsize=grainsize)
                a = self._add_item(a)
            return a

    def add_sample(self, name, project, pi, material, grainsize=None, **kw):
        with self.session_ctx():
            ret = self.get_sample(name, project, pi, material, grainsize)
            if ret is None:
                self.debug('Adding sample {},{},{},{}'.format(name, project, pi, material))
                p = self.get_project(project, pi)
                a = SampleTbl(name=name, **kw)
                if p is not None:
                    a.project = p
                    m = self.get_material(material, grainsize)
                    if m is not None:
                        a.materialID = m.id
                        ret = self._add_item(a)
                    else:
                        self.debug('No material={}, grainsize={}'.format(material, grainsize))
                else:
                    self.debug('No project {}, {}'.format(project, pi))
            return ret

    def add_extraction_device(self, name):
        with self.session_ctx():
            a = ExtractDeviceTbl(name=name)
            return self._add_item(a)

    def add_mass_spectrometer(self, name, kind='Argus'):
        with self.session_ctx():
            a = MassSpectrometerTbl(name=name, kind=kind)
            return self._add_item(a)

    def add_irradiation(self, name):
        with self.session_ctx():
            a = IrradiationTbl(name=name)
            return self._add_item(a)

    def add_irradiation_level(self, name, irradiation, holder, production_name,
                              z=0, note=''):
        with self.session_ctx():
            dblevel = self.get_irradiation_level(irradiation, name)
            if dblevel is None:
                irradiation = self.get_irradiation(irradiation)

                a = LevelTbl(name=name,
                             irradiation=irradiation,
                             holder=holder,
                             z=z,
                             note=note)

                dblevel = self._add_item(a)
            return dblevel

    def add_principal_investigator(self, name, **kw):
        with self.session_ctx():
            piname = self.get_principal_investigator(name)
            if piname is None:
                if ',' in name:
                    last_name, fi = name.split(',')
                    piname = PrincipalInvestigatorTbl(last_name=last_name.strip(), first_initial=fi.strip(), **kw)
                else:
                    piname = PrincipalInvestigatorTbl(last_name=name, **kw)
                piname = self._add_item(piname)
                self.debug('added principal investigator {}'.format(name))
            return piname

    def add_project(self, name, principal_investigator=None, **kw):
        with self.session_ctx():
            a = self.get_project(name, principal_investigator)
            if a is None:
                self.debug('Adding project {} {}'.format(name, principal_investigator))
                a = ProjectTbl(name=name, checkin_date=datetime.now(), **kw)
                if principal_investigator:
                    dbpi = self.get_principal_investigator(principal_investigator)
                    if dbpi:
                        a.principal_investigator = dbpi

                a = self._add_item(a)
            return a

    def add_irradiation_position(self, irrad, level, pos, identifier=None, **kw):
        with self.session_ctx():
            dbpos = self.get_irradiation_position(irrad, level, pos)
            if dbpos is None:
                self.debug('Adding irradiation position {}{} {}'.format(irrad, level, pos))
                a = IrradiationPositionTbl(position=pos, **kw)
                if self.kind == 'mssql':
                    # identifier cannot be null
                    # mssql does not allow multiple nulls for a unique column, e.g. identifier
                    # need a place holder value.

                    if not identifier:
                        identifier = '{}{}{}'.format(irrad, level, pos)
                    a.identifier = str(identifier)
                else:
                    if identifier:
                        a.identifier = str(identifier)

                a.level = self.get_irradiation_level(irrad, level)
                dbpos = self._add_item(a)
            else:
                self.debug('Irradiation position exists {}{} {}'.format(irrad, level, pos))

            return dbpos

    def add_load_position(self, ln, position, weight=0, note='', nxtals=0):
        with self.session_ctx():
            a = LoadPositionTbl(identifier=ln, position=position, weight=weight,
                                note=note, nxtals=nxtals)
            return self._add_item(a)

    def add_repository(self, name, principal_investigator, **kw):
        with self.session_ctx():
            repo = self.get_repository(name)
            if repo:
                return repo

            principal_investigator = self.get_principal_investigator(principal_investigator)
            if not principal_investigator:
                principal_investigator = self.add_principal_investigator(principal_investigator)
                self.flush()

            a = RepositoryTbl(name=name, **kw)
            a.principal_investigator = principal_investigator
            return self._add_item(a)

    # fuzzy getters
    def get_fuzzy_projects(self, search_str):
        with self.session_ctx() as sess:
            q = sess.query(ProjectTbl)

            f = or_(ProjectTbl.name.like('{}%'.format(search_str)), ProjectTbl.id.like('{}%'.format(search_str)))
            q = q.filter(f)
            return self._query_all(q, verbose_query=True)

    def get_fuzzy_labnumbers(self, search_str):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            q = q.join(SampleTbl)
            q = q.join(ProjectTbl)

            q = q.distinct(IrradiationPositionTbl.id)
            f = or_(IrradiationPositionTbl.identifier.like('{}%'.format(search_str)),
                    SampleTbl.name.like('{}%'.format(search_str)),
                    ProjectTbl.name == search_str,
                    ProjectTbl.id == search_str)
            q = q.filter(f)
            ips = self._query_all(q, verbose_query=True)

            q = sess.query(ProjectTbl)
            q = q.join(SampleTbl)
            q = q.join(IrradiationPositionTbl)
            f = or_(IrradiationPositionTbl.identifier.like('{}%'.format(search_str)),
                    SampleTbl.name.like('{}%'.format(search_str)), )
            q = q.filter(f)
            ps = self._query_all(q)
            return ips, ps

    # special getters
    def get_flux_value(self, identifier, attr):
        j = 0
        with self.session_ctx():
            dbpos = self.get_identifier(identifier)
            if dbpos:
                j = getattr(dbpos, attr)
        return j

    def get_greatest_identifier(self, **kw):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl.identifier)
            q = q.order_by(IrradiationPositionTbl.identifier.desc())
            ret = self._query_first(q)
            return int(ret[0]) if ret else 0

    def get_last_nhours_analyses(self, n, return_limits=False,
                                 mass_spectrometers=None, analysis_types=None):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)

            hpost = datetime.now()
            lpost = hpost - timedelta(hours=n)
            self.debug('last nhours n={}, lpost={}, mass_spec={}'.format(n, lpost, mass_spectrometers))
            if mass_spectrometers:
                q = in_func(q, AnalysisTbl.mass_spectrometer, mass_spectrometers)

            if analysis_types:
                q = analysis_type_filter(q, analysis_types)

            q = q.filter(AnalysisTbl.timestamp >= lpost)
            q = q.order_by(AnalysisTbl.timestamp.asc())
            ans = self._query_all(q)
            if return_limits:
                return ans, hpost, lpost
            else:
                return ans

    def get_last_n_analyses(self, n, mass_spectrometer=None, analysis_types=None,
                            excluded_uuids=None, verbose=False):

        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)

            if mass_spectrometer:
                q = q.filter(AnalysisTbl.mass_spectrometer == mass_spectrometer)
            else:
                q = q.order_by(AnalysisTbl.mass_spectrometer)

            if analysis_types:
                q = analysis_type_filter(q, analysis_types)

            if excluded_uuids:
                q = q.filter(not_(AnalysisTbl.uuid.in_(excluded_uuids)))

            q = q.order_by(AnalysisTbl.timestamp.desc())
            q = q.limit(n)
            return self._query_all(q, verbose_query=verbose)

    def get_last_analysis(self, ln=None, aliquot=None, spectrometer=None,
                          hours_limit=None,
                          analysis_type=None):
        self.debug(
            'get last analysis labnumber={}, aliquot={}, spectrometer={}'.format(
                ln, aliquot, spectrometer))
        with self.session_ctx() as sess:
            if ln:
                ln = self.get_identifier(ln)
                if not ln:
                    return

            q = sess.query(AnalysisTbl)
            if ln:
                q = q.join(IrradiationPositionTbl)

            if spectrometer:
                q = q.filter(AnalysisTbl.mass_spectrometer == spectrometer)

            if ln:
                q = q.filter(IrradiationPositionTbl.identifier == ln)
                if aliquot:
                    q = q.filter(AnalysisTbl.aliquot == aliquot)

            if analysis_type:
                q = q.filter(AnalysisTbl.analysis_type == analysis_type)

            if hours_limit:
                lpost = datetime.now() - timedelta(hours=hours_limit)
                q = q.filter(AnalysisTbl.timestamp >= lpost)

            q = q.order_by(AnalysisTbl.timestamp.desc())
            q = q.limit(1)
            try:
                r = q.one()
                self.debug('got last analysis {}-{}'.format(r.labnumber.identifier, r.aliquot))
                return r

            except NoResultFound as e:
                if ln:
                    name = ln.identifier
                elif spectrometer:
                    name = spectrometer

                if name:
                    self.debug('no analyses for {}'.format(name))
                else:
                    self.debug('no analyses for get_last_analysis')

                return 0

    def get_greatest_aliquot(self, identifier):
        with self.session_ctx(use_parent_session=False) as sess:
            if identifier:
                if not self.get_identifier(identifier):
                    return

                q = sess.query(AnalysisTbl.aliquot)
                q = q.join(IrradiationPositionTbl)

                q = q.filter(IrradiationPositionTbl.identifier == identifier)
                q = q.order_by(AnalysisTbl.aliquot.desc())
                result = self._query_one(q)
                if result:
                    return int(result[0])
                else:
                    return 0

    def get_greatest_step(self, ln, aliquot):
        """
            return greatest step for this labnumber and aliquot.
            return step as an integer. A=0, B=1...
        """
        with self.session_ctx(use_parent_session=False) as sess:
            if ln:
                dbln = self.get_identifier(ln)
                if not dbln:
                    return
                q = sess.query(AnalysisTbl.increment)
                q = q.join(IrradiationPositionTbl)

                q = q.filter(IrradiationPositionTbl.identifier == ln)
                q = q.filter(AnalysisTbl.aliquot == aliquot)
                q = q.order_by(AnalysisTbl.increment.desc())
                result = self._query_one(q)
                if result:
                    increment = result[0]
                    return increment if increment is not None else -1

    def get_unique_analysis(self, ln, ai, step=None):
        with self.session_ctx() as sess:
            try:
                ai = int(ai)
            except ValueError as e:
                self.debug('get_unique_analysis aliquot={}.  {}'.format(ai, e))
                return

            dbln = self.get_identifier(ln)
            if not dbln:
                self.debug('get_unique_analysis, no labnumber {}'.format(ln))
                return

            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)

            q = q.filter(IrradiationPositionTbl.identifier == ln)
            q = q.filter(AnalysisTbl.aliquot == int(ai))
            if step:
                if not isinstance(step, int):
                    step = alpha_to_int(step)

                q = q.filter(AnalysisTbl.increment == step)

            try:
                return q.one()
            except NoResultFound:
                return

    def get_labnumbers_startswith(self, partial_id, mass_spectrometers=None, filter_non_run=True,
                                  verbose_query=True, **kw):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            if mass_spectrometers or filter_non_run:
                q = q.join(AnalysisTbl)

            q = q.filter(IrradiationPositionTbl.identifier.like('%{}%'.format(partial_id)))
            if mass_spectrometers:
                q = q.filter(AnalysisTbl.mass_spectrometer.in_(mass_spectrometers))
            if filter_non_run:
                q = q.group_by(IrradiationPositionTbl.id)
                q = q.having(count(AnalysisTbl.id) > 0)

            return self._query_all(q, verbose_query=verbose_query, **kw)

    def get_associated_repositories(self, idn, verbose_query=False):
        with self.session_ctx() as sess:
            q = sess.query(distinct(RepositoryTbl.name), IrradiationPositionTbl.identifier)
            q = q.join(RepositoryAssociationTbl, AnalysisTbl, IrradiationPositionTbl)
            q = q.filter(IrradiationPositionTbl.identifier.in_(idn))
            q = q.order_by(IrradiationPositionTbl.identifier)

            return self._query_all(q, verbose_query=verbose_query)

    def get_analysis(self, value):
        return self._retrieve_item(AnalysisTbl, value, key='id')

    def get_analysis_uuid(self, value):
        return self._retrieve_item(AnalysisTbl, value, key='uuid')

    def get_analyses_uuid(self, uuids, verbose_query=False):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.filter(AnalysisTbl.uuid.in_(uuids))
            q = q.order_by(AnalysisTbl.uuid.asc())
            return self._query_all(q, verbose_query=verbose_query)

    def get_analysis_runid(self, idn, aliquot, step=None):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)
            if step:
                if isinstance(step, (str, six.text_type)):
                    step = alpha_to_int(step)

                q = q.filter(AnalysisTbl.increment == step)
            if aliquot:
                q = q.filter(AnalysisTbl.aliquot == aliquot)

            q = q.filter(IrradiationPositionTbl.identifier == idn)
            return self._query_one(q)

    def get_analysis_by_attr(self, **kw):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            use_ident = False
            if 'identifier' in kw:
                q = q.join(IrradiationPositionTbl)
                use_ident = True
            use_pos = False
            if 'position' in kw:
                q = q.join(MeasuredPositionTbl)
                use_pos = True

            if use_ident:
                q = q.filter(IrradiationPositionTbl.identifier == kw['identifier'])
                kw.pop('identifier')

            if use_pos:
                q = q.filter(MeasuredPositionTbl.position == kw['position'])
                kw.pop('position')

            for k, v in kw.items():
                try:
                    q = q.filter(getattr(AnalysisTbl, k) == v)
                except AttributeError:
                    self.debug('Invalid AnalysisTbl column {}'.format(k))
            q = q.order_by(AnalysisTbl.timestamp.desc())
            return self._query_first(q, verbose_query=False)

    def get_analysis_groups_by_name(self, name, project):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisGroupTbl)
            q = q.join(ProjectTbl)
            q = q.filter(AnalysisGroupTbl.name == name)

            if hasattr(project, 'name'):
                project = project.name

            q = q.filter(ProjectTbl.name == project)

            return self._query_all(q)

    def get_database_version(self, **kw):
        with self.session_ctx() as sess:
            q = sess.query(VersionTbl)
            v = self._query_one(q, **kw)
            return v.version

    def get_labnumber_analyses(self, lns,
                               low_post=None, high_post=None,
                               omit_key=None, exclude_uuids=None,
                               include_invalid=False,
                               mass_spectrometers=None,
                               repositories=None,
                               loads=None,
                               order='asc',
                               limit=None,
                               verbose_query=True):

        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)
            if omit_key or not include_invalid:
                q = q.join(AnalysisChangeTbl)

            if repositories:
                q = q.join(RepositoryAssociationTbl, RepositoryTbl)
            if loads:
                q = q.join(MeasuredPositionTbl)

            q = in_func(q, AnalysisTbl.mass_spectrometer, mass_spectrometers)
            q = in_func(q, RepositoryTbl.name, repositories)
            q = in_func(q, IrradiationPositionTbl.identifier, lns)
            q = in_func(q, MeasuredPositionTbl.loadName, loads)

            if low_post:
                q = q.filter(AnalysisTbl.timestamp >= str(low_post))

            if high_post:
                q = q.filter(AnalysisTbl.timestamp <= str(high_post))

            if exclude_uuids:
                q = q.filter(not_(AnalysisTbl.uuid.in_(exclude_uuids)))

            if not include_invalid:
                q = q.filter(AnalysisChangeTbl.tag != 'invalid')

            if omit_key:
                q = q.filter(AnalysisChangeTbl.tag != omit_key)

            if order:
                q = q.order_by(getattr(AnalysisTbl.timestamp, order)())

            if limit:
                q = q.limit(limit)

            tc = q.count()
            return self._query_all(q, verbose_query=verbose_query), tc

    def get_repository_date_range(self, names):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl.timestamp)
            q = q.join(RepositoryAssociationTbl)
            q = q.filter(RepositoryAssociationTbl.repository.in_(names))
            return self._get_date_range(q)

    def get_project_date_range(self, names):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl.timestamp)
            q = q.join(IrradiationPositionTbl, SampleTbl, ProjectTbl)
            if names:
                q = q.filter(ProjectTbl.name.in_(names))

            return self._get_date_range(q)

    def get_analyses_by_date_range(self, lpost, hpost,
                                   labnumber=None,
                                   limit=None,
                                   analysis_types=None,
                                   mass_spectrometers=None,
                                   extract_devices=None,
                                   project=None,
                                   repositories=None,
                                   loads=None,
                                   order='asc',
                                   exclude=None,
                                   exclude_uuids=None,
                                   exclude_invalid=True,
                                   verbose=True):
        if verbose:
            self.debug('------get analyses by date range parameters------')
            self.debug('low={}'.format(lpost))
            self.debug('high={}'.format(hpost))
            self.debug('labnumber={}'.format(labnumber))
            self.debug('analysis_types={}'.format(analysis_types))
            self.debug('mass spectrometers={}'.format(mass_spectrometers))
            self.debug('extract device={}'.format(extract_devices))
            self.debug('project={}'.format(project))
            self.debug('exclude={}'.format(exclude))
            self.debug('exclude_uuids={}'.format(exclude_uuids))
            self.debug('-------------------------------------------------')

        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            if exclude_invalid:
                q = q.join(AnalysisChangeTbl)
            if labnumber:
                q = q.join(IrradiationPositionTbl)
            if project:
                if not labnumber:
                    q = q.join(IrradiationPositionTbl)
                q = q.join(SampleTbl, ProjectTbl)

            if loads:
                q = q.join(MeasuredPositionTbl)
                q = in_func(q, MeasuredPositionTbl.loadName, loads)

            if labnumber:
                q = q.filter(IrradiationPositionTbl.identifier == labnumber)
            if mass_spectrometers:
                q = in_func(q, AnalysisTbl.mass_spectrometer, mass_spectrometers)

            if analysis_types:
                q = analysis_type_filter(q, analysis_types)

            q = extract_devices_query(analysis_types, extract_devices, q)

            if project:
                q = q.filter(ProjectTbl.name == project)
            if lpost:
                q = q.filter(AnalysisTbl.timestamp >= lpost)
            if hpost:
                q = q.filter(AnalysisTbl.timestamp <= hpost)
            if exclude_invalid:
                q = exclude_invalid_analyses(q)
            if exclude:
                q = q.filter(not_(AnalysisTbl.id.in_(exclude)))
            if exclude_uuids:
                q = q.filter(not_(AnalysisTbl.uuid.in_(exclude_uuids)))
            q = q.order_by(getattr(AnalysisTbl.timestamp, order)())
            if limit:
                q = q.limit(limit)

            return self._query_all(q, verbose_query=verbose)

    def get_project_labnumbers(self, project_names, filter_non_run,
                               low_post=None, high_post=None,
                               analysis_types=None, mass_spectrometers=None):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            q = q.join(SampleTbl, ProjectTbl)
            if filter_non_run:
                if mass_spectrometers or analysis_types or low_post or high_post:
                    q = q.join(AnalysisTbl)

                if mass_spectrometers:
                    mass_spectrometers = listify(mass_spectrometers)
                    q = q.filter(AnalysisTbl.mass_spectrometer.in_(mass_spectrometers))

                if analysis_types:
                    q = q.filter(AnalysisTbl.analysis_type.in_(analysis_types))
                    project_names.append('references')

                q = q.group_by(IrradiationPositionTbl.identifier)
                q = q.having(count(AnalysisTbl.id) > 0)
                if low_post:
                    q = q.filter(AnalysisTbl.timestamp >= str(low_post))
                if high_post:
                    q = q.filter(AnalysisTbl.timestamp <= str(high_post))

            if project_names:
                q = q.filter(ProjectTbl.name.in_(project_names))

            self.debug(compile_query(q))
            return self._query_all(q)

    def get_level_names(self, irrad):
        with self.session_ctx():
            levels = self.get_irradiation_levels(irrad)
            if levels:
                return [l.name for l in levels]
            else:
                return []

    def get_irradiation_levels(self, irradname):
        with self.session_ctx() as sess:
            q = sess.query(LevelTbl)
            q = q.join(IrradiationTbl)
            q = q.filter(IrradiationTbl.name == irradname)
            q = q.order_by(LevelTbl.name.asc())
            return self._query_all(q)

    def get_labnumbers(self, principal_investigators=None,
                       samples=None,
                       projects=None, repositories=None,
                       mass_spectrometers=None,
                       irradiation=None, level=None,
                       analysis_types=None,
                       high_post=None,
                       low_post=None,
                       loads=None,
                       filter_non_run=False):

        self.debug('------- Get Labnumbers {}-------'.format(id(self)))
        self.debug('------- samples: {}'.format(samples))
        self.debug('------- principal_investigators: {}'.format(principal_investigators))
        self.debug('------- projects: {}'.format(projects))
        self.debug('------- experiments: {}'.format(repositories))
        self.debug('------- mass_spectrometers: {}'.format(mass_spectrometers))
        self.debug('------- irradiation: {}'.format(irradiation))
        self.debug('------- level: {}'.format(level))
        self.debug('------- analysis_types: {}'.format(analysis_types))
        self.debug('------- high_post: {}'.format(high_post))
        self.debug('------- low_post: {}'.format(low_post))
        self.debug('------- loads: {}'.format(loads))
        self.debug('------------------------------')

        with self.session_ctx() as sess:
            q = sess.query(distinct(IrradiationPositionTbl.id))

            # joins
            at = False
            if repositories:
                at = True
                q = q.join(AnalysisTbl, RepositoryAssociationTbl, RepositoryTbl)

            if samples or projects or principal_investigators:
                q = q.join(SampleTbl, ProjectTbl)
                if principal_investigators:
                    q = q.join(PrincipalInvestigatorTbl)

            if mass_spectrometers and not at:
                at = True
                q = q.join(AnalysisTbl)

            if (low_post or high_post) and not at:
                at = True
                q = q.join(AnalysisTbl)

            if analysis_types and not at:
                at = True
                q = q.join(AnalysisTbl)

            if filter_non_run and not at:
                at = True
                q = q.join(AnalysisTbl)

            if loads:
                if not at:
                    at = True
                    q = q.join(AnalysisTbl)
                q = q.join(MeasuredPositionTbl)

            if irradiation:
                if not at:
                    q = q.join(AnalysisTbl)
                q = q.join(LevelTbl, IrradiationTbl)

            has_filter = False
            # filters
            if repositories:
                has_filter = True
                q = q.filter(RepositoryTbl.name.in_(repositories))

            if principal_investigators:
                has_filter = True
                for p in principal_investigators:
                    q = principal_investigator_filter(q, p)

            if projects:
                has_filter = True
                q = q.filter(ProjectTbl.name.in_(projects))
            if mass_spectrometers:
                has_filter = True
                q = in_func(q, AnalysisTbl.mass_spectrometer, mass_spectrometers)

            if low_post:
                has_filter = True
                q = q.filter(AnalysisTbl.timestamp >= low_post)
            if high_post:
                has_filter = True
                q = q.filter(AnalysisTbl.timestamp <= high_post)

            if samples:
                has_filter = True
                if analysis_types:
                    q = q.filter(or_(SampleTbl.name.in_(samples), make_at_filter(analysis_types)))
                else:
                    q = q.filter(SampleTbl.name.in_(samples))

            if analysis_types and not samples:
                has_filter = True
                q = analysis_type_filter(q, analysis_types)

            if irradiation:
                has_filter = True
                q = q.filter(IrradiationTbl.name == irradiation)
                q = q.filter(LevelTbl.name == level)
            if loads:
                has_filter = True
                q = q.filter(MeasuredPositionTbl.loadName.in_(loads))

            if filter_non_run:
                q = q.group_by(IrradiationPositionTbl.id)
                q = q.having(count(AnalysisTbl.id) > 0)

            if has_filter:
                res = self._query_all(q, verbose_query=False)
                if res:
                    ids = [r[0] for r in res]
                    q = sess.query(IrradiationPositionTbl)
                    q = q.filter(IrradiationPositionTbl.id.in_(ids))
                    return self._query_all(q, verbose_query=False)

    def get_analysis_groups(self, project_ids, **kw):
        ret = []
        if project_ids:
            with self.session_ctx() as sess:
                q = sess.query(AnalysisGroupTbl)
                q = q.filter(AnalysisGroupTbl.projectID.in_(project_ids))
                ret = self._query_all(q, **kw)
        return ret

    # single getters
    def get_user(self, name):
        return self._retrieve_item(UserTbl, name)

    def get_extraction_device(self, name):
        return self._retrieve_item(ExtractDeviceTbl, name)

    def get_mass_spectrometer(self, name):
        return self._retrieve_item(MassSpectrometerTbl, name)

    def get_repository(self, name):
        return self._retrieve_item(RepositoryTbl, name)

    def get_load_position(self, loadname, pos):
        with self.session_ctx() as sess:
            q = sess.query(LoadPositionTbl)
            q = q.join(LoadTbl)
            q = q.filter(LoadTbl.name == loadname)
            q = q.filter(LoadPositionTbl.position == pos)
            return self._query_one(q)

    def get_loadtable(self, name=None):
        if name is not None:
            lt = self._retrieve_item(LoadTbl, name)
        else:
            with self.session_ctx() as s:
                q = s.query(LoadTbl)
                q = q.order_by(LoadTbl.create_date.desc())
                lt = self._query_first(q)

        return lt

    get_load = get_loadtable

    def get_identifier(self, identifier):
        return self._retrieve_item(IrradiationPositionTbl, identifier, key='identifier')

    def get_irradiation_position_by_sample(self, name, material, grainsize, principal_investigator, project):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            q = q.join(SampleTbl, MaterialTbl, ProjectTbl, PrincipalInvestigatorTbl)
            q = q.filter(SampleTbl.name == name)
            q = q.filter(MaterialTbl.name == material)
            if grainsize:
                q = q.filter(MaterialTbl.grainsize == grainsize)
            q = q.filter(ProjectTbl.name == project)
            q = principal_investigator_filter(q, principal_investigator)
            return self._query_all(q, verbose_query=True)

    def get_irradiation_position(self, irrad, level, pos):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            q = q.join(LevelTbl, IrradiationTbl)
            q = q.filter(IrradiationTbl.name == irrad)
            q = q.filter(LevelTbl.name == level)
            q = q.filter(IrradiationPositionTbl.position == pos)

        return self._query_one(q)

    def get_project_by_id(self, pid):
        with self.session_ctx() as sess:
            q = sess.query(ProjectTbl)
            q = q.filter(ProjectTbl.id == pid)
            return self._query_one(q)

    def get_project(self, name, pi=None):
        if isinstance(name, (str, six.text_type)):
            if pi:
                with self.session_ctx() as sess:

                    q = sess.query(ProjectTbl)
                    q = q.join(PrincipalInvestigatorTbl)
                    q = q.filter(ProjectTbl.name == name)

                    dbpi = self.get_principal_investigator(pi)
                    if dbpi:
                        q = principal_investigator_filter(q, pi)

                    return self._query_one(q, verbose_query=True)
            else:
                return self._retrieve_item(ProjectTbl, name)
        else:
            return name

    def get_principal_investigator(self, name):
        with self.session_ctx() as sess:
            q = sess.query(PrincipalInvestigatorTbl)
            q = principal_investigator_filter(q, name)
            return self._query_one(q)

    def get_irradiation_level(self, irrad, name):
        with self.session_ctx() as sess:
            irrad = self.get_irradiation(irrad)
            if irrad:
                q = sess.query(LevelTbl)
                q = q.filter(LevelTbl.irradiationID == irrad.id)
                q = q.filter(LevelTbl.name == name)
                return self._query_one(q)

    def get_irradiation(self, name):
        return self._retrieve_item(IrradiationTbl, name)

    def get_material(self, name, grainsize=None):
        # if not isinstance(name, str) and not isinstance(name, six.text_type):
        if isinstance(name, MaterialTbl):
            if grainsize is None or name.grainsize == grainsize:
                return name

        with self.session_ctx() as sess:
            q = sess.query(MaterialTbl)
            if isinstance(name, MaterialTbl):
                q = q.filter(MaterialTbl.id == name.id)
            else:
                q = q.filter(MaterialTbl.name == name)

            if grainsize:
                q = q.filter(MaterialTbl.grainsize == grainsize)
            return self._query_one(q)

    def get_sample_id(self, id):
        return self._retrieve_item(SampleTbl, id, key='id')

    def get_sample(self, name, project, pi, material, grainsize=None):
        with self.session_ctx() as sess:
            q = sess.query(SampleTbl)
            q = q.join(ProjectTbl)

            project = self.get_project(project, pi)
            material = self.get_material(material, grainsize)

            q = q.filter(SampleTbl.project == project)
            q = q.filter(SampleTbl.material == material)
            q = q.filter(SampleTbl.name == name)

            return self._query_one(q, verbose_query=False)

    def get_last_identifier(self, sample=None):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            if sample:
                q = q.join(SampleTbl)
                q = q.filter(SampleTbl.name == sample)

            q = q.order_by(func.abs(IrradiationPositionTbl.identifier).desc())
            return self._query_first(q)

    def get_latest_load(self):
        return self._retrieve_first(LoadTbl,
                                    order_by=LoadTbl.create_date.desc())

    # similar getters

    def get_similar_pi(self, name):
        name = name.lower()
        with self.session_ctx() as sess:
            q = sess.query(PrincipalInvestigatorTbl)
            attr = func.lower(PrincipalInvestigatorTbl.name)
            return self._get_similar(name, attr, q)

    def get_similar_material(self, name):
        name = name.lower()
        with self.session_ctx() as sess:
            q = sess.query(MaterialTbl)
            attr = func.lower(MaterialTbl.name)
            return self._get_similar(name, attr, q)

    def get_similar_project(self, name, pi):
        name = name.lower()
        with self.session_ctx() as sess:
            q = sess.query(ProjectTbl)
            q = q.join(PrincipalInvestigatorTbl)
            q = q.filter(PrincipalInvestigatorTbl.name == pi)

            attr = func.lower(ProjectTbl.name)
            return self._get_similar(name, attr, q)

    # multi getters
    def get_analyses_by_level(self, irradiation, level, verbose=False):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl)
            q = q.join(LevelTbl)
            q = q.join(IrradiationTbl)
            q = q.join(RepositoryAssociationTbl)

            q = q.filter(IrradiationTbl.name == irradiation)
            q = q.filter(LevelTbl.name == level)
            q = q.order_by(RepositoryAssociationTbl.repository)

            return self._query_all(q, verbose_query=verbose)

    def get_analyses(self, analysis_type=None, mass_spectrometer=None,
                     reverse_order=False):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            if mass_spectrometer:
                q = q.filter(AnalysisTbl.mass_spectrometer == mass_spectrometer)
            if analysis_type:
                q = q.filter(AnalysisTbl.analysis_type == analysis_type)

            q = q.order_by(getattr(AnalysisTbl.timestamp,
                                   'desc' if reverse_order else 'asc')())
            return self._query_all(q)

    def get_analysis_types(self):
        return []

    def get_measured_load_names(self):
        with self.session_ctx() as sess:
            q = sess.query(distinct(MeasuredPositionTbl.loadName))
            q = q.order_by(MeasuredPositionTbl.loadName)
            s = self._query_all(q)
            return [si[0] for si in s]

    def get_measured_positions(self, loadname, pos):
        with self.session_ctx() as sess:
            q = sess.query(MeasuredPositionTbl)
            q = q.filter(MeasuredPositionTbl.loadName == loadname)
            q = q.filter(MeasuredPositionTbl.position == pos)
            return self._query_all(q)

    def get_last_identifiers(self, sample=None, limit=1000, excludes=None):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            if sample:
                q = q.join(SampleTbl)
                q = q.filter(SampleTbl.name == sample)
                if excludes:
                    q = q.filter(not_(SampleTbl.name.in_(excludes)))
            elif excludes:
                q = q.join(SampleTbl)
                q = q.filter(not_(SampleTbl.name.in_(excludes)))
            q = q.filter(IrradiationPositionTbl.identifier.isnot(None))
            q = q.order_by(func.abs(IrradiationPositionTbl.identifier).desc())
            q = q.limit(limit)
            return [ni.identifier for ni in self._query_all(q, verbose_query=True)]

    def get_loads(self):
        return self._retrieve_items(LoadTbl, order=LoadTbl.create_date.desc())

    def get_load_names(self, names=None, exclude_archived=True, **kw):
        with self.session_ctx():
            if 'order' not in kw:
                kw['order'] = LoadTbl.create_date.desc()

            if exclude_archived:
                kw = self._append_filters(not_(LoadTbl.archived), kw)

            if names:
                kw = self._append_filters(LoadTbl.name.in_(names), kw)

            loads = self._retrieve_items(LoadTbl, **kw)
            if loads:
                return [ui.name for ui in loads]

    def get_extraction_devices(self):
        return self.get_extract_devices()

    def get_extraction_device_names(self):
        names = []
        with self.session_ctx():
            eds = self.get_extract_devices()
            if eds:
                names = [e.name for e in eds]
        return names

    def get_users(self, **kw):
        return self._retrieve_items(UserTbl, **kw)

    def get_usernames(self):
        return self._get_table_names(UserTbl)

    def get_project_names(self):
        return self._get_table_names(ProjectTbl, use_distinct=ProjectTbl.name)

    def get_material_names(self):
        return self._get_table_names(MaterialTbl, use_distinct=MaterialTbl.name)

    def get_project_pnames(self):
        with self.session_ctx() as sess:
            q = sess.query(ProjectTbl)
            q = q.order_by(ProjectTbl.name.asc())
            ms = self._query_all(q)
            return [mi.pname for mi in ms]

    def get_material_gnames(self):
        with self.session_ctx() as sess:
            q = sess.query(MaterialTbl)
            q = q.order_by(MaterialTbl.name.asc())
            ms = self._query_all(q)
            return [mi.gname for mi in ms]

    def get_principal_investigator_names(self, *args, **kw):
        order = PrincipalInvestigatorTbl.last_name.asc()
        return self._get_table_names(PrincipalInvestigatorTbl, order=order)

    def get_principal_investigators(self, order=None, **kw):
        if order:
            order = getattr(PrincipalInvestigatorTbl.last_name, order)()

        return self._retrieve_items(PrincipalInvestigatorTbl, order=order, **kw)

    def get_grainsizes(self):
        with self.session_ctx() as sess:
            q = sess.query(distinct(MaterialTbl.grainsize))
            gs = self._query_all(q)
            return [g[0] for g in gs if g[0]]

    def get_samples_by_name(self, name):
        with self.session_ctx() as sess:
            q = sess.query(SampleTbl)
            q = q.filter(SampleTbl.name.like('%{}%'.format(name)))
            return self._query_all(q, verbose_query=True)

    def distinct_sample_names(self, irradiation, level):
        with self.session_ctx() as sess:
            q = sess.query(distinct(SampleTbl.name))
            q = q.join(IrradiationPositionTbl)
            q = q.join(LevelTbl)
            q = q.join(IrradiationTbl)

            q = q.filter(IrradiationTbl.name == irradiation)
            q = q.filter(LevelTbl.name == level)
            records = self._query_all(q, verbose_query=False)
            return [r[0] for r in records]

    def get_samples_filter(self, attr, value, **kw):
        with self.session_ctx() as sess:
            q = sess.query(SampleTbl)
            if attr == 'project':
                q = q.join(ProjectTbl)
            elif attr == 'material':
                q = q.join(MaterialTbl)
            elif attr == 'principal_investigator':
                q = q.join(ProjectTbl, PrincipalInvestigatorTbl)

            value = '{}%'.format(value)
            if attr == 'project':
                q = q.filter(ProjectTbl.name.like(value))
            elif attr == 'material':
                q = q.filter(MaterialTbl.name.like(value))
            elif attr == 'principal_investigator':
                if ',' in value:
                    # trim off wildcard
                    value = value[:-1]
                    l, f = value.split(',')
                    lastname = l.strip()
                    first_initial = f.strip()
                    q = q.filter(PrincipalInvestigatorTbl.last_name == lastname)
                    q = q.filter(PrincipalInvestigatorTbl.first_initial == first_initial)
                else:
                    q = q.filter(PrincipalInvestigatorTbl.last_name.like(value))

            else:
                q = q.filter(getattr(SampleTbl, attr).like(value))

            return self._query_all(q, verbose_query=False, **kw)

    def get_samples(self, projects=None, principal_investigators=None, project_like=None, name_like=None, **kw):
        with self.session_ctx() as sess:
            q = sess.query(SampleTbl)
            if projects or project_like:
                q = q.join(ProjectTbl)

            if principal_investigators:
                q = q.join(PrincipalInvestigatorTbl)

            if projects:
                if isinstance(projects, (list, tuple)):
                    q = q.filter(ProjectTbl.name.in_(projects))
                else:
                    q = q.filter(ProjectTbl.name == projects)

            if project_like:
                q = q.filter(ProjectTbl.name.like('{}%'.format(project_like)))

            if principal_investigators:
                if not isinstance(principal_investigators, (list, tuple)):
                    principal_investigators = (principal_investigators,)

                for p in principal_investigators:
                    q = principal_investigator_filter(q, p)

            if name_like:
                q = q.filter(SampleTbl.name.like('{}%'.format(name_like)))
            return self._query_all(q, **kw)

    def get_irradiations_by_repositories(self, repositories):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationTbl)
            q = q.join(LevelTbl, IrradiationPositionTbl, AnalysisTbl,
                       RepositoryAssociationTbl, RepositoryTbl)

            q = in_func(q, RepositoryTbl.name, repositories)
            return self._query_all(q)

    def get_level_identifiers(self, irrad, level):
        lns = []
        with self.session_ctx():
            level = self.get_irradiation_level(irrad, level)
            if level:
                lns = [str(pi.identifier).strip()
                       for pi in level.positions if pi.identifier]
                lns = [li for li in lns if li]
                lns = sorted(lns)
        return lns

    def get_irradiation_names(self, **kw):
        names = []
        with self.session_ctx():
            ns = self.get_irradiations(**kw)
            if ns:
                names = [i.name for i in ns]

        return names

    def get_irradiations(self, names=None, project_names=None, order_func='desc', mass_spectrometers=None,
                         exclude_name=None, **kw):

        if names is not None:
            if hasattr(names, '__call__'):
                f = names(IrradiationTbl)
            else:
                names = listify(names)
                f = (IrradiationTbl.name.in_(names),)
            kw = self._append_filters(f, kw)

        if project_names is not None:
            project_names = listify(project_names)
            kw = self._append_filters(ProjectTbl.name.in_(project_names), kw)

            kw = self._append_joins((LevelTbl, IrradiationPositionTbl, SampleTbl, ProjectTbl), kw)

        if mass_spectrometers:
            kw = self._append_filters(AnalysisTbl.mass_spectrometer.name.in_(mass_spectrometers), kw)
            kw = self._append_joins((LevelTbl, IrradiationPositionTbl, AnalysisTbl), kw)

        if exclude_name:
            kw = self._append_filters(IrradiationTbl.name.notlike(exclude_name), kw)

        order = None
        if order_func:
            order = getattr(IrradiationTbl.name, order_func)()

        return self._retrieve_items(IrradiationTbl, order=order, **kw)

    def get_projects(self, principal_investigators=None, irradiation=None, level=None,
                     mass_spectrometers=None, order=None, verbose_query=False):

        if order:
            order = getattr(ProjectTbl.name, order)()

        if principal_investigators or irradiation or mass_spectrometers:
            with self.session_ctx() as sess:
                q = sess.query(ProjectTbl)

                # joins
                if principal_investigators:
                    q = q.join(PrincipalInvestigatorTbl)

                if irradiation:
                    q = q.join(SampleTbl, IrradiationPositionTbl)
                    if level:
                        q = q.join(LevelTbl)

                if mass_spectrometers:
                    q = q.join(SampleTbl, IrradiationPositionTbl, AnalysisTbl)

                # filters
                if principal_investigators:
                    for p in principal_investigators:
                        q = principal_investigator_filter(q, p)

                if irradiation:
                    if level:
                        q = q.filter(LevelTbl.name == level)
                    q = q.filter(IrradiationTbl.name == irradiation)

                if mass_spectrometers:
                    mass_spectrometers = listify(mass_spectrometers)
                    q = q.filter(AnalysisTbl.mass_spectrometer.in_(mass_spectrometers))

                if order is not None:
                    q = q.order_by(order)

                ps = self._query_all(q, verbose_query=verbose_query)
        else:
            ps = self._retrieve_items(ProjectTbl, order=order, verbose_query=verbose_query)
        return ps

    def get_repositories(self):
        return self._retrieve_items(RepositoryTbl)

    def get_extract_devices(self):
        return self._retrieve_items(ExtractDeviceTbl)

    def get_mass_spectrometer_names(self):
        with self.session_ctx():
            ms = self.get_mass_spectrometers()
            return [mi.name for mi in ms]

    def get_mass_spectrometers(self):
        return self._retrieve_items(MassSpectrometerTbl)

    def get_active_mass_spectrometer_names(self):
        with self.session_ctx():
            ms = self.get_mass_spectrometers()
            return [mi.name for mi in ms if mi.active]

    def get_repository_identifiers(self):
        return self._get_table_names(RepositoryTbl)

    def get_unknown_positions(self, *args, **kw):
        kw['invert'] = True
        return self._flux_positions(*args, **kw)

    def get_flux_monitors(self, *args, **kw):
        return self._flux_positions(*args, **kw)

    def get_flux_monitor_analyses(self, irradiation, level, sample):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl)
            q = q.join(IrradiationPositionTbl, LevelTbl, IrradiationTbl,
                       SampleTbl, AnalysisChangeTbl)

            q = q.filter(IrradiationTbl.name == irradiation)
            q = q.filter(LevelTbl.name == level)
            q = q.filter(SampleTbl.name == sample)
            q = q.filter(AnalysisChangeTbl.tag != 'invalid')

            return self._query_all(q, verbose_query=True)

    def delete_tag(self, name):
        with self.session_ctx() as sess:
            q = sess.query(AnalysisTbl.id)
            q = q.join(AnalysisChangeTbl)
            q = q.filter(AnalysisChangeTbl.tag == name)
            n = q.count()
            if n:
                a = 'analyses' if n > 1 else 'analysis'

                if not self.confirmation_dialog(
                        'The Tag "{}" is applied to {} {}. '
                        'Are you sure to want to delete it?'.format(name, n,
                                                                    a)):
                    return

            self._delete_item(name, name='tag')
            return True

    def delete_analysis_group(self, g, commit=False):
        with self.session_ctx() as sess:
            for si in g.sets:
                sess.delete(si)

            sess.delete(g)
            if commit:
                sess.commit()

    # ============================================================
    # Sample Prep
    # ============================================================
    def add_sample_prep_worker(self, name, fullname, email, phone, comment):
        with self.session_ctx():
            w = self.get_sample_prep_worker(name)
            if w is None:
                obj = SamplePrepWorkerTbl(name=name, fullname=fullname,
                                          email=email, phone=phone, comment=comment)
                self._add_item(obj)
                return True

    def update_sample_prep_session(self, oname, worker, **kw):
        s = self.get_sample_prep_session(oname, worker)
        if s:
            for k, v in kw.items():
                setattr(s, k, v)
            self.commit()

    def move_sample_to_session(self, current, sample, session, worker):
        with self.session_ctx() as sess:
            session = self.get_sample_prep_session(session, worker)
            q = sess.query(SamplePrepStepTbl)
            q = q.join(SamplePrepSessionTbl)
            q = q.join(SampleTbl)

            q = q.filter(SamplePrepSessionTbl.name == current)
            q = q.filter(SamplePrepSessionTbl.worker_name == worker)
            q = q.filter(SampleTbl.name == sample['name'])
            q = q.filter(MaterialTbl.name == sample['material'])
            q = q.filter(ProjectTbl.name == sample['project'])
            ss = self._query_all(q)
            for si in ss:
                si.sessionID = session.id

    def add_sample_prep_session(self, name, worker, comment):
        with self.session_ctx():
            s = self.get_sample_prep_session(name, worker)
            if s is None:
                obj = SamplePrepSessionTbl(name=name, worker_name=worker,
                                           comment=comment)
                self._add_item(obj)
                return True

    def add_sample_prep_step(self, sampleargs, worker, session, **kw):
        with self.session_ctx():
            sample = self.get_sample(*sampleargs)
            session = self.get_sample_prep_session(session, worker)
            obj = SamplePrepStepTbl(**kw)
            obj.sampleID = sample.id
            obj.sessionID = session.id
            self._add_item(obj)

            # add choice
            for k, v in kw.items():
                if v and v is not 'X':
                    if k in SAMPLE_PREP_STEPS:
                        self.add_sample_prep_choice(k, v)

    def add_sample_prep_image(self, stepid, host, path, note):
        with self.session_ctx():
            obj = SamplePrepImageTbl(host=host,
                                     path=path,
                                     stepID=stepid,
                                     note=note)
            self._add_item(obj)

    def get_sample_prep_image(self, img_id):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepImageTbl)
            q = q.filter(SamplePrepImageTbl.id == img_id)
            return self._query_one(q)

    def get_sample_prep_samples(self, worker, session):
        with self.session_ctx() as sess:
            q = sess.query(SampleTbl)
            q = q.join(SamplePrepStepTbl)
            q = q.join(SamplePrepSessionTbl)
            q = q.filter(SamplePrepSessionTbl.name == session)
            q = q.filter(SamplePrepSessionTbl.worker_name == worker)
            return self._query_all(q, verbose_query=False)

    def get_sample_prep_step_by_id(self, id):
        return self._retrieve_item(SamplePrepStepTbl, id, 'id')

    def get_sample_prep_session(self, name, worker):
        return self._retrieve_item(SamplePrepSessionTbl, (name, worker), ('name', 'worker_name'))

    def get_sample_prep_worker(self, name):
        return self._retrieve_item(SamplePrepWorkerTbl, name)

    def get_sample_prep_worker_names(self):
        return self._get_table_names(SamplePrepWorkerTbl)

    def get_sample_prep_session_names(self, worker):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepSessionTbl.name)
            q = q.filter(SamplePrepSessionTbl.worker_name == worker)
            return [i[0] for i in self._query_all(q)]

    def get_sample_prep_sessions(self, sample):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepSessionTbl)
            q = q.join(SamplePrepStepTbl)
            q = q.join(SampleTbl)
            q = q.filter(SampleTbl.name == sample)
            return self._query_all(q)

    def get_sample_prep_steps(self, worker, session, sample, project, material, grainsize):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepStepTbl)
            q = q.join(SamplePrepSessionTbl)
            q = q.join(SampleTbl)
            q = q.join(ProjectTbl)
            q = q.join(MaterialTbl)

            q = q.filter(SamplePrepStepTbl.added.is_(None))
            q = q.filter(SamplePrepSessionTbl.worker_name == worker)
            q = q.filter(SamplePrepSessionTbl.name == session)
            q = q.filter(SampleTbl.name == sample)
            q = q.filter(ProjectTbl.name == project)
            q = q.filter(MaterialTbl.name == material)
            if grainsize:
                q = q.filter(MaterialTbl.grainsize == grainsize)

            return self._query_all(q)

    def get_sample_prep_choice_names(self, tag):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepChoicesTbl)
            q = q.filter(SamplePrepChoicesTbl.tag == tag)
            return [v.value for v in self._query_all(q, verbose_query=False)]

    def add_sample_prep_choice(self, tag, value):
        with self.session_ctx() as sess:
            q = sess.query(SamplePrepChoicesTbl)
            q = q.filter(SamplePrepChoicesTbl.tag == tag)
            q = q.filter(SamplePrepChoicesTbl.value == value)

            if not self._query_one(q):
                obj = SamplePrepChoicesTbl()
                obj.value = value
                obj.tag = tag
                self._add_item(obj)

    # private
    def _get_date_range(self, q, asc=None, desc=None, hours=0):
        if asc is None:
            asc = AnalysisTbl.timestamp.asc()
        if desc is None:
            desc = AnalysisTbl.timestamp.desc()
        return super(DVCDatabase, self)._get_date_range(q, asc, desc, hours=hours)

    def _get_similar(self, name, attr, q):
        f = or_(attr == name, attr.like('{}%{}'.format(name[0], name[-1])))
        q = q.filter(f)
        items = self._query_all(q)
        if len(items) > 1:
            # get the most likely name
            obj = self.get_principal_investigator(correct(name, [i.name for i in items]))
            return obj
        elif items:
            return items[0]

    def _flux_positions(self, irradiation, level, sample, invert=False):
        with self.session_ctx() as sess:
            q = sess.query(IrradiationPositionTbl)
            q = q.join(LevelTbl, IrradiationTbl, SampleTbl)
            q = q.filter(IrradiationTbl.name == irradiation)
            q = q.filter(LevelTbl.name == level)
            if invert:
                q = q.filter(not_(SampleTbl.name == sample))
            else:
                q = q.filter(SampleTbl.name == sample)

            return self._query_all(q)

    def _get_table_names(self, tbl, order='asc', use_distinct=False, **kw):
        with self.session_ctx():
            if isinstance(order, str):
                order = getattr(tbl.name, order)()

            ret = []
            names = self._retrieve_items(tbl, order=order, distinct_=use_distinct, **kw)
            if names:
                if use_distinct:
                    ret = [ni[0] for ni in names]
                else:
                    ret = [ni.name for ni in names or []]
            return ret

# ============= EOF =============================================
