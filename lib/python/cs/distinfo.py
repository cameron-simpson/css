#!/usr/bin/python
#
# Default distutils info for all packages in cs.* and utility
# functions to prep and release packages to PyPI.
#   - Cameron Simpson <cs@cskk.id.au> 01jan2015
#

from __future__ import print_function
from functools import partial
from getopt import getopt, GetoptError
from glob import glob
import importlib
from inspect import cleandoc, getargspec, getcomments, isfunction, isclass
import os
import os.path
from os.path import basename, exists as pathexists, isdir as pathisdir, join as joinpath
from pprint import pprint
from subprocess import Popen, PIPE
import shutil
import sys
from tempfile import mkdtemp
from textwrap import dedent
from cs.logutils import setup_logging, info, warning, error
from cs.obj import O
from cs.pfx import Pfx
import cs.sh

URL_PYPI_PROD = 'https://pypi.python.org/pypi'
URL_PYPI_TEST = 'https://test.pypi.org/legacy/'

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/'

# local directory where the files live
LIBDIR = 'lib/python'

DISTINFO_CLASSIFICATION = {
    "Programming Language": "Python",
    "Development Status": "4 - Beta",
    "Intended Audience": "Developers",
    "Operating System": "OS Independent",
    "Topic": "Software Development :: Libraries :: Python Modules",
    "License": "OSI Approved :: GNU General Public License v3 (GPLv3)",
}


USAGE = '''Usage: %s [-n pypi-pkgname] [-v pypi_version] pkgname[@tag] op [op-args...]
  -n pypi-pkgname
        Name of package in PyPI. Default the same as the local package.
  -r pypi_repo_url
        Use the specified PyPI repository URL.
        Default: %s, or from the environment variable $PYPI_URL.
        Official site: %s
  -v pypi-version
        Version number for PyPI. Default from the chosen pkgname release.
  pkgname
        Python package/module name.
  @tag  Use the specified VCS tag. Default: the last release tag for pkgname.
  Operations:
    check       Run setup.py check on the resulting package.
    distinfo    Recite the distinfo map for the package.
    register    Register/update the package description and version.
    upload      Upload the package source distribution.'''

def main(argv):
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, URL_PYPI_TEST, URL_PYPI_PROD)
  setup_logging(cmd)

  badopts = False

  package_name = None
  vcs_tag = None
  pypi_package_name = None
  pypi_version = None
  pypi_url = None

  try:
    opts, argv = getopt(argv, 'n:r:v:')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
  else:
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-n':
          pypi_package_name = val
        elif opt == '-r':
          pypi_url = val
        elif opt == '-v':
          pypi_version = val
        else:
          warning('unhandled option')
          badopts = True

  if not argv:
    warning("missing pkgname")
    badopts = True
  else:
    package_name = argv.pop(0)
    try:
      package_name, vcs_tag = package_name.split('@', 1)
    except ValueError:
      pass

    if not argv:
      warning("missing op")
      badopts = True
    else:
      op = argv.pop(0)
      with Pfx(op):
        if op in ("check", "register", "upload"):
          if argv:
            warning("extra arguments: %s", ' '.join(argv))
            badopts = True
        elif op in ("distinfo",):
          pass
        else:
          warning("unrecognised op")
          badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  if pypi_url is None:
    pypi_url = os.environ.get('PYPI_URL', URL_PYPI_TEST)

  if pypi_package_name is None:
    pypi_package_name = package_name

  PKG = PyPI_Package(pypi_url, package_name, pypi_version,
                     pypi_package_name=pypi_package_name)

  xit = 0

  with Pfx(op):
    if op == 'check':
      PKG.check()
    elif op == 'distinfo':
      dinfo = PKG.distinfo
      if argv:
        for arg in argv:
          print(arg)
          try:
            value = dinfo[arg]
          except KeyError:
            print("None")
          else:
            pprint(value)
      else:
        pprint(dinfo)
    elif op == 'register':
      PKG.register()
    elif op == 'upload':
      PKG.upload()
    else:
      raise RuntimeError("unimplemented")

  return xit

def pathify(package_name):
  ''' Translate foo.bar.zot into foo/bar/zot.
  '''
  return package_name.replace('.', os.path.sep)

def needdir(dirpath):
  ''' Create the directory `dirpath` if missing.
  '''
  if not pathisdir(dirpath):
    warning("makedirs(%r)", dirpath)
    os.makedirs(dirpath)

