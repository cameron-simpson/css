#!/usr/bin/env python3
#

''' My Python package release script.
'''

from __future__ import print_function
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from functools import partial
from getopt import GetoptError
from glob import glob
import importlib
import os
import os.path
from os.path import (
    basename,
    dirname,
    exists as pathexists,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    splitext,
)
from pprint import pprint, pformat
import re
from subprocess import Popen
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from icontract import ensure
from cs.app.lastvalue import LastValues
from cs.cmdutils import BaseCommand
from cs.dateutils import isodate
from cs.deco import cachedmethod
from cs.lex import cutsuffix, get_dotted_identifier
from cs.logutils import error, warning, info, status
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method, XP
import cs.psutils
from cs.py.doc import module_doc
from cs.py.func import prop
from cs.py.modules import direct_imports
from cs.sh import quotestr as shq, quotecmd as shqv
from cs.tagset import TagFile, tag_or_tag_value
from cs.upd import Upd
from cs.vcs.hg import VCS_Hg

URL_PYPI_PROD = 'https://pypi.python.org/pypi'
URL_PYPI_TEST = 'https://test.pypi.org/legacy/'

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/'

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return CSReleaseCommand().run(argv, cmd=cmd)

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

# the prefix of interesting packages
MODULE_PREFIX = 'cs.'

TAG_PYPI_RELEASE = 'pypi.release'

# defaults for packages without their own specifics
DISTINFO_DEFAULTS = {
    'url': 'https://bitbucket.org/cameron_simpson/css/commits/all',
}

re_RELEASE_TAG = re.compile(
    #  name       - YYYYMMDD                            [.n]
    r'([a-z][^-]*)-(2[0-9][0-9][0-9][01][0-9][0-3][0-9](\.[1-9]\d*)?)$'
)

