#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' My Python package release script.
'''

from collections import defaultdict, namedtuple
from configparser import ConfigParser
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from functools import cache, cached_property
from getopt import GetoptError
from glob import glob
import importlib
import os
import os.path
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    normpath,
    relpath,
    splitext,
)
from pprint import pprint
import re
from shutil import rmtree
import sys
from types import SimpleNamespace

from icontract import ensure, require
import tomli_w
from typeguard import typechecked

from cs.ansi_colour import colourise
from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.dateutils import isodate
from cs.fs import atomic_directory, scandirpaths
from cs.fstags import FSTags
from cs.lex import (
    cutsuffix,
    get_identifier,
    get_dotted_identifier,
    is_dotted_identifier,
    is_identifier,
)
from cs.logutils import error, warning, info, status
from cs.numeric import intif
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.psutils import pipefrom as ps_pipefrom, pipeto as ps_pipeto, run
from cs.py.doc import module_doc
from cs.py.modules import direct_imports
from cs.resources import RunState, uses_runstate
from cs.tagset import TagFile, tag_or_tag_value
from cs.upd import Upd, print, uses_upd
from cs.vcs import VCS
from cs.vcs.hg import VCS_Hg

def main(argv=None):
  ''' Main command line.
  '''
  return CSReleaseCommand(argv).run()

URL_PYPI_PROD = 'https://pypi.python.org/pypi'
URL_PYPI_TEST = 'https://test.pypi.org/legacy/'

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/'

MIRROR_SRCBASE = 'https://github.com/cameron-simpson/css/blob/main'

DISTINFO_CLASSIFICATION = {
    "Programming Language": "Python",
    "Development Status": "4 - Beta",
    "Intended Audience": "Developers",
    "Operating System": "OS Independent",
    "Topic": "Software Development :: Libraries :: Python Modules",
    "License":
    "OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
}

# the top level TagFile containing package state information
PKG_TAGS = 'pkg_tags'

# the path from the top level to the package files
PYLIBTOP = 'lib/python'
SVGLIBTOP = 'lib/svg'

# the prefix of interesting packages
MODULE_PREFIX = 'cs.'

TAG_PYPI_RELEASE = 'pypi.release'

# defaults for packages without their own specifics
DISTINFO_DEFAULTS = {
    'urls': {
        'Monorepo Hg/Mercurial Mirror':
        'https://hg.sr.ht/~cameron-simpson/css',
        'Monorepo Git Mirror':
        'https://github.com/cameron-simpson/css',
        'MonoRepo Commits':
        'https://bitbucket.org/cameron_simpson/css/commits/branch/main',
    },
}

re_RELEASE_TAG = re.compile(
    #  name       - YYYYMMDD                            [.n]
    r'([a-z][^-]*)-(2[0-9][0-9][0-9][01][0-9][0-3][0-9](\.[1-9]\d*)?)$'
)

class ReleaseTag(namedtuple('ReleaseTag', 'name version')):
  ''' A parsed version of one of my release tags,
      which have the form *package_name*`-`*version*.
  '''

  @classmethod
  def from_vcstag(cls, vcstag):
    ''' Create a new `ReleaseTag` from a VCS tag.
    '''
    name, version = vcstag.split('-', 1)
    return cls(name, version)

  @classmethod
  def today(cls, name):
    ''' Basic release tag for today, without any `.`*n* suffix.
    '''
    return cls(name, isodate(dashed=False))

  @property
  def vcstag(self):
    ''' The VCS tag for this `(name,version)` pair.
    '''
    return self.name + '-' + self.version

  @ensure(lambda result: isinstance(result, ReleaseTag))
  @ensure(
      lambda result: result.version == isodate(dashed=False) or result.version.
      startswith(isodate(dashed=False) + '.')
  )
  @ensure(lambda self, result: self.version < result.version)
  def next(self):
    ''' Compute the next `ReleaseTag` after the current one.
    '''
    today = type(self).today(self.name)
    if self.version < today.version:
      return today
    current_version = self.version
    if '.' in current_version:
      _, seqpart = current_version.split('.', 1)
      next_seq = int(seqpart) + 1
    else:
      next_seq = 1
    version = today.version + '.' + str(next_seq)
    return type(self)(self.name, version)

class ModuleRequirement(namedtuple('ModuleRequirement',
                                   'module_name op requirements modules')):
  ''' A parsed version of a module requirement string
      such as `'cs.upd>=multiline'` or `'cs.obj>=20200716'`.

      Attributes:
      * `module_name`: the name of the module or package
      * `op`: the relationship to the requirements,
        supporting `'='` and `'>='`;
        `None` if only the name is present
      * `requirements`: a list of the requirement terms,
        broken out on commas in the requirements part
      * `modules`: a references to the `Modules` instance used to track module information
  '''

  @classmethod
  @pfx_method
  def from_requirement(cls, requirement_spec, modules):
    ''' Parse a requirement string, return a `ModuleRequirement`.
    '''
    with Pfx(requirement_spec):
      module_name, offset = get_dotted_identifier(
          requirement_spec, extras='_-'
      )
      if not module_name:
        raise ValueError('module_name is not a dotted identifier')
      if requirement_spec.startswith('[', offset):
        close_pos = requirement_spec.find(']', offset + 1)
        if close_pos > offset:
          offset = close_pos + 1
      if offset == len(requirement_spec):
        op = None
      else:
        for op in '>=', '=', None:
          if op is None:
            raise ValueError(
                "no valid op after module_name at %r" %
                (requirement_spec[offset:],)
            )
          if requirement_spec.startswith(op, offset):
            offset += len(op)
            break
      if op is not None and offset == len(requirement_spec):
        raise ValueError("no requirements after op %r" % (op,))
      requirements = [
          req for req in map(str.strip, requirement_spec[offset:].split(','))
          if req
      ]
      return cls(
          module_name=module_name,
          op=op,
          requirements=requirements,
          modules=modules
      )

  @pfx_method
  def resolve(self):
    ''' Return a requirement string,
        either `self.module_name` if `self.op` is `None`
        or *module_name*{`=`,`>=`}*version*
        satisfying the versions and features in `self.requirements`.
    '''
    try:
      pkg = self.modules[self.module_name]
    except KeyError as e:
      warning("cannot resolve self.module_name=%r: %s", self.module_name, e)
      print("sys.path")
      for p in sys.path:
        print(" ", p)
      raise
    if self.op is None:
      pkg_pypi_version = pkg.latest_pypi_version
      return (
          f'{self.module_name}>={pkg_pypi_version}'
          if pkg_pypi_version else self.module_name
      )
    release_versions = set()
    feature_set = set()
    for requirement in self.requirements:
      with Pfx("%r", requirement):
        feature_name, _ = get_identifier(requirement)
        if feature_name == requirement:
          feature_set.add(feature_name)
        else:
          # not a bare identifier, presume release version
          release_versions.add(requirement)
    if feature_set:
      release_version = pkg.release_with_features(feature_set)
      if release_version is None:
        raise ValueError(
            "no release version satifying feature set %r" % (feature_set,)
        )
      release_versions.add(release_version)
    if not release_versions:
      raise ValueError("no satisfactory release versions")
    if self.op == '=':
      if len(release_versions) > 1:
        raise ValueError(
            "conflicting release versions for %r: %r" %
            (self.op, release_versions)
        )
      release_version = release_versions.pop()
    elif self.op == '>=':
      # the release versions are minima: pick their maximum
      release_version = max(release_versions)
    else:
      raise RuntimeError("onimplemenented op %r" % (self.op,))
    return ''.join((self.module_name, self.op, release_version))

def release_tags(vcs):
  ''' Generator yielding the current release tags.
  '''
  for tag in vcs.tags():
    m = re_RELEASE_TAG.match(tag)
    if m:
      yield tag

def clean_release_entry(entry):
  '''Turn a VCS release log entry into some MarkDown.
  '''
  lines = list(
      filter(
          lambda line: (
              line.strip() and line != 'Summary:' and not line.
              startswith('Release information for ')
          ),
          entry.strip().split('\n')
      )
  )
  if len(lines) > 1:
    # Multiple lines become a MarkDown bullet list.
    lines = ['* ' + line for line in lines]
  return '\n'.join(lines)

@uses_upd
def prompt(message, *, fin=None, fout=None, upd):
  ''' Prompt for a one line answer.
      Return the answer with trailing newlines or carriage returns stripped.
  '''
  if fin is None:
    fin = sys.stdin
  if fout is None:
    fout = sys.stderr
  with upd.above():
    print(message, end='? ', file=fout)
    fout.flush()
    return fin.readline().rstrip('\r\n')

def ask(message, fin=None, fout=None):
  ''' Prompt with yes/no question, return true if response is "y" or "yes".
  '''
  response = prompt(message, fin=fin, fout=fout)
  response = response.rstrip().lower()
  return response in ('y', 'yes')

@contextmanager
def pipefrom(*argv, **kw):
  ''' Context manager returning the standard output file object of a command.
  '''
  with ps_pipefrom(argv, **kw) as P:
    yield P.stdout

class Modules(defaultdict):
  ''' An autopopulating dict of mod_name->Module.
  '''

  def __init__(self, *, vcs):
    super().__init__()
    self.vcs = vcs

  def __missing__(self, mod_name):
    assert isinstance(mod_name, str), "mod_name=%s:%r" % (
        type(mod_name),
        mod_name,
    )
    assert is_dotted_identifier(
        mod_name, extras='_-'
    ), ("not a dotted identifier: %r" % (mod_name,))
    try:
      M = Module(mod_name, self)
    except ValueError as e:
      raise KeyError(mod_name) from e
    self[mod_name] = M
    return M

  @pfx_method
  def resolve_requirement(self, requirement_spec):
    ''' Resolve the `install_requires` specification `requirement_spec`
        into a requirement string.
    '''
    with Pfx(requirement_spec):
      mrq = ModuleRequirement.from_requirement(requirement_spec, modules=self)
      requirement = mrq.resolve()
      return requirement

# pylint: disable=too-many-public-methods
class Module:
  ''' Metadata about a Python module/package.
  '''

  def __init__(self, name, modules):
    self.name = name
    self._module = None
    self.modules = modules
    self._distinfo = None
    self._checking = False
    self._module_problems = None
    if self.ismine() and not self.paths():
      warning("no file paths for Module(%r)", name)

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.name)

  __repr__ = __str__

  @property
  def vcs(self):
    ''' The VCS from `self.modules.vcs`.
    '''
    return self.modules.vcs

  @cached_property
  @pfx_method(use_str=True)
  def module(self):
    ''' The module for this package name.
    '''
    try:
      M = pfx_call(importlib.import_module, self.name)
    except (ImportError, ModuleNotFoundError) as e:
      warning("import fails: %s", e._)
      M = None
    except (NameError, SyntaxError) as e:
      warning("import fails: %s", e)
      M = None
    return M

  @pfx_method(use_str=True)
  def ismine(self):
    ''' Test whether this is one of my modules.
    '''
    return self.name.startswith(MODULE_PREFIX)

  @pfx_method(use_str=True)
  def isthirdparty(self):
    ''' Test whether this is a third party module.
    '''
    if self.ismine():
      return False
    if hasattr(sys, 'stdlib_module_names'):
      # what about removed batteries? just not use them?
      return not self.isstdlib()
    M = self.module
    if M is None:
      return False
    return '/site-packages/' in getattr(M, '__file__', '')

  @pfx_method(use_str=True)
  def isstdlib(self):
    ''' Test if this module exists in the stdlib.
    '''
    try:
      stdlib_module_names = sys.stdlib_module_names
    except AttributeError:
      if self.ismine():
        return False
      if self.isthirdparty():
        return False
      return True
    else:
      return self.name in stdlib_module_names

  @cached_property
  @pfx_method(use_str=True)
  def package_name(self):
    ''' The name of the package containing this module,
        or `None` if this is not inside a package.
    '''
    M = self.module
    if M is None:
      return None
    tested_name = cutsuffix(self.name, '_tests')
    if tested_name is not self.name:
      # foo_tests is considered part of foo
      return self.modules[tested_name].package_name
    try:
      pkg_name = M.__package__
    except AttributeError:
      warning("self.module has no __package__: %r", sorted(dir(M)))
      return None
    if pkg_name != self.name and hasattr(M, 'DISTINFO'):
      # standalone module like cs.py.modules (within cs.py)
      return None
    if self.ismine() and not pkg_name.startswith(MODULE_PREFIX):
      # top level modules tend to be in the notional "cs" package,
      # but they're just modules
      return None
    return pkg_name

  @property
  @pfx_method(use_str=True)
  def package(self):
    ''' The python package module for this Module
        (which may be the package module or some submodule).
    '''
    name = self.package_name
    if name is None:
      raise AttributeError("self.package_name is None")
    return self.modules[name]

  @property
  def in_package(self):
    ''' Is this module part of a package?
    '''
    return self.package_name != self.name

  @property
  def is_package(self):
    ''' Is this module a package?
    '''
    pkg_name = self.package_name
    return pkg_name is not None and pkg_name == self.name

  @property
  def needs_setuptools(self):
    ''' Do we require `setuptools` (to compile C extensions)?
    '''
    return bool(self.compute_distinfo().get('ext-modules'))

  @property
  def pkg_tags(self):
    ''' The `TagSet` for this package.
    '''
    return self.vcs.pkg_tagsets[self.name]

  def feature_map(self):
    ''' Return a `dict` mapping package names to features.
    '''
    return dict(self.pkg_tags.get('features', {}))

  @pfx_method
  def named_features(self):
    ''' Return a set containing all the feature names in use by this `Module`.
    '''
    feature_map = self.feature_map()
    all_feature_names = set()
    for feature_names in feature_map.values():
      for feature_name in feature_names:
        if not is_identifier(feature_name):
          warning("ignoring non-dentifier feature name: %r", feature_name)
        else:
          all_feature_names.add(feature_name)
    return all_feature_names

  @typechecked
  def set_feature(self, feature_name: str, release_version: str):
    ''' Include `feature_name` in the features for release `release_version`.
    '''
    feature_map = self.feature_map()
    release_features = set(feature_map.get(release_version, []))
    release_features.add(feature_name)
    feature_map[release_version] = sorted(release_features)
    self.set_tag(
        'features',
        feature_map,
        msg="features[%s]+%s" % (release_version, feature_name)
    )

  @pfx_method(use_str=True)
  def release_features(self):
    ''' Yield `(release_version,feature_names)`
        for all releases mentioned in the `features` tag.
    '''
    feature_map = self.feature_map()
    yield from feature_map.items()

  @pfx_method(use_str=True)
  def release_feature_set(self):
    ''' Yield `(release_version,feature_sets)`
        for all releases mentioned in the `features` tag
        in release order.

        This is an accumulation of features up to and including `release_version`.
        Use of this method implies an assumption that features are
        only added and never removed.
    '''
    feature_set = set()
    for release_version, release_features in sorted(self.release_features()):
      feature_set.update(release_features)
      yield release_version, set(feature_set)

  @pfx_method(use_str=True)
  def features(self, release_version=None):
    ''' Return a set of the feature names for `release_version`,
        default from the `pypi.release`.

        This is an accumulation of the features from prior releases.
    '''
    tags = self.pkg_tags
    if release_version is None:
      release_version = tags.get('pypi.release')
      if release_version is None:
        raise ValueError("no pypi.release")
    release_version = intif(float(release_version))
    release_set = set()
    for version, feature_set in sorted(self.release_feature_set()):
      if intif(float(version)) > release_version:
        break
      release_set = feature_set
    return release_set

  @pfx_method(use_str=True)
  def release_with_features(self, features):
    ''' Return the earliest release version containing all the named features.
        Return `None` if no release has all the features.
    '''
    for version, feature_set in sorted(self.release_feature_set()):
      if all(map(lambda feature: feature in feature_set, features)):
        return version
    return None

  def save_pkg_tags(self):
    ''' Sync the package `Tag`s `TagFile`, return the pathname of the tag file.
    '''
    self.vcs.pkg_tagsets.save()
    return self.vcs.pkg_tagsets.fspath

  @tag_or_tag_value
  def set_tag(self, tag_name, value, *, msg):
    ''' Set a tag value and commit the modified tag file.
    '''
    print("%s: set %s=%s" % (self.name, tag_name, value))
    self.pkg_tags.set(tag_name, value)
    self.save_pkg_tags()
    self.vcs.commit(
        f'{PKG_TAGS}: {self.name}: {msg+": " if msg else ""}set {tag_name}={value!r} [IGNORE]',
        PKG_TAGS
    )

  @cache
  def release_tags(self):
    ''' Return the `ReleaseTag`s for this package.
    '''
    myname = self.name
    return list(
        filter(
            lambda tag: tag.name == myname, (
                ReleaseTag.from_vcstag(vcstag)
                for vcstag in release_tags(self.vcs)
            )
        )
    )

  def release_log(self):
    ''' Generator yielding `(ReleaseTag,log_entry)`
        for our release tags in reverse tag order (most recent first).
    '''
    return (
        (ReleaseTag.from_vcstag(vcstag), entry)
        for vcstag, entry in self.vcs.release_log(self.name + '-')
    )

  @property
  @ensure(lambda result: result is None or isinstance(result, ReleaseTag))
  @ensure(lambda self, result: result is None or result.name == self.name)
  def latest(self):
    ''' The `ReleaseTag` of the latest release of this `Module`.
    '''
    tags = self.release_tags()
    if not tags:
      return None
    return max(tags)

  def next(self):
    ''' The next `ReleaseTag` after `self.latest`.
    '''
    latest = self.latest
    return ReleaseTag.today() if latest is None else latest.next()

  @property
  def latest_pypi_version(self):
    ''' The last PyPI version.
    '''
    return self.pkg_tags.get(TAG_PYPI_RELEASE)

  @latest_pypi_version.setter
  def latest_pypi_version(self, new_version):
    ''' Update the last PyPI version.
    '''
    self.set_tag(TAG_PYPI_RELEASE, new_version, msg='update PyPI release')

  def compute_doc(self, all_class_names=True):
    ''' Compute the components of the documentation.

        Parameters:
        * `all_class_names`: optional flag, default `False`;
          if true list all methods, otherwise constrain the listing
          to `__new__` and `__init__`.
    '''
    # break out the release log and format it
    releases = list(self.release_log())
    preamble_md = None
    postamble_parts = []
    if releases:
      release_tag, release_entry = releases[0]
      release_entry = clean_release_entry(release_entry)
      preamble_md = f'*Latest release {release_tag.version}*:\n{release_entry}'
      for release_tag, release_entry in releases:
        postamble_parts.append(
            f'*Release {release_tag.version}*:\n{clean_release_entry(release_entry)}'
        )
    full_doc = module_doc(
        self.module,
        method_names=None if all_class_names else ('__new__', '__init__')
    )
    # split the module documentation after the opening paragraph
    try:
      doc_head, doc_tail = full_doc.split('\n\n', 1)
    except ValueError:
      doc_head = full_doc
      doc_tail = ''
    # compute some distinfo stuff
    description = doc_head.replace('\n', ' ')
    if preamble_md:
      long_description = '\n\n'.join(
          [
              doc_head,
              preamble_md.rstrip(), doc_tail, '# Release Log\n\n',
              *postamble_parts
          ]
      )
    else:
      long_description = full_doc
    return SimpleNamespace(
        module_doc=full_doc,
        description=description,
        long_description=long_description,
        release_paragraphs=postamble_parts,
    )

  @property
  def latest_changeset_hash(self):
    ''' The most recent changeset hash of the files in the module.
    '''
    path_revs = self.vcs.file_revisions(self.paths())
    rev_latest = None
    for rev, node in sorted(path_revs.values()):
      if rev is not None and rev_latest is None or rev_latest < rev:
        changeset_hash = node
        rev_latest = rev
    return changeset_hash

  # pylint: disable=too-many-branches,too-many-locals
  @pfx_method
  def compute_distinfo(
      self,
      *,
      pypi_package_name=None,
      pypi_package_version=None,
  ):
    ''' Compute the distutils info mapping for this package.
        Return a new `dict` containing the mapping.
    '''
    if '>' in self.name or '=' in self.name:
      raise RuntimeError("bad module name %r" % (self.name))
    if pypi_package_name is None:
      pypi_package_name = self.name
    if pypi_package_version is None:
      pypi_package_version = self.latest.version if self.latest else None

    # prepare core distinfo
    dinfo = dict(DISTINFO_DEFAULTS)
    docs = self.compute_doc(all_class_names=True)
    dinfo.update(description=docs.description)
    dinfo.update(self.module.DISTINFO)

    # resolve install_requires
    dinfo.update(
        install_requires=self
        .resolve_requirements(dinfo.pop('install_requires', ()))
    )

    # fill in default fields
    di_defaults = {
        'author': os.environ['NAME'],
        'author_email': os.environ['EMAIL'],
        'include_package_data': True,
        'package_dir': PYLIBTOP,
    }
    for di_field in ('author', 'author_email', 'package_dir'):
      with Pfx("%r", di_field):
        if di_field not in dinfo:
          dinfo[di_field] = di_defaults[di_field]

    # fill in default classifications
    classifiers = dinfo['classifiers']
    for classifier_topic, classifier_subsection in DISTINFO_CLASSIFICATION.items(
    ):
      classifier_prefix = classifier_topic + " ::"
      classifier_value = classifier_topic + " :: " + classifier_subsection
      if not any(classifier.startswith(classifier_prefix)
                 for classifier in classifiers):
        dinfo['classifiers'].append(classifier_value)

    # derive some stuff from the classifiers
    license_type = None
    for classifier in dinfo['classifiers']:
      parts = classifier.split(' :: ')
      topic = parts[0]
      if topic == 'License':
        license_type = parts[-1]

    # source URLs
    urls = dinfo['urls']
    basepath = self.basepath
    if isdirpath(basepath):
      urls['Source'] = joinpath(MIRROR_SRCBASE, basepath)
    elif isfilepath(basepath + '.py'):
      urls['Source'] = joinpath(MIRROR_SRCBASE, basepath + '.py')
    else:
      warning("cannot compute Source URL: basepath=%r", basepath)

    if self.is_package:
      # stash the package in a top level directory of that name
      ## dinfo['package_dir'] = {package_name: package_name}
      dinfo['packages'] = [self.name]
    else:
      dinfo['py_modules'] = [self.name]

    # fill in missing but expected fields
    for kw, value in (
        ('license', license_type),
        ('name', pypi_package_name),
        ('version', pypi_package_version),
    ):
      with Pfx(kw):
        if value is None:
          warning("no value")
        elif kw in dinfo:
          if dinfo[kw] != value:
            info("publishing %s instead of %s", value, dinfo[kw])
        else:
          dinfo[kw] = value

    # check for required fields
    for kw in (
        'name',
        'description',
        'author',
        'author_email',
        'version',
        'license',
        'urls',
    ):
      with Pfx(kw):
        if kw not in dinfo:
          warning('not in distinfo', kw)

    return dinfo

  @require(lambda self: self.is_package)
  def setuptools_ext_modules(self, specs):
    ''' A copy of `specs` with the sources prepended with `package_dir`.
    '''
    full_specs = []
    for ext_mod in specs:
      full_spec = dict(ext_mod)
      full_spec['sources'] = [
          joinpath(self.basepath, srcrpath)
          for srcrpath in full_spec['sources']
      ]
      full_specs.append(full_spec)
    return full_specs

  @pfx_method
  def compute_pyproject(
      self,
      dinfo=None,
      *,
      pypi_package_name=None,
      pypi_package_version=None,
  ):
    ''' Compute the contents for the `pyproject.toml` file,
        return a `dict` for transcription as TOML.
    '''
    if dinfo is None:
      dinfo = self.compute_distinfo(
          pypi_package_name=pypi_package_name,
          pypi_package_version=pypi_package_version
      )
    elif pypi_package_name or pypi_package_version:
      raise ValueError(
          "cannot supply both dinfo and either pypi_package_name or pypi_package_version"
      )
    # we will be consuming the dict so make a copy of the presupplied mapping
    dinfo = dict(dinfo)
    projspec = dict(
        name=dinfo.pop('name').replace('.', '-'),
        description=dinfo.pop('description'),
        authors=[
            dict(name=dinfo.pop('author'), email=dinfo.pop('author_email'))
        ],
        license={"text": dinfo.pop('license')},
        keywords=dinfo.pop('keywords'),
        dependencies=dinfo.pop('install_requires'),
        urls=dinfo.pop('urls'),
        classifiers=dinfo.pop('classifiers'),
    )
    version = dinfo.pop('version', None)
    if version:
      projspec['version'] = version
    if 'extra_requires' in dinfo:
      projspec['optional-dependencies'] = dinfo.pop('extra_requires')
    if 'python_requires' in dinfo:
      projspec['requires-python'] = dinfo.pop('python_requires')
    package_dir = dinfo.pop('package_dir')
    dinfo_entry_points = dinfo.pop('entry_points', {})
    if dinfo_entry_points:
      console_scripts = dinfo_entry_points.pop('console_scripts', [])
      if console_scripts:
        projspec['scripts'] = console_scripts
      gui_scripts = dinfo_entry_points.pop('gui_scripts', [])
      if gui_scripts:
        projspec['gui-scripts'] = gui_scripts
    pyproject = {
        "project": projspec,
    }
    if self.needs_setuptools:
      pyproject["build-system"] = {
          "build-backend": "setuptools.build_meta",
          "requires": [
              "setuptools >= 61.2",
              "trove-classifiers",
              "wheel",
          ],
      }
      setuptools_cfg = {
          "package-dir": {
              "": package_dir,
          },
          "ext-modules":
          self.setuptools_ext_modules(dinfo.pop("ext-modules", [])),
      }
      if self.is_package:
        setuptools_cfg["packages"] = [self.name]
      else:
        setuptools_cfg["py-modules"] = [self.name]
      pyproject["tool"] = {
          "setuptools": setuptools_cfg,
      }
    else:
      pyproject["build-system"] = {
          "build-backend": "flit_core.buildapi",
          "requires": ["flit_core >=3.2,<4"],
      }
      pyproject["tool"] = {"flit": {"module": {"name": self.name}}}
    docs = self.compute_doc()
    projspec["readme"] = {
        "text": docs.long_description,
        "content-type": "text/markdown",
    }
    # check that everything was covered off
    if dinfo:
      warning("dinfo not emptied: %r", dinfo)
    return pyproject

  # pylint: disable=too-many-locals
  @pfx_method
  def compute_setup_cfg(
      self,
      dinfo=None,
      *,
      pypi_package_name=None,
      pypi_package_version=None,
  ) -> ConfigParser:
    ''' Compute the contents for `setup.cfg`, used by `setuptools`.
        Return a filled in `ConfigParser` instance.
    '''
    if dinfo is None:
      dinfo = self.compute_distinfo(
          pypi_package_name=pypi_package_name,
          pypi_package_version=pypi_package_version
      )
    else:
      if pypi_package_name or pypi_package_version:
        raise ValueError(
            "cannot supply both dinfo and either pypi_package_name or pypi_package_version"
        )
      # we will be consuming the dict so make a copy of the presupplied mapping
      dinfo = dict(dinfo)
    sections = {}
    # metadata section
    md = {}
    for k in ('name', 'version', 'author', 'author_email', 'license',
              'description', 'keywords', 'url', 'classifiers'):
      v = dinfo.pop(k, None)
      if v is None:
        continue
      if k in ('keywords',):
        v = ', '.join(v)
      elif k in ('classifiers', 'install_requires', 'extra_requires'):
        v = '\n' + '\n'.join(v)
      md[k] = v
    md['long_description'] = 'file: README.md'
    md['long_description_content_type'] = 'text/markdown'
    sections['metadata'] = md
    # options section
    options = {
        'package_dir': '',
        '': PYLIBTOP,
    }
    dinfo.pop('package_dir')
    install_requires = dinfo.pop('install_requires', [])
    if install_requires:
      options.update(install_requires='\n' + '\n'.join(install_requires))
    sections['options'] = options
    # options.entry_points section
    dinfo_entry_points = dinfo.pop('entry_points', {})
    if dinfo_entry_points:
      entry_points = {}
      console_scripts = dinfo_entry_points.pop('console_scripts', [])
      if console_scripts:
        entry_points['console_scripts'] = '\n' + '\n'.join(console_scripts)
      if entry_points:
        sections['options.entry_points'] = entry_points
    # options.extras_require section
    dinfo_extra_requires = dinfo.pop('extras_requires', {})
    if dinfo_extra_requires:
      sections['options.extras_require'] = {
          k: '; '.join(v)
          for k, v in sorted(dinfo_extra_requires.items())
      }
    cfg = ConfigParser()
    for section_name, section in sections.items():
      cfg[section_name] = section
    # check that everything was covered off
    if dinfo:
      warning("dinfo not emptied: %r", dinfo)
    return cfg

  @property
  def basename(self):
    ''' The last component of the package name.
    '''
    return self.name.split('.')[-1]

  @property
  def basepath(self):
    ''' The base path for this package, *PYLIBTOP*`/`*pkg*`/'*name*.
    '''
    return os.sep.join([PYLIBTOP] + self.name.split('.'))

  @property
  def toppath(self):
    ''' The top file of the package:
        *basepath*`/__init__.py` for packages,
        *basepath*`.py` for modules.
    '''
    basepath = self.basepath
    if isdirpath(basepath):
      return joinpath(basepath, '__init__.py')
    return basepath + '.py'

  @cache
  @pfx_method(use_str=True)
  def paths(self):
    ''' Return a list of the paths associated with this package
        relative to `top_dirpath` (default `'.'`).

        Note: this is based on the current checkout state instead
        of some revision because "hg archive" complains if globs
        match no paths, and aborts.
    '''
    skip_suffixes = 'pyc', 'o', 'orig', 'so'
    basepath = self.basepath
    if isdirpath(basepath):
      pathlist = list(
          scandirpaths(
              basepath,
              skip_suffixes=skip_suffixes,
              sort_names=True,
          )
      )
    else:
      updir = dirname(basepath)
      base_ = basename(basepath) + '.'
      pathlist = list(
          scandirpaths(
              updir,
              skip_suffixes=skip_suffixes,
              name_selector=lambda name: name.startswith(base_),
              sort_names=True,
          )
      )
    if not pathlist:
      warning("no paths for %s", self)
    return pathlist

  def resolve_requirements(self, requirement_specs):
    ''' Resolve the requirement specifications from `requirement_specs`
        into valid `install_requires` specifications.
    '''
    return [
        self.modules.resolve_requirement(spec)
        for spec in sorted(requirement_specs)
    ]

  @staticmethod
  def reldistfiles(pkg_dir):
    ''' Return the relative paths existing within `pkg_dir`.

        TODO: does not recurse: should this just run listdir?
    '''
    return [
        relpath(fullpath, pkg_dir)
        for fullpath in glob(joinpath(pkg_dir, 'dist/*'))
    ]

  @pfx_method
  def upload_dist(self, pkg_dir, repo=None):
    ''' Upload the package to PyPI using twine.
    '''
    if repo is None:
      repo = 'pypi'
    distfiles = self.reldistfiles(pkg_dir)
    run(['twine', 'upload', '--repository', repo, *distfiles], cwd=pkg_dir)

  @pfx_method(use_str=True)
  def log_since(self, vcstag=None, ignored=False):
    ''' Generator yielding (files, line) tuples
        for log lines since the last release for the supplied `prefix`.

        Parameters:
        * `vcstag`: the reference revision, default `self.latest`
        * `ignored`: if true (default `False`) include log entries
          containing the string `'IGNORE'` in the description
    '''
    if vcstag is None:
      latest = self.latest
      if latest is None:
        vcstag = "0"
        warning(f"no release tags, starting from revision {vcstag}")
      else:
        vcstag = self.latest.vcstag
    paths = self.paths()
    latest = self.latest
    latest_release_line = 'Release information for ' + latest.vcstag + '.' if latest else None
    return (
        ([filename
          for filename in files
          if filename in paths], firstline)
        for files, firstline in self.vcs.log_since(vcstag, paths)
        if (
            ignored or (
                'IGNORE' not in firstline and (
                    latest_release_line is None
                    or firstline != latest_release_line
                )
            )
        )
    )

  def uncommitted_paths(self):
    ''' Generator yielding paths relevant to this package
        with uncommitted changes.
    '''
    return self.vcs.uncommitted(self.paths())

  @pfx_method(use_str=True)
  def patch__version__(self, version):
    ''' Patch the `__version__` module attribute.
        Does not commit the change.
    '''
    version_line = '__version__ = ' + repr(version) + '\n'
    toppath = self.toppath
    with Pfx(toppath):
      if toppath in self.uncommitted_paths():
        raise ValueError("has uncommited changes")
      with pfx_call(open, toppath, encoding='utf8') as tf:
        lines = tf.readlines()
      patched = False
      distinfo_index = None
      for i, line in enumerate(lines):
        if line.startswith('DISTINFO = '):
          distinfo_index = i
        elif line.startswith('__version__ = '):
          lines[i] = version_line
          patched = True
          break
      if not patched:
        if distinfo_index is None:
          raise ValueError("no __version__ line and no DISTINFO line")
        lines[distinfo_index:distinfo_index] = version_line, '\n'
      with Pfx("rewrite %r", toppath):
        with pfx_call(open, toppath, 'w', encoding='utf8') as tf:
          for line in lines:
            tf.write(line)
    return toppath

  @cached_property
  @pfx_method(use_str=True)
  def DISTINFO(self):
    ''' The `DISTINFO` from `self.module`.
    '''
    D = {}
    M = self.module
    if M is None:
      warning("cannot load module")
    else:
      try:
        D = M.DISTINFO
      except AttributeError:
        pkg_name = self.package_name
        if pkg_name is None or pkg_name == self.name:
          warning("missing DISTINFO")
        else:
          # look in the package
          P = self.modules[pkg_name]
          PM = P.module
          try:
            D = PM.DISTINFO
          except AttributeError:
            warning("missing and also missing from package %r", pkg_name)
          else:
            ##warning("DISTINFO from package %r", pkg_name)
            pass
      else:
        ##warning("DISTINFO from this module")
        pass
    return D

  @property
  def requires(self):
    ''' Return other nonstdlib packages required by this module.
    '''
    return self.DISTINFO.get('install_requires', [])

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  @cache
  @uses_runstate
  def problems(self, *, runstate=RunState):
    ''' Sanity check of this module.

        This is a list of problems,
        each of which is either a string
        or a mapping of required package name to its problems.
    '''
    problems = self._module_problems
    # TODO": lru_cache?
    if problems is not None:
      return problems
    problems = self._module_problems = []
    # check for conflicts with third parties
    allowed_conflicts = ('cs.resources',)
    if self.name not in allowed_conflicts:
      for third_party_listpath in glob('3rd-party-conflicts/*'):
        with Pfx(third_party_listpath):
          with open(third_party_listpath) as f:
            for lineno, line in enumerate(f, 1):
              with Pfx(lineno):
                line = line.rstrip().replace('-', '_')
                if not line or line.startswith('#'):
                  continue
                if self.name == line:
                  problems.append(
                      f'name conflicts with {third_party_listpath}:{lineno}: {line!r}'
                  )
    # see if this package has been marked "ok" as of a particular revision
    latest_ok_rev = self.pkg_tags.get('ok_revision')
    unreleased_logs = None
    if latest_ok_rev:
      post_ok_commits = list(self.log_since(vcstag=latest_ok_rev))
      if not post_ok_commits:
        return problems
      unreleased_logs = post_ok_commits
    runstate.raiseif()
    subproblems = defaultdict(list)
    pkg_name = self.package_name
    if pkg_name is None:
      pkg_prefix = None
    else:
      pkg_prefix = pkg_name + '.'
    M = self.module
    if M is None:
      problems.append("module import fails")
      return problems
    import_names = []
    for fspath in self.paths():
      rfspath = relpath(fspath, PYLIBTOP)
      module_name = cutsuffix(rfspath, '.py')
      if module_name == fspath:
        warning("skip non-.py path %r", fspath)
        continue
      module_name = module_name.replace('/', '.')
      for import_name in direct_imports(fspath, module_name):
        if self.modules[import_name].isstdlib():
          continue
        if import_name.endswith('_tests'):
          continue
        if import_name == pkg_name:
          # tests usually import the package - this is not a dependency
          continue
        if pkg_prefix and import_name.startswith(pkg_prefix):
          # package components are not a dependency
          continue
        import_names.append(import_name)
    import_names = sorted(set(import_names))
    # check the DISTINFO
    distinfo = getattr(M, 'DISTINFO', None)
    if not distinfo:
      problems.append("missing DISTINFO")
    else:
      distinfo_requires = distinfo.get('install_requires')
      if distinfo_requires is None:
        problems.append("missing DISTINFO[install_requires]")
      else:
        distinfo_requires_names = [
            get_dotted_identifier(dirq)[0] for dirq in distinfo_requires
        ]
        if sorted(distinfo_requires_names) != import_names:
          new_import_names = set(import_names) - set(distinfo_requires_names)
          old_import_names = set(distinfo_requires_names) - set(import_names)
          problems.append(
              (
                  "DISTINFO[install_requires]=%r"
                  "  != direct_imports=%r\n"
                  "  new imports %r\n"
                  "  removed imports %r"
              ) % (
                  distinfo_requires, sorted(import_names),
                  sorted(new_import_names), sorted(old_import_names)
              )
          )
        for import_name in import_names:
          runstate.raiseif()
          if not import_name.startswith(MODULE_PREFIX):
            continue
          import_problems = self.modules[import_name].problems()
          if import_problems:
            subproblems[import_name] = import_problems
    for required_name in sorted(self.requires):
      with Pfx(required_name):
        dotted_name, _ = get_dotted_identifier(required_name)
        if not dotted_name:
          problems.append(
              "requirement %r does not start with a module name" %
              (required_name,)
          )
        elif dotted_name not in self.imported_names:
          problems.append("requirement %r not imported" % (required_name,))
    # check that this package has files
    if not self.paths():
      problems.append("no files")
    # check for unreleased commit logs
    if unreleased_logs is None:
      unreleased_logs = list(self.log_since())
    if unreleased_logs:
      problems.append(['unreleased commits'] + unreleased_logs)
    # check for uncommited changes
    paths = self.paths()
    changed_paths = [
        changed_path for changed_path in self.vcs.uncommitted()
        if changed_path in paths
    ]
    if changed_paths:
      problems.append("%d modified files" % (len(changed_paths),))
    # append submodule problems if present
    if subproblems:
      problems.append(subproblems)
    return problems

  @cached_property
  def imported_names(self):
    ''' Return a set containing the module names imported by this module
        both directly and indirectly.
    '''
    subnames = set()
    for path in self.paths():
      subimports = direct_imports(path, self.name)
      subnames.update(subimports)
    return subnames

  def imported_modules(self, prefix=MODULE_PREFIX):
    ''' Generator yielding directly imported `Module`s.
    '''
    for name in sorted(self.imported_names):
      if name.startswith(prefix) and name != self.name:
        yield self.modules[name]

  @contextmanager
  @typechecked
  def release_dir(
      self, vcs, vcs_revision, *, persist: bool = False, bare: bool = False
  ):
    ''' Context manager to prepare a package release directory.
        It yields the a 2-tuple of `(release_dirpath,dist_rpaths)`
        being the release directory path and a `dict` mapping
        `'sdist'` and `'wheel'` to the built artifacts' paths
        relative to `release_dirpath`.

        Parameters:
        * `vcs`: the version control system
        * `vcs_revision`: the revision to release, usually a tag name
        * `persist`: optional flag or directory name, default `False`;
          if false, create the release in a temporary directory
          which will be tidied up on exit from the context manager;
          if true, create the release in a directory which is
          `persist` if that is a string, otherwise a name derived
          from the `vcs_revision` and the current time
        * `bare`: optional flag, default `False`;
          if true, do not prepare the package metadata files and
          the distribution files
    '''
    path_name, path_version = vcs_revision.split('-')
    path_name = path_name.replace('.', '_')
    release_dirpath = f'{path_name}-{path_version}--{datetime.now().isoformat()}'
    try:
      dist_rpaths = self.prepare_release_dir(
          release_dirpath, self, vcs, vcs_revision, bare=bare
      )
      yield release_dirpath, dist_rpaths
    except:
      persist = False
      raise
    finally:
      if not persist and isdirpath(release_dirpath):
        pfx_call(rmtree, release_dirpath)

  # this is a static method to accomodate @atomic_directory
  @staticmethod
  @atomic_directory
  @typechecked
  def prepare_release_dir(
      release_dirpath: str,
      self: 'Module',
      vcs: VCS,
      vcs_revision: str,
      bare: bool = False
  ):
    ''' Create and fill in a release directory at `release_dirpath`.
        Return a `dict` mapping `'sdist'` and `'wheel'` to the built
        artifacts' paths relative to `release_dirpath`.
    '''
    # mkdir omitted, done by @atomic_directory
    # unpack the source
    vcs.hg_cmd(
        'archive',
        ('-r', vcs_revision),
        *vcs.hg_include(self.paths()),
        '--',
        release_dirpath,
    )
    # flit has src/ hardwired
    os.symlink(PYLIBTOP, joinpath(release_dirpath, 'src'))
    if bare:
      dist_rpaths = {}
    else:
      self.prepare_autofiles(release_dirpath)
      self.prepare_metadata(release_dirpath)
      dist_rpaths = self.prepare_dist(release_dirpath, vcs_revision)
    return dist_rpaths

  def prepare_autofiles(self, pkg_dir):
    ''' Create automatic files in `pkg_dir`.

        Currently this prepares the man files from `*.[1-9].md` files.
    '''
    for rpath in self.paths():
      with Pfx(rpath):
        path = normpath(joinpath(pkg_dir, rpath))
        if fnmatch(path, '*.[1-9].md'):
          # create man page from markdown source
          manpath, _ = splitext(path)
          if existspath(manpath):
            warning("man path already exists: %r", manpath)
            continue
          with pfx_call(open, manpath, 'x') as manf:
            # was md2man-roff
            run(['go-md2man', path], stdout=manf)
          continue

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def prepare_metadata(self, pkg_dir):
    ''' Prepare an existing package checkout as a package for upload or install.

        This writes the following files:
        * `MANIFEST.in`: list of additional files
        * `README.md`: a README containing the long_description
        * `pyproject.toml`: the TOML configuration file
    '''
    # write MANIFEST.in
    manifest_path = joinpath(pkg_dir, 'MANIFEST.in')
    with pfx_call(open, manifest_path, "x") as mf:
      # TODO: support extra files
      print('include', 'README.md', file=mf)
      subpaths = self.paths()
      for subpath in subpaths:
        with Pfx(subpath):
          if any(
              (fnmatch(subpath, ptn) for ptn in ("*.c", "*.md", "*.[1-9]"))):
            print('include', subpath, file=mf)

    # create README.md
    docs = self.compute_doc(all_class_names=True)
    with pfx_call(open, joinpath(pkg_dir, 'README.md'), 'x') as rf:
      print(docs.long_description, file=rf)

    # write the pyproject.toml file
    proj = self.compute_pyproject()
    with pfx_call(open, joinpath(pkg_dir, 'pyproject.toml'), 'xb') as tf:
      tomli_w.dump(proj, tf, multiline_strings=True)

  def prepare_dist(self, pkg_dir, vcs_version):
    ''' Run "python3 -m build ." inside `pkg_dir`, making files in `dist/`.
    '''
    path_name, path_version = vcs_version.split('-')
    path_name = path_name.replace('.', '_')
    distdirpath = joinpath(pkg_dir, 'dist')
    if isdirpath(distdirpath):
      rmtree(distdirpath)
    run(
        [
            ('python3', '-m', 'uv', 'build'),
            ('--out-dir', 'dist'),
            ('--sdist', '--wheel'),
            (
                #'--skip-dependency-check',
                #'--no-isolation',
            ),
            '.',
        ],
        cwd=pkg_dir,
    )
    sdist_rpath = None
    wheel_rpath = None
    for ext in 'tar.gz', 'whl':
      for distpath in glob(f'{pkg_dir}/dist/*.{ext}'):
        assert existspath(distpath)
        basepath = basename(distpath)
        assert basepath.startswith(f'{path_name}-{path_version}')
        if distpath.endswith('.tar.gz'):
          run(['tar', 'tvzf', distpath])
          assert sdist_rpath is None
          sdist_rpath = f'dist/{basepath}'
        elif distpath.endswith('.whl'):
          run(['unzip', '-l', distpath])
          assert wheel_rpath is None
          wheel_rpath = f'dist/{basepath}'
        else:
          raise RuntimeError(f'unexpected dist file extension: {distpath!r}')
    assert sdist_rpath is not None
    assert wheel_rpath is not None
    return dict(sdist=sdist_rpath, wheel=wheel_rpath)

class CSReleaseCommand(BaseCommand):
  ''' The `cs-release` command line implementation.
  '''

  SUBCOMMAND_ARGV_DEFAULT = ['releases']
  GETOPT_SPEC = 'fqv'
  USAGE_FORMAT = '''Usage: {cmd} [-fqv] subcommand [subcommand-args...]
      -q  Quiet. Not verbose.
      -v  Verbose.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    release_message: str = None

    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        f=(
            'force',
            ''' Force. Sanity checks that would stop some actions normally
                will not prevent them.
            ''',
        )
    )

    def stderr_isatty():
      ''' Test whether `sys.stderr` is a tty.
      '''
      try:
        return sys.stderr.isatty()
      except AttributeError:
        return False

    verbose: bool = field(default_factory=stderr_isatty)
    colourise: bool = field(default_factory=stderr_isatty)
    pkg_tagsets: TagFile = field(
        default_factory=lambda:
        TagFile(joinpath(VCS_Hg().get_topdir(), PKG_TAGS))
    )
    modules: "Modules" = field(default_factory=lambda: Modules(vcs=VCS_Hg()))

    @property
    def vcs(self):
      ''' The prevailing VCS.
      '''
      return self.modules.vcs

  def apply_opts(self, opts):
    ''' Apply the command line options mapping `opts` to `options`.
    '''
    options = self.options
    for opt, _ in opts:
      if opt == '-f':
        options.force = True
      elif opt == '-q':
        options.verbose = False
      elif opt == '-v':
        options.verbose = True
      else:
        raise NotImplementedError("unhandled option: %s" % (opt,))

  @contextmanager
  def run_context(self):
    ''' Arrange to autosave the package tagsets.
    '''
    with super().run_context():
      with FSTags():
        with self.options.pkg_tagsets:
          with stackattrs(self.options.vcs,
                          pkg_tagsets=self.options.pkg_tagsets):
            yield

  ##  export      Export release to temporary directory, report directory.
  ##  freshmeat-submit Announce last release to freshmeat.

  @uses_runstate
  def cmd_check(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} pkg_name...
          Perform sanity checks on the names packages.
    '''
    if not argv:
      raise GetoptError("missing package names")
    options = self.options
    xit = 0
    for pkg_name in argv:
      runstate.raiseif()
      with Pfx(pkg_name):
        status("...")
        pkg = options.modules[pkg_name]
        problems = pkg.problems()
        status('')
        if problems:
          xit = 1
          for problem in problems:
            if isinstance(problem, str):
              warning(problem)
            elif isinstance(problem, list):
              label, *values = problem
              warning("%s:", label)
              for subproblem in values:
                warning(
                    "  %s", ', '.join(
                        map(str, subproblem) if
                        isinstance(subproblem, (list, tuple)) else subproblem
                    )
                )
            else:
              for subpkg, subproblems in sorted(problem.items()):
                warning(
                    "%s: %s", subpkg, ', '.join(
                        subproblem if isinstance(subproblem, str) else (
                            (
                                (
                                    subproblem[0] if len(subproblem) ==
                                    1 else "%s (%d)" %
                                    (subproblem[0], len(subproblem) - 1)
                                )
                            ) if isinstance(subproblem, list) else (
                                "{" + ", ".join(
                                    "%s: %d %s" % (
                                        subsubkey, len(subsubproblems),
                                        "problem" if len(subsubproblems) ==
                                        1 else "problems"
                                    ) for subsubkey, subsubproblems in
                                    sorted(subproblem.items())
                                ) + "}"
                            ) if hasattr(subproblem, 'items') else
                            repr(subproblem)
                        ) for subproblem in subproblems
                    )
                )
    return xit

  def cmd_checkout(self, argv):
    ''' Usage: {cmd} pkg_name [revision]
          Check out the named package.
    '''
    if not argv:
      raise GetoptError("missing package name")
    options = self.options
    vcs = options.vcs
    pkg_name = argv.pop(0)
    pkg = options.modules[pkg_name]
    if argv:
      version = argv.pop(0)
    else:
      version = pkg.latest.version
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    release = ReleaseTag(pkg_name, version)
    vcstag = release.vcstag
    with pkg.release_dir(vcs, vcstag,
                         persist=True) as (checkout_dirpath, rpaths):
      print(checkout_dirpath)
      for artifact, rpath in sorted(rpaths.items()):
        print(" ", artifact, rpath)

  def cmd_distinfo(self, argv):
    ''' Usage: {cmd} pkg_name
          Print out the package distinfo mapping.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if not is_dotted_identifier(pkg_name):
      raise GetoptError("invalid package name: %r" % (pkg_name,))
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pkg = self.options.modules[pkg_name]
    pprint(pkg.compute_distinfo())

  def cmd_last(self, argv):
    ''' Usage: {cmd} pkg_names...
          Print the latest release tags for the names packages.
    '''
    if not argv:
      raise GetoptError("missing package names")
    options = self.options
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        latest = pkg.latest
        print(pkg.name, latest.version if latest else "NONE")

  def cmd_log(self, argv):
    ''' Usage: {cmd} pkg_name
          Print the commit log since the latest release.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pkg = self.options.modules[pkg_name]
    for files, firstline in pkg.log_since():
      files = [
          filename[11:] if filename.startswith('lib/python/') else filename
          for filename in files
      ]
      print(' '.join(files) + ':', firstline)

  def cmd_ls(self, argv):
    ''' Usage: {cmd} pkg_name
          List the file paths associated with this package.
    '''
    if not argv:
      raise GetoptError("missing package names")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments after package name: %r" % (argv,))
    with Pfx(pkg_name):
      pkg = self.options.modules[pkg_name]
      for filepath in pkg.paths():
        print(filepath)

  def cmd_next(self, argv):
    ''' Usage: next pkg_names...
          Print package names and their next release tag.
    '''
    if not argv:
      raise GetoptError("missing package names")
    options = self.options
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        print(pkg.name, pkg.next().version)

  def cmd_ok(self, argv):
    ''' Usage: {cmd} pkg_name [changset-hash]
          Mark a particular changeset as ok for purposes of "check".
          This lets one accept cosmetic outstanding commits as irrelevant.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      changeset_hash = argv.pop(0)
    else:
      changeset_hash = None
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    pkg = options.modules[pkg_name]
    if changeset_hash is None:
      changeset_hash = pkg.latest_changeset_hash
      if changeset_hash is None:
        error("no changeset revisions for paths: %r", pkg.paths())
        return 1
    pkg.set_tag('ok_revision', changeset_hash, msg="mark revision as ok")
    return 0

  def cmd_package(self, argv):
    ''' Usage: package [--bare] pkg_name [version]
          Export the package contents as a prepared package directory.
          --bare  Do not prepare any of the metadata or distribution files.
    '''
    bare = False
    if argv and argv[0] == '--bare':
      bare = True
    if not argv:
      raise GetoptError("missing package name")
    options = self.options
    vcs = options.vcs
    pkg_name = argv.pop(0)
    try:
      pkg = options.modules[pkg_name]
    except KeyError as e:
      raise GetoptError(f'unknown {pkg_name=}') from e
    if argv:
      version = argv.pop(0)
    else:
      version = pkg.latest.version
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    release = ReleaseTag(pkg_name, version)
    vcstag = release.vcstag
    with pkg.release_dir(
        vcs,
        vcstag,
        bare=bare,
        persist=True,
    ) as (pkgpath, rpaths):
      print(pkgpath)
      for artifact, rpath in sorted(rpaths.items()):
        print(" ", artifact, rpath)

  def cmd_pypi(self, argv):
    ''' Usage: {cmd} [-r repository] pkg_names...
          Push the named packages to PyPI.
          -r repository Specify the repository to which to upload.
    '''
    repo = 'pypi'
    if argv and argv[0] == '-r':
      argv.pop(0)
      repo = argv.pop(0)
    if not argv:
      raise GetoptError("missing package names")
    options = self.options
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        vcs = options.vcs
        release = pkg.latest
        vcstag = release.vcstag
        with pkg.release_dir(vcs, vcstag) as (pkgpath, rpaths):
          pkg.upload_dist(pkgpath, repo)
        pkg.latest_pypi_version = release.version

  def cmd_pyproject_toml(self, argv):
    ''' Usage: {cmd} pkg_name
          Transcribe the contents of pyproject.toml to the standard output.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pkg = self.options.modules[pkg_name]
    pyproject = pkg.compute_pyproject()
    sys.stdout.write(tomli_w.dumps(pyproject, multiline_strings=True))

  @popopts(
      a=(
          'all_class_names',
          '''' Document all public class members (default is just
               __new__ and __init__ for the PyPI README.md file).''',
      ),
      raw='Do not format output with glow(1) on a tty.',
  )
  def cmd_readme(self, argv):
    ''' Usage: {cmd} [-a] pkg_name
          Print out the package long_description.
    '''
    options = self.options
    all_class_names = options.all_class_names
    raw_mode = options.raw
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    pkg = options.modules[pkg_name]
    docs = pkg.compute_doc(all_class_names=all_class_names)
    if not raw_mode and sys.stdout.isatty():
      with ps_pipeto(['glow', '-', '-p']) as P:
        print(docs.long_description, file=P.stdin)
    else:
      print(docs.long_description)

  # pylint: disable=too-many-locals,too-many-return-statements
  # pylint: disable=too-many-branches,too-many-statements
  @uses_upd
  @popopts(f='force', m_='release_message')
  def cmd_release(self, argv, *, upd):
    ''' Usage: {cmd} [-f] [-m release-message] pkg_name
          Issue a new release for the named package.
    '''
    options = self.options
    force = options.force
    release_message = options.release_message
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pkg = options.modules[pkg_name]
    vcs = options.vcs
    # issue new release tag
    print("new release for %s ..." % (pkg.name,))
    outstanding = list(pkg.uncommitted_paths())
    if outstanding:
      print("uncommitted changes exist for these files:")
      for path in sorted(outstanding):
        print(' ', path)
      error("Aborting release; please commit or shelve/stash these changes.")
      return 1
    changes = list(pkg.log_since())
    if not changes:
      if force:
        warning("no commits since last release, making release anyway")
      else:
        error("no changes since last release, not making new release")
        return 1
    if release_message is None:
      print("Changes since the last release:")
      for files, firstline in changes:
        print(" ", ' '.join(files) + ': ' + firstline)
      print()
      with upd.above():
        with pipefrom('readdottext', stdin=sys.stdin) as dotfp:
          release_message = dotfp.read().rstrip()
    else:
      print("Release message:")
      print(release_message)
    if not release_message:
      error("empty release message, not making new release")
      return 1
    print("Feature and bug names should be space separated identifiers.")
    existing_features = pkg.named_features()
    if existing_features:
      print("Existing features:", ' '.join(sorted(existing_features)))
    features = [] if force else list(
        filter(None,
               prompt('Any named features with this release').split())
    )
    if any(map(
        lambda feature_name: (not is_dotted_identifier(feature_name) or
                              feature_name.startswith('fix_')),
        features,
    )):
      error("Rejecting nonidentifiers or fix_* names in feature list.")
      return 1
    bugfixes = [] if force else list(
        filter(None,
               prompt('Any named bugs fixed with this release').split())
    )
    if any(map(
        lambda bug_name:
        (not is_dotted_identifier(bug_name) or bug_name.startswith('fix_')),
        bugfixes,
    )):
      error("Rejecting nonidentifiers or fix_* names in feature list.")
      return 1
    bugfixes = list(map(lambda bug_name: 'fix_' + bug_name, bugfixes))
    latest = pkg.latest
    next_release = pkg.latest.next() if latest else ReleaseTag.today(pkg.name)
    next_vcstag = next_release.vcstag
    if (not force and not ask("Confirm new release for %r as %r" %
                              (pkg.name, next_vcstag))):
      error("aborting release at user request")
      return 1
    rel_dir = joinpath('release', next_vcstag)
    pfx_call(os.mkdir, rel_dir)
    summary_filename = joinpath(rel_dir, 'SUMMARY.txt')
    with Pfx(summary_filename):
      with pfx_call(open, summary_filename, 'w', encoding='utf8') as sfp:
        print(release_message, file=sfp)
    changes_filename = joinpath(rel_dir, 'CHANGES.txt')
    with Pfx(changes_filename):
      with pfx_call(open, changes_filename, 'w', encoding='utf8') as cfp:
        for files, firstline in changes:
          print(' '.join(files) + ': ' + firstline, file=cfp)
    versioned_filename = pkg.patch__version__(next_release.version)
    vcs.add_files(summary_filename, changes_filename)
    vcs.commit(
        'Release information for %s.\nSummary:\n%s' %
        (next_vcstag, release_message), summary_filename, changes_filename,
        versioned_filename
    )
    vcs.tag(
        next_vcstag,
        message="%s: added tag %s [IGNORE]" % (pkg.name, next_vcstag)
    )
    pkg.patch__version__(next_release.version + '-post')
    vcs.commit(
        '%s: bump __version__ to %s to avoid misleading value'
        ' for future unreleased changes [IGNORE]' %
        (pkg.name, next_release.version + '-post'), versioned_filename
    )
    pkg.set_tag(
        'ok_revision', pkg.latest_changeset_hash, msg="mark revision as ok"
    )
    for feature_name in features + bugfixes:
      pkg.set_feature(feature_name, next_release.version)
    return 0

  def cmd_releases(self, argv):
    ''' Usage: {cmd} [package_name...]
          List package names and their latst PyPI releases.
    '''
    options = self.options
    if argv:
      pkg_names = argv
    else:
      pkg_names = sorted(options.pkg_tagsets.keys())
    with Upd().insert(1) as proxy:
      for pkg_name in progressbar(pkg_names, label="packages"):
        with Pfx(pkg_name):
          proxy.prefix = f'{pkg_name}: '
          if pkg_name.startswith(MODULE_PREFIX):
            pkg = options.modules[pkg_name]
            pypi_release = pkg.pkg_tags.get(TAG_PYPI_RELEASE)
            if pypi_release is not None:
              problems = pkg.problems()
              if not problems:
                proxy.text = "ok"
              else:
                proxy.text = f'{len(problems)} problems'
                problem_text = (
                    "%d problems" % (len(problems),) if problems else "ok"
                )
                if problems and options.colourise:
                  problem_text = colourise(problem_text, 'yellow')
                list_argv = [
                    pkg_name,
                    pypi_release,
                    problem_text,
                ]
                features = pkg.features(pypi_release)
                if features:
                  list_argv.append('[' + ' '.join(sorted(features)) + ']')
                print(*list_argv)
    return 0

  def cmd_resolve(self, argv):
    ''' Usage: {cmd} requirements_spec...
          Resolve and print each requirements_spec into a valid install_requires value.
    '''
    if not argv:
      raise GetoptError("missing requirements_specs")
    xit = 0
    modules = self.options.modules
    for requirement_spec in argv:
      with Pfx(requirement_spec):
        try:
          requirement = modules.resolve_requirement(requirement_spec)
        except ValueError as e:
          error("invalid requirement_spec: %s", e)
        else:
          print(requirement_spec, requirement)
    return xit

  def cmd_setup_cfg(self, argv):
    ''' Usage: {cmd} pkg_name
          Transcribe the contents of setup.cfg to the standard output.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pkg = self.options.modules[pkg_name]
    setup_cfg = pkg.compute_setup_cfg()
    setup_cfg.write(sys.stdout)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