def runcmd(argv):
  ''' Run command.
  '''
  P = Popen(argv)
  xit = P.wait()
  if xit != 0:
    raise ValueError("command failed, exit code %d: %r" % (xit, argv))

def cmdstdout(argv):
  ''' Run command, return output in string.
  '''
  P = Popen(argv, stdout=PIPE)
  output = P.stdout.read()
  if sys.version_info[0] >= 3:
    output = output.decode('utf-8')
  xit = P.wait()
  if xit != 0:
    raise ValueError("command failed, exit code %d: %r" % (xit, argv))
  return output

def test_is_package(libdir, package_name):
  ''' Test whether `package_name` is a package (a directory with a __init__.py file).
      Do some sanity checks and complain loudly.
  '''
  package_subpath = pathify(package_name)
  package_dir = joinpath(libdir, package_subpath)
  package_py = package_dir + '.py'
  package_init_path = joinpath(package_dir, '__init__.py')
  is_pkg = pathisdir(package_dir)
  if is_pkg:
    if pathexists(package_py):
      error("both %s/ and %s exist", package_dir, package_py)
      is_pkg = False
    if not pathexists(package_init_path):
      error("%s/ exists, but not %s", package_dir, package_init_path)
      is_pkg = False
  else:
    if not pathexists(package_py):
      error("neither %s/ nor %s exist", package_dir, package_py)
  return is_pkg

class Package(O):

  def __init__(self, package_name):
    super().__init__(name=package_name)

  @property
  def hg_tag(self):
    return self.name + '-' + self.version