class CSReleaseCommand(BaseCommand):
  ''' The `cs-release` command line implementation.
  '''

  GETOPT_SPEC = 'fqv'
  USAGE_FORMAT = '''Usage: {cmd} [-f] subcommand [subcommand-args...]
      -f  Force. Sanity checks that would stop some actions normally
          will not prevent them.
      -q  Quiet. Not verbose.
      -v  Verbose.
  '''

  @classmethod
  def apply_defaults(cls, options):
    cmd = basename(options.cmd)
    if cmd.endswith('.py'):
      cmd = 'cs-release'
    options.cmd = cmd
    # verbose if stderr is a tty
    try:
      options.verbose = sys.stderr.isatty()
    except AttributeError:
      options.verbose = False
    # TODO: get from cs.logutils?
    options.verbose = sys.stderr.isatty()
    options.force = False
    options.vcs = VCS_Hg()
    options.pkg_tagsets = TagFile(joinpath(options.vcs.get_topdir(), PKG_TAGS))
    options.last_values = LastValues()
    options.modules = Modules()
    options.modules.options = options

  @staticmethod
  def apply_opts(opts, options):
    ''' Apply the command line options mapping `opts` to `options`.
    '''
    for opt, _ in opts:
      if opt == '-f':
        options.force = True
      elif opt == '-q':
        options.verbose = False
      elif opt == '-v':
        options.verbose = True
      else:
        raise RuntimeError("unhandled option: %s" % (opt,))

  ##  export      Export release to temporary directory, report directory.
  ##  freshmeat-submit Announce last release to freshmeat.

  @staticmethod
  def cmd_check(argv, options):
    ''' Usage: {cmd} pkg_name...
          Perform sanity checks on the names packages.
    '''
    if not argv:
      raise GetoptError("missing package names")
    xit = 0
    with Upd(sys.stderr):
      for pkg_name in argv:
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

  @staticmethod
  def cmd_checkout(argv, options):
    ''' Usage: {cmd} pkg_name [revision]
          Check out the named package.
    '''
    if not argv:
      raise GetoptError("missing package name")
    vcs = options.vcs
    pkg_name = argv.pop(0)
    pkg = options.modules[pkg_name]
    if argv:
      version = argv.pop(0)
    else:
      version = pkg.latest.version
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    release = ReleaseTag(pkg_name, version)
    vcstag = release.vcstag
    checkout_dir = vcstag
    ModulePackageDir.fill(
        checkout_dir, pkg, vcs, vcstag, do_mkdir=True, bare=True
    )
    print(checkout_dir)

  @staticmethod
  def cmd_distinfo(argv, options):
    ''' Usage: {cmd} pkg_name
          Print out the package distinfo mapping.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    pkg = options.modules[pkg_name]
    pprint(pkg.compute_distinfo())

  @staticmethod
  def cmd_last(argv, options):
    ''' Usage: {cmd} pkg_names...
          Print the latest release tags for the names packages.
    '''
    if not argv:
      raise GetoptError("missing package names")
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        latest = pkg.latest
        print(pkg.name, latest.version if latest else "NONE")

  @staticmethod
  def cmd_log(argv, options):
    ''' Usage: {cmd} pkg_name
          Print the commit log since the latest release.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r", argv)
    pkg = options.modules[pkg_name]
    for files, firstline in pkg.log_since():
      files = [
          filename[11:] if filename.startswith('lib/python/') else filename
          for filename in files
      ]
      print(' '.join(files) + ':', firstline)

  @staticmethod
  def cmd_ls(argv, options):
    ''' Usage: {cmd}
          List package names and their latst PyPI releases.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    tagsets = options.pkg_tagsets
    for pkg_name in sorted(tagsets.keys()):
      if pkg_name.startswith(MODULE_PREFIX):
        pypi_release = tagsets[pkg_name].get(TAG_PYPI_RELEASE)
        if pypi_release is not None:
          print(pkg_name, pypi_release)
    return 0

  @staticmethod
  def cmd_next(argv, options):
    ''' Usage: next pkg_names...
          Print package names and their next release tag.
    '''
    if not argv:
      raise GetoptError("missing package names")
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        print(pkg.name, pkg.next().version)

  @staticmethod
  def cmd_ok(argv, options):
    ''' Usage: {cmd} pkg_name [changset-hash]
          Print the commit log since the latest release.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      changeset_hash=argv.pop(0)
    else:
      changeset_hash=None
    if argv:
      raise GetoptError("extra arguments: %r", argv)
    pkg = options.modules[pkg_name]
    if changeset_hash is None:
      paths=pkg.paths()
      path_revs = options.vcs.file_revisions(pkg.paths())
      rev_latest = None
      for rev, node in sorted(path_revs.values()):
        if rev_latest is None or rev_latest < rev:
          changeset_hash=node
      if changeset_hash is None:
        error("no changeset revisions for paths: %r",pkg.paths())
        return 1
    print("%s: set ok_revision=%s"%(pkg_name,changeset_hash))
    pkg.set_tag('ok_revision', changeset_hash, msg="mark revision as ok")

  @staticmethod
  def cmd_package(argv, options):
    ''' Usage: package pkg_name [version]
          Export the package contents as a prepared package.
    '''
    if not argv:
      raise GetoptError("missing package name")
    vcs = options.vcs
    pkg_name = argv.pop(0)
    pkg = options.modules[pkg_name]
    if argv:
      version = argv.pop(0)
    else:
      version = pkg.latest.version
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    release = ReleaseTag(pkg_name, version)
    vcstag = release.vcstag
    checkout_dir = vcstag
    ModulePackageDir.fill(checkout_dir, pkg, vcs, vcstag, do_mkdir=True)
    print(checkout_dir)

  @staticmethod
  def cmd_pypi(argv, options):
    ''' Usage: {cmd} pkg_names...
          Push the named packages to PyPI.
    '''
    if not argv:
      raise GetoptError("missing package names")
    for pkg_name in argv:
      with Pfx(pkg_name):
        pkg = options.modules[pkg_name]
        vcs = options.vcs
        release = pkg.latest
        vcstag = release.vcstag
        pkg_dir = ModulePackageDir(pkg, vcs, vcstag)
        dirpath = pkg_dir.dirpath
        pkg.upload_dist(dirpath)
        pkg.latest_pypi_version = release.version

  @staticmethod
  def cmd_readme(argv, options):
    ''' Usage: {cmd} [-a] pkg_name
          Print out the package long_description.
          -a  Document all public class members (default is just
              __new__ and __init__ for the PyPI README.md file).
    '''
    all_class_names = False
    if argv and argv[0] == '-a':
      all_class_names = True
      argv.pop(0)
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    pkg = options.modules[pkg_name]
    docs = pkg.compute_doc(all_class_names=all_class_names)
    print(docs.long_description)

  @staticmethod
  def cmd_release(argv, options):
    ''' Usage: {cmd} pkg_name
          Issue a new release for the named package.
    '''
    if not argv:
      raise GetoptError("missing package name")
    pkg_name = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
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
      if options.force:
        warning("no commits since last release, making release anyway")
      else:
        error("no changes since last release, not making new release")
        return 1
    print("Changes since the last release:")
    for files, firstline in changes:
      print(" ", ' '.join(files) + ': ' + firstline)
    print()
    with pipefrom('readdottext', keep_stdin=True) as dotfp:
      release_message = dotfp.read().rstrip()
    if not release_message:
      error("empty release message, not making new release")
      return 1
    latest = pkg.latest
    next_release = pkg.latest.next() if pkg.latest else ReleaseTag.today(
        pkg.name
    )
    next_vcstag = next_release.vcstag
    if not ask("Confirm new release for %r as %r" % (pkg.name, next_vcstag)):
      error("aborting release at user request")
      return 1
    rel_dir = joinpath('release', next_vcstag)
    with Pfx("mkdir(%s)", rel_dir):
      os.mkdir(rel_dir)
    summary_filename = joinpath(rel_dir, 'SUMMARY.txt')
    with Pfx(summary_filename):
      with open(summary_filename, 'w') as sfp:
        print(release_message, file=sfp)
    changes_filename = joinpath(rel_dir, 'CHANGES.txt')
    with Pfx(changes_filename):
      with open(changes_filename, 'w') as cfp:
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
        '%s: bump __version__ to %s to avoid misleading value for future unreleased changes [IGNORE]'
        % (pkg.name, next_release.version + '-post'), versioned_filename
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
    ''' Basic release tag for today, without any `.`n suffix.
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

def runcmd(argv, **kw):
  ''' Run command.
  '''
  with Pfx("Popen(%r,...)", argv):
    P = Popen(argv, **kw)
    xit = P.wait()
    if xit != 0:
      raise ValueError("command failed, exit code %d: %r" % (xit, argv))

def cd_shcmd(wd, shcmd):
  ''' Run a command supplied as a sh(1) command string.
  '''
  qpkg_dir = shq(wd)
  xit = os.system("set -uex; cd %s; %s" % (qpkg_dir, shcmd))
  if xit != 0:
    raise ValueError("command failed, exit status %d: %r" % (xit, shcmd))

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
              line and line != 'Summary:' and not line.
              startswith('Release information for ')
          ),
          entry.strip().split('\n')
      )
  )
  if len(lines) > 1:
    # Multiple lines become a MarkDown bullet list.
    lines = ['* ' + line for line in lines]
  return '\n'.join(lines)

def ask(message, fin=None, fout=None):
  ''' Prompt with yes/no question, return true if response is "y" or "yes".
  '''
  if fin is None:
    fin = sys.stdin
  if fout is None:
    fout = sys.stderr
  print(message, end='? ', file=fout)
  fout.flush()
  response = fin.readline().rstrip().lower()
  return response in ('y', 'yes')

@contextmanager
def pipefrom(*argv, **kw):
  ''' Context manager returning the standard output file object of a command.
  '''
  P = cs.psutils.pipefrom(argv, trace=False, **kw)
  yield P.stdout
  if P.wait() != 0:
    pipecmd = ' '.join(argv)
    raise ValueError("%s: exit status %d" % (
        pipecmd,
        P.returncode,
    ))

class Modules(defaultdict):
  ''' An autopopulating dict of mod_name->Module.
  '''

  def __missing__(self, mod_name):
    assert isinstance(mod_name, str), "mod_name=%r" % (mod_name,)
    M = Module(mod_name, self.options)
    self[mod_name] = M
    return M

class Module(object):
  ''' Metadata about a Python module.
  '''

  @pfx_method(use_str=True)
  def __init__(self, name, options):
    self.name = name
    self._module = None
    self.options = options
    self._distinfo = None
    self._checking = False
    self._module_problems = None

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.name)

  @prop
  def modules(self):
    ''' The modules from `self.options`.
    '''
    return self.options.modules

  @prop
  def vcs(self):
    ''' The VCS from `self.options`.
    '''
    return self.options.vcs

  @prop
  @pfx_method(use_str=True)
  def module(self):
    ''' The Module for this package name.
    '''
    M = self._module
    if M is None:
      with Pfx("importlib.import_module(%r)", self.name):
        try:
          M = importlib.import_module(self.name)
        except (ImportError, SyntaxError) as e:
          error("import fails: %s", e)
          raise
      self._module = M
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
    M = self.module
    if M is None:
      warning("self.module is None")
      return False
    return '/site-packages/' in getattr(M, '__file__', '')

  @pfx_method(use_str=True)
  def isstdlib(self):
    ''' Test if this module exists in the stdlib.
    '''
    if self.ismine():
      return False
    if self.isthirdparty():
      return False
    return True

  @prop
  @cachedmethod
  @pfx_method(use_str=True)
  def package_name(self):
    ''' The name of the package containing this module,
        or `None` if this is not inside a package.
    '''
    M = self.module
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

  @prop
  @pfx_method(use_str=True)
  def package(self):
    name = self.package_name
    if name is None:
      raise ValueError("self.package_name is None")
    return self.modules[name]

  @prop
  def in_package(self):
    ''' Is this module part of a package?
    '''
    return self.package_name != self.name

  @prop
  def is_package(self):
    ''' Is this module a package?
    '''
    pkg_name = self.package_name
    return pkg_name is not None and pkg_name == self.name

  @property
  def pkg_tags(self):
    ''' The `TagSet` for this package.
    '''
    return self.options.pkg_tagsets[self.name]

  def save_pkg_tags(self):
    ''' Sync the package `Tag`s `TagFile`, return the pathname of the tag file.
    '''
    self.options.pkg_tagsets.save()
    return self.options.pkg_tagsets.filepath

  @tag_or_tag_value
  def set_tag(self, tag_name, value, *, msg):
    ''' Set a tag value and commit the modified tag file.
    '''
    self.pkg_tags.set(tag_name, value)
    pkg_tags_filename = self.save_pkg_tags()
    self.vcs.commit(
        f'{PKG_TAGS}: {self.name}: {msg+": " if msg else ""}set {tag_name}={value!r} [IGNORE]',
        PKG_TAGS
    )

  @cachedmethod
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

  def compute_doc(self, all_class_names=False):
    ''' Compute the components of the documentation.
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
        release_entry = clean_release_entry(release_entry)
        postamble_parts.append(
            f'*Release {release_tag.version}*:\n{release_entry}'
        )
    # split the module documentation after the opening paragraph
    full_doc = module_doc(
        self.module,
        method_names=None if all_class_names else ('__new__', '__init__')
    )
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

  @pfx_method
  def compute_distinfo(
      self,
      pypi_package_name=None,
      pypi_package_version=None,
  ):
    ''' Compute the distutils info mapping for this package.
    '''
    if pypi_package_name is None:
      pypi_package_name = self.name
    if pypi_package_version is None:
      pypi_package_version = self.latest.version

    # prepare core distinfo
    dinfo = dict(DISTINFO_DEFAULTS)
    docs = self.compute_doc(all_class_names=True)
    dinfo.update(
        description=docs.description, long_description=docs.long_description
    )
    dinfo.update(self.module.DISTINFO)

    # fill in default fields
    for field in ('author', 'author_email', 'long_description_content_type',
                  'package_dir'):
      with Pfx("%r", field):
        if field in dinfo:
          continue
        compute_field = {
            'author': lambda: os.environ['NAME'],
            'author_email': lambda: os.environ['EMAIL'],
            'include_package_data': lambda: True,
            'long_description_content_type': lambda: 'text/markdown',
            'package_dir': lambda: {
                '': PYLIBTOP
            },
        }[field]
        dinfo[field] = compute_field()

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
      if value is None:
        warning("no value for %r", kw)
      else:
        with Pfx(kw):
          if kw in dinfo:
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
        'url',
    ):
      if kw not in dinfo:
        warning('no %r in distinfo', kw)

    return dinfo

  @prop
  def basename(self):
    ''' The last component of the package name.
    '''
    return self.name.split('.')[-1]

  @prop
  def basepath(self):
    ''' The base path for this package.
    '''
    return os.sep.join([PYLIBTOP] + self.name.split('.'))

  @prop
  def toppath(self):
    ''' The top file of the package:
        basepath/__init__.py for packages
        and basepath.py for modules.
    '''
    basepath = self.basepath
    if isdirpath(basepath):
      return joinpath(basepath, '__init__.py')
    return basepath + '.py'

  @cachedmethod
  @pfx_method(use_str=True)
  def paths(self):
    ''' Yield the paths associated with this package.

        Note: this is based on the current checkout state instead
        of some revision because "hg archive" complains if globs
        match no paths, and aborts.
    '''
    pathlist = []
    basepath = self.basepath
    if isdirpath(basepath):
      for subpath, dirnames, filenames in os.walk(basepath):
        if not subpath.startswith(basepath):
          info("SKIP %s", subpath)
          continue
        for filename in sorted(filenames):
          if not (filename.endswith('.pyc') or filename.endswith('.o')):
            filepath = joinpath(subpath, filename)
            pathlist.append(filepath)
    else:
      base = self.basename
      updir = dirname(basepath)
      for filename in sorted(os.listdir(updir)):
        filepath = joinpath(updir, filename)
        if filename.startswith((base + '.', base + '_')):
          if (not (filename.endswith('.pyc') or filename.endswith('.o'))
              and isfilepath(filepath)):
            if isfilepath(filepath):
              pathlist.append(filepath)
            else:
              info("ignore %r, not a file", filepath)
    if not pathlist:
      raise ValueError("no paths for %s" % (self,))
    return pathlist

  @pfx_method
  def prepare_package(self, pkg_dir):
    ''' Prepare an existing package checkout as a package for upload or install.

        This writes the `'MANIFEST.in'`, `'README.md'` and `'setup.py'` files.
    '''
    distinfo = self.compute_distinfo()

    # write MANIFEST.in
    manifest_path = joinpath(pkg_dir, 'MANIFEST.in')
    with open(manifest_path, "w") as mf:
      # TODO: support extra files
      subpaths = self.paths()
      for subpath in subpaths:
        with Pfx(subpath):
          prefix, ext = splitext(subpath)
          if ext == '.md':
            _, ext2 = splitext(prefix)
            if len(ext2) == 2 and ext2[-1].isdigit():
              # md2man manual entry
              mdsrc = joinpath(pkg_dir, subpath)
              mddst = joinpath(pkg_dir, prefix)
              if pathexists(mddst):
                error("not converting because %r already exists", mddst)
              else:
                info("create %s", mddst)
                with Pfx(mddst):
                  with open(mddst, 'w') as mddstf:
                    runcmd(['md2man-roff', subpath], stdout=mddstf)
              mf.write('include ' + subpath + '\n')
              mf.write('include ' + prefix + '\n')
          elif ext == '.c':
            mf.write('include ' + subpath + '\n')
      # create README.md
      readme_path = joinpath(pkg_dir, 'README.md')
      with open(readme_path, 'w') as rf:
        print(
            distinfo.get('long_description', '') or distinfo['description'],
            file=rf
        )

    # final step: write setup.py with information gathered earlier
    setup_path = joinpath(pkg_dir, 'setup.py')
    with Pfx(setup_path):
      ok = True
      with open(setup_path, "w") as sf:
        out = partial(print, file=sf)
        out("#!/usr/bin/env python")
        ##out("from distutils.core import setup")
        out("from setuptools import setup")
        out("setup(")
        # mandatory fields, in preferred order
        written = set()
        for kw in (
            'name',
            'author',
            'author_email',
            'version',
            'url',
            'description',
            'long_description',
        ):
          try:
            kv = distinfo[kw]
          except KeyError:
            warning("missing distinfo[%r]", kw)
            ok = False
          else:
            if kw in ('description', 'long_description') and isinstance(kv,
                                                                        str):
              out("  %s =" % (kw,))
              out("   ", pformat(kv).replace('\n', '    \n') + ',')
            else:
              out("  %s = %r," % (kw, distinfo[kw]))
            written.add(kw)
        for kw, kv in sorted(distinfo.items()):
          if kw not in written:
            out("  %s = %r," % (kw, kv))
        out(")")
      if not ok:
        raise ValueError("could not construct valid setup.py file")

  def reldistfiles(self, pkg_dir):
    return [
        relpath(fullpath, pkg_dir)
        for fullpath in glob(joinpath(pkg_dir, 'dist/*'))
    ]

  @pfx_method
  def prepare_dist(self, pkg_dir):
    ''' Run "setup.py check sdist", making files in dist/.
    '''
    cd_shcmd(pkg_dir, shqv(['python3', 'setup.py', 'check']))
    cd_shcmd(pkg_dir, shqv(['python3', 'setup.py', 'sdist']))
    distfiles = self.reldistfiles(pkg_dir)
    cd_shcmd(pkg_dir, shqv(['twine', 'check'] + distfiles))

  @pfx_method
  def upload_dist(self, pkg_dir):
    ''' Upload the package to PyPI using twine.
    '''
    distfiles = self.reldistfiles(pkg_dir)
    cd_shcmd(pkg_dir, shqv(['twine', 'upload'] + distfiles))

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
    latest_release_line = 'Release information for ' + self.latest.vcstag + '.'
    return (
        ([filename
          for filename in files
          if filename in paths], firstline)
        for files, firstline in self.vcs.log_since(vcstag, paths)
        if (
            ignored or
            ('IGNORE' not in firstline and firstline != latest_release_line)
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
      with open(toppath) as tf:
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
        with open(toppath, 'w') as tf:
          for line in lines:
            tf.write(line)
    return toppath

  @prop
  def DISTINFO(self):
    ''' The `DISTINFO` from `self.module`.
    '''
    D = self._distinfo
    if D is None:
      with Pfx("%s.DISTINFO", self.name):
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
        self._distinfo = D
    return D

  @prop
  def requires(self):
    ''' Return other nonstdlib packages required by this module.
    '''
    return self.DISTINFO.get('install_requires', [])

  @pfx_method(use_str=True)
  def problems(self):
    ''' Sanity check of this module.

        This is a list of problems,
        each of which is either a string
        or a mapping of required package name to its problems.
    '''
    problems = self._module_problems
    if problems is not None:
      return problems
    problems = self._module_problems = []
    latest_ok_rev = self.pkg_tags.get('ok_revision')
    # see if this package has been marked "ok" as of a particular revision
    unreleased_logs=None
    if latest_ok_rev:
      post_ok_commits=list(self.log_since(vcstag=latest_ok_rev))
      if not post_ok_commits:
        return problems
      unreleased_logs=post_ok_commits
    subproblems = defaultdict(list)
    pkg_name = self.package_name
    if pkg_name is None:
      pkg_prefix = None
    else:
      pkg_prefix = pkg_name + '.'
    M = self.module
    import_names = []
    for import_name in direct_imports(M.__file__, self.name):
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
          problems.append(
              "DISTINFO[install_requires=%r] != direct_imports=%r" %
              (distinfo_requires, sorted(import_names))
          )
        for import_name in import_names:
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

  @prop
  @cachedmethod
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
    ''' Generator yielding directly imported Modules.
    '''
    for name in sorted(self.imported_names):
      if name.startswith(prefix) and name != self.name:
        yield self.modules[name]

class ModulePackageDir(SingletonMixin):
  ''' A singleton class for module package distributions.
  '''

  @classmethod
  def _singleton_key(cls, pkg, vcs, revision):
    return pkg.name, revision

  def __init__(self, pkg, vcs, revision, persist=False):
    # upgrade persist setting if requested
    self.persist = getattr(self, 'persist', False) or persist
    if hasattr(self, 'pkg'):
      return
    self.pkg = pkg
    self.vcs = vcs
    self.revision = revision
    self._setup()

  def __del__(self):
    if self.pkg_dir and not self.persist:
      self.pkg_dir.cleanup()
      self.pkg_dir = None

  @pfx_method
  def _setup(self):
    pkg = self.pkg
    vcs = self.vcs
    vcs_revision = self.revision
    pkg_dir = self.pkg_dir = TemporaryDirectory(prefix=vcs_revision + '-')
    dirpath = self.dirpath = pkg_dir.name
    self.fill(dirpath, pkg, vcs, vcs_revision)

  @staticmethod
  def fill(dirpath, pkg, vcs, vcs_revision, *, do_mkdir=False, bare=False):
    with Pfx(dirpath):
      if do_mkdir:
        with Pfx("mkdir(%r)", dirpath):
          os.mkdir(dirpath, 0o777)
      hg_argv = ['archive', '-r', vcs_revision]
      hg_argv.extend(vcs.hg_include(pkg.paths()))
      hg_argv.extend(['--', dirpath])
      vcs.hg_cmd(*hg_argv)
      os.system("find %r -type f -print" % (dirpath,))
      if not bare:
        pkg.prepare_package(dirpath)
        pkg.prepare_dist(dirpath)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