class PyPI_Package(O):
  ''' Operations for a package at PyPI.
  '''

  def __init__(self, pypi_url,
    package_name, package_version,
    pypi_package_name=None, pypi_package_version=None,
    defaults=None,
  ):
    ''' Initialise: save package_name and its name in PyPI.
    '''
    if defaults is None:
      defaults = {}
    if 'author' not in defaults:
      try:
        author_name = os.environ['NAME']
      except KeyError:
        pass
      else:
        defaults['author'] = author_name
    if 'author_email' not in defaults:
      try:
        author_email = os.environ['EMAIL']
      except KeyError:
        pass
      else:
        defaults['author_email'] = author_email
    self.pypi_url = pypi_url
    self.package = Package(package_name)
    self.package.version = package_version
    self._pypi_package_name = pypi_package_name
    self._pypi_package_version = pypi_package_version
    self.defaults = defaults
    self.libdir = LIBDIR
    self._prep_distinfo()

  @property
  def package_name(self):
    return self.package.name

  @property
  def pypi_package_name(self):
    name = self._pypi_package_name
    if name is None:
      name = self.package_name
    return name

  @property
  def pypi_package_version(self):
    version = self._pypi_package_version
    if version is None:
      version = self.package.version
    return version

  @property
  def hg_tag(self):
    return self.package.hg_tag

  def _prep_distinfo(self):
    ''' Property containing the distutils info for this package.
    '''
    global DISTINFO_DEFAULTS
    global DISTINFO_CLASSIFICATION

    dinfo = dict(self.defaults)
    M = importlib.import_module(self.package_name)
    dinfo.update(M.DISTINFO)

    doc = M.__doc__
    if doc:
      doc = doc.strip()
    else:
      doc = ''
    try:
      doc_line1, doc_tail = doc.split('\n', 1)
    except ValueError:
      doc_line1 = doc
      doc_tail = ''
    else:
      doc_tail = doc_tail.lstrip()

    for Mname in sorted(dir(M), key-lambda s: s.lower()):
      if Mname == 'DISTINFO':
        continue
      if Mname.startswith('_'):
        continue
      o = getattr(M, Mname, None)
      if not isclass(o) and not isfunction(o):
        continue
      odoc = o.__doc__
      if odoc is None:
        continue
      odoc = cleandoc(odoc)
      if isfunction(o):
        args, varargs, keywords, defaults = getargspec(o)
        if defaults is None:
          defaults = ()
        arg_desc = ''
        nreq = len(args) - len(defaults)
        for i, arg in enumerate(args):
          if arg_desc:
            arg_desc += ', '
          arg_desc += arg
          if i >= nreq:
            arg_desc += '=' + repr(defaults[i-nreq])
        if varargs:
          if arg_desc:
            arg_desc += ', '
          arg_desc += '*' + varargs
        if keywords:
          if arg_desc:
            arg_desc += ', '
          arg_desc += '**' + keywords
        doc_tail += f'\n\n## Function `{Mname}({arg_desc})`\n\n{odoc}'
      elif isclass(o):
        doc_tail += f'\n\n## Class `{Mname}`\n\n{odoc}'

    # fill in some missing info if it can be inferred
    for field in 'description', 'long_description':
      if field in dinfo:
        continue
      if field == 'description':
        if doc_line1:
          dinfo[field] = doc_line1
      elif field == 'long_description':
        if doc_tail:
          dinfo[field] = doc_tail
        if 'long_description_content_type' not in dinfo:
          dinfo['long_description_content_type'] = 'text/markdown'

    dinfo['package_dir'] = {'': self.libdir}

    classifiers = dinfo['classifiers']
    for classifier_topic, classifier_subsection in DISTINFO_CLASSIFICATION.items():
      classifier_prefix = classifier_topic + " ::"
      classifier_value = classifier_topic + " :: " + classifier_subsection
      if not any(classifier.startswith(classifier_prefix)
                 for classifier in classifiers
                 ):
        dinfo['classifiers'].append(classifier_value)

    ispkg = self.is_package(self.package_name)
    if ispkg:
      # stash the package in a top level directory of that name
      ## dinfo['package_dir'] = {package_name: package_name}
      dinfo['packages'] = [self.package_name]
    else:
      dinfo['py_modules'] = [self.package_name]

    for kw, value in (('name', self.pypi_package_name),
                      ('version', self.pypi_package_version),
                      ):
      if value is None:
        warning("_prep: no value for %r", kw)
      else:
        with Pfx(kw):
          if kw in dinfo:
            if dinfo[kw] != value:
              info("publishing %s instead of %s", value, dinfo[kw])
          else:
            dinfo[kw] = value

    self.distinfo = dinfo
    for kw in ('name',
               'description', 'author', 'author_email', 'version',
               'url',
              ):
      if kw not in dinfo:
        error('no %r in distinfo', kw)

  def pkg_rpath(self, package_name=None, prefix_dir=None, up=False):
    ''' Return a path based on a `package_name` (default self.package_name).
        `prefix_dir`: if supplied, prefixed to the returned relative path.
        `up`: if true, discard the last component of the package name before
        computing the path.
    '''
    if package_name is None:
      package_name = self.package_name
    package_paths = package_name.split('.')
    if up:
      package_paths = package_paths[:-1]
    rpath = joinpath(*package_paths)
    if prefix_dir:
      rpath = joinpath(prefix_dir, rpath)
    return rpath

  def make_package(self, pkg_dir=None):
    ''' Prepare package contents in the directory `pkg_dir`, return `pkg_dir`.
        If `pkg_dir` is not supplied, create a temporary directory.
    '''
    if pkg_dir is None:
      pkg_dir = mkdtemp(prefix='pkg-' + self.pypi_package_name + '-', dir='.')

    distinfo = self.distinfo

    manifest_path = joinpath(pkg_dir, 'MANIFEST.in')
    with open(manifest_path, "w") as mfp:
       # TODO: support extra files
      self.copyin(pkg_dir)

      # create README.rst
      with open('README.rst', 'w') as fp:
        print(distinfo['description'], file=fp)
        print('=' * len(distinfo['description']), file=fp)
        long_desc = distinfo.get('long_description', '')
        if long_desc:
          print(file=fp)
          print(long_desc, file=fp)
      mfp.write('include README.rst\n')

    # final step: write setup.py with information gathered earlier
    self.write_setup(joinpath(pkg_dir, 'setup.py'))

    return pkg_dir

  def checkout(self):
    return PyPI_PackageCheckout(self)

  def upload(self):
    with self.checkout() as pkg_co:
      pkg_co.prepare_dist()
      pkg_co.upload()

  def write_setup(self, setup_path):
    ''' Transcribe a setup.py file.
    '''
    with Pfx("write_setup(%r)", setup_path):
      ok = True
      with open(setup_path, "w") as setup:
        distinfo = self.distinfo
        out = partial(print, file=setup)
        out("#!/usr/bin/env python")
        ##out("from distutils.core import setup")
        out("from setuptools import setup")
        out("setup(")
        # mandatory fields, in preferred order
        written = set()
        for kw in ('name',
                   'description', 'author', 'author_email', 'version',
                   'url',
                   ):
          if kw in distinfo:
            out("  %s = %r," % (kw, distinfo[kw]))
            written.add(kw)
          else:
            warning("missing distinfo[%r]", kw)
            ok = False
        for kw in sorted(distinfo.keys()):
          if kw not in written:
            out("  %s = %r," % (kw, distinfo[kw]))
        out(")")
      if not ok:
        raise ValueError("could not construct valid setup.py file")

  def is_package(self, package_name):
    ''' Test if the `package_name` is a package or just a file.
    '''
    return test_is_package(self.libdir, package_name)

  def package_base(self, package_name):
    ''' Return the base of `package_name`, a relative directory or filename.
    '''
    package_subpath = pathify(package_name)
    base = joinpath(self.libdir, package_subpath)
    if not self.is_package(package_name):
      base += '.py'
    return base

  def package_paths(self, package_name, libdir):
    ''' Generator to yield the file paths from a package relative to the `libdir` subdirectory.
    '''
    package_subpath = pathify(package_name)
    if not self.is_package(package_name):
      # simple case - module file and its tests
      yield package_subpath + '.py'
      test_subpath = package_subpath + '_tests.py'
      test_path = joinpath(libdir, test_subpath)
      if pathexists(test_path):
        yield test_subpath
    else:
      # packages - all .py files in directory
      # warning about unexpected other files
      libprefix = libdir + os.path.sep
      for dirpath, dirnames, filenames in os.walk(joinpath(libdir, package_subpath)):
        for filename in filenames:
          if filename.startswith('.'):
            continue
          if filename.endswith('.pyc'):
            continue
          if filename.endswith('.py'):
            yield joinpath(dirpath[len(libprefix):], filename)
            continue
          warning("skipping %s", joinpath(dirpath, filename))

  def copyin(self, dstdir):
    ''' Write the contents of the tagged release into `dstdir`.
    '''
    hgargv = ['set-x', 'hg',
              'archive',
              '-r', '"%s"' % self.hg_tag,
              ]
    first = True
    package_parts = self.package_name.split('.')
    while package_parts:
      superpackage_name = '.'.join(package_parts)
      base = self.package_base(superpackage_name)
      if first:
        # collect entire package contents
        for subpath in self.package_paths(superpackage_name, self.libdir):
          hgargv.extend(['-I', joinpath(self.libdir, subpath)])
      else:
        # just collecting required __init__.py files
        hgargv.extend(['-I', joinpath(base, '__init__.py')])
      package_parts.pop()
      first = False
    hgargv.append(dstdir)
    runcmd(hgargv)

class PyPI_PackageCheckout(O):
  ''' Facilities available with a checkout of a package.
  '''

  def __init__(self, pkg):
    self.package = pkg

  def __enter__(self):
    ''' Prep the package in a temporary directory, return self.
    '''
    if hasattr(self, 'pkg_dir'):
      raise RuntimeError("already using .pkg_dir = %r" % (self.pkg_dir,))
    self.pkg_dir = self.package.make_package()
    self.inpkg("find . -type f | sort | xxargs ls -ld -- ")
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    ''' Remove the temporary directory.
    '''
    shutil.rmtree(self.pkg_dir)
    del self.pkg_dir
    return False

  @property
  def pypi_url(self):
    return self.package.pypi_url

  def inpkg(self, shcmd):
    ''' Run a command supplied as a sh(1) command string.
    '''
    qpkg_dir = cs.sh.quotestr(self.pkg_dir)
    xit = os.system("set -uex; cd %s; %s" % (qpkg_dir, shcmd))
    if xit != 0:
      raise ValueError("command failed, exit status %d: %r" % (xit, shcmd))

  __call__ = inpkg

  def inpkg_argv(self, argv):
    ''' Run a command supplied as an argument list.
    '''
    shcmd = ' '.join(cs.sh.quote(argv))
    return self.inpkg(shcmd)

  def setup_py(self, *argv):
    ''' Run a setup.py incantation.
    '''
    return self.inpkg_argv(['python3', 'setup.py'] + list(argv))

  def prepare_dist(self):
    self.setup_py('check', 'sdist')

  def upload(self):
    upload_files = [
        joinpath('dist', basename(distpath))
        for distpath in glob(joinpath(self.pkg_dir, 'dist/*'))
    ]
    return self.inpkg_argv(['twine', 'upload'] + upload_files)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
