#!/usr/bin/python
#

''' Default distutils info for all packages in cs.* and utility
    functions to prep and release packages to PyPI.
    - Cameron Simpson <cs@cskk.id.au> 01jan2015
'''

from __future__ import print_function
from collections import namedtuple
from functools import partial
from getopt import getopt, GetoptError
from glob import glob
import importlib
import os
import os.path
from os.path import (
    basename, exists as pathexists, isdir as pathisdir, join as joinpath,
    splitext
)
from pprint import pprint
from subprocess import Popen, PIPE
import shutil
import sys
from tempfile import mkdtemp
from types import SimpleNamespace as NS
from cs.logutils import setup_logging, info, warning, error
from cs.pfx import Pfx
from cs.sh import quotestr as shq, quote as shqv

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
    "License":
    "OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
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
  ''' Main command line programme.
  '''
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, URL_PYPI_TEST, URL_PYPI_PROD)
  setup_logging(cmd)

  badopts = False

  package_name = None
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
      package_name, _ = package_name.split('@', 1)
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

  PKG = PyPI_Package(
      pypi_url,
      package_name,
      pypi_version,
      pypi_package_name=pypi_package_name
  )

  xit = 0

  with Pfx(op):
    if op == 'check':
      PKG.check()
    elif op == 'distinfo':
      isatty = os.isatty(sys.stdout.fileno())
      dinfo = PKG.distinfo
      if argv:
        for arg in argv:
          if len(argv) > 1:
            print(arg)
          try:
            value = dinfo[arg]
          except KeyError:
            print("None")
          else:
            if isatty:
              pprint(value)
            else:
              print(value)
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

def runcmd(argv, **kw):
  ''' Run command.
  '''
  P = Popen(argv, **kw)
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

def pathify(package_name):
  ''' Translate foo.bar.zot into foo/bar/zot.
  '''
  return package_name.replace('.', os.path.sep)

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

class PackageInstance(NS):

  def __init__(self, package, version, vcs=None):
    ##if vcs is None:
    ##  vcs = VCS_Hg()
    self.package = package
    self.version = version
    ##self.vcs = vcs

  @property
  def name(self):
    return self.package.name

  @property
  def vcs_tag(self):
    ''' The tag used in the VCS for this version of the package.
    '''
    return self.name + '-' + self.version

  def copyin(self, dstdir):
    ''' Write the contents of this release into `dstdir`.
        Return the subpaths of `dstdir` created.
    '''
    included = []
    hgargv = [
        'set-x',
        'hg',
        'archive',
        '-r',
        '"%s"' % self.vcs_tag,
    ]
    first = True
    package_parts = self.package_name.split('.')
    while package_parts:
      superpackage_name = '.'.join(package_parts)
      base = self.package_base(superpackage_name)
      if first:
        # collect entire package contents
        for subpath in self.package_paths(superpackage_name, self.libdir):
          incpath = joinpath(self.libdir, subpath)
          hgargv.extend(['-I', incpath])
          included.append(incpath)
      else:
        # just collecting required __init__.py files
        incpath = joinpath(base, '__init__.py')
        hgargv.extend(['-I', incpath])
        included.append(incpath)
      package_parts.pop()
      first = False
    hgargv.append(dstdir)
    runcmd(hgargv)
    return included

  def _prep_distinfo(self):
    ''' Compute the distutils info for this package.
    '''
    dinfo = dict(self.defaults)
    module = importlib.import_module(self.package_name)
    dinfo.update(module.DISTINFO)

    if ('long_description' in dinfo
        and 'long_description_content_type' not in dinfo):
      dinfo['long_description_content_type'] = 'text/markdown'

    if 'include_package_data' not in dinfo:
      dinfo['include_package_data'] = True

    dinfo['package_dir'] = {'': self.libdir}

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

    ispkg = self.is_package(self.package_name)
    if ispkg:
      # stash the package in a top level directory of that name
      ## dinfo['package_dir'] = {package_name: package_name}
      dinfo['packages'] = [self.package_name]
    else:
      dinfo['py_modules'] = [self.package_name]

    for kw, value in (
        ('license', license_type),
        ('name', self.pypi_package_name),
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
        error('no %r in distinfo', kw)

  def make_package(self, pkg_dir=None):
    ''' Prepare package contents in the directory `pkg_dir`, return `pkg_dir`.

        If `pkg_dir` is not specified, create a temporary directory.
    '''
    if pkg_dir is None:
      pkg_dir = mkdtemp(prefix='pkg--' + self.pypi_package_name + '--', dir='.')

    distinfo = self.distinfo

    manifest_path = joinpath(pkg_dir, 'MANIFEST.in')
    with open(manifest_path, "w") as mfp:
      # TODO: support extra files
      subpaths = self.copyin(pkg_dir)
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
                    runcmd(['md2man-roff', mdsrc], stdout=mddstf)
              mfp.write('include ' + subpath + '\n')
              mfp.write('include ' + prefix + '\n')
          elif ext == '.c':
            mfp.write('include ' + subpath + '\n')
      # create README.md
      readme_path = joinpath(pkg_dir, 'README.md')
      with open(readme_path, 'w') as fp:
        print(distinfo['description'], file=fp)
        print('', file=fp)
        long_desc = distinfo.get('long_description', '')
        if long_desc:
          print(file=fp)
          print(long_desc, file=fp)

    # final step: write setup.py with information gathered earlier
    self.write_setup(joinpath(pkg_dir, 'setup.py'))

    return pkg_dir

  def checkout(self):
    ''' Return a fresh checkout of the package.
    '''
    return PyPI_PackageCheckout(self)

  def upload(self):
    ''' Upload: make a checkout, prepare the distribution, twine upload.
    '''
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
        for kw in (
            'name',
            'description',
            'author',
            'author_email',
            'version',
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
    ''' Generator to yield the file paths from a package
        relative to the `libdir` subdirectory.
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
      # packages - all .py and .md files in directory
      # warning about unexpected other files
      libprefix = libdir + os.path.sep
      for dirpath, _, filenames in os.walk(joinpath(libdir,
                                                           package_subpath)):
        for filename in filenames:
          if filename.startswith('.'):
            continue
          _, ext = splitext(filename)
          if ext == '.pyc':
            continue
          if ext in ('.py', '.md', '.c'):
            yield joinpath(dirpath[len(libprefix):], filename)
            continue
          warning("skipping %s", joinpath(dirpath, filename))

class PyPI_PackageCheckout(NS):
  ''' Facilities available with a checkout of a package.
  '''

  def __init__(self, pkg_instance):
    self.package_instance = pkg_instance

  def __enter__(self):
    ''' Prep the package in a temporary directory, return self.
    '''
    if hasattr(self, 'pkg_dir'):
      raise RuntimeError("already using .pkg_dir = %r" % (self.pkg_dir,))
    self.pkg_dir = self.package_instance.make_package()
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
    ''' The PyPI URL from the parent package.
    '''
    return self.package_instance.pypi_url

  def inpkg(self, shcmd):
    ''' Run a command supplied as a sh(1) command string.
    '''
    qpkg_dir = shq(self.pkg_dir)
    xit = os.system("set -uex; cd %s; %s" % (qpkg_dir, shcmd))
    if xit != 0:
      raise ValueError("command failed, exit status %d: %r" % (xit, shcmd))

  __call__ = inpkg

  def inpkg_argv(self, argv):
    ''' Run a command supplied as an argument list.
    '''
    shcmd = ' '.join(shqv(argv))
    return self.inpkg(shcmd)

  def setup_py(self, *argv):
    ''' Run a setup.py incantation.
    '''
    return self.inpkg_argv(['python3', 'setup.py'] + list(argv))

  def prepare_dist(self):
    ''' Run "setup.py check sdist".
    '''
    self.setup_py('check', 'sdist')

  def upload(self):
    ''' Upload the package to PyPI using twine.
    '''
    upload_files = [
        joinpath('dist', basename(distpath))
        for distpath in glob(joinpath(self.pkg_dir, 'dist/*'))
    ]
    return self.inpkg_argv(['twine', 'upload'] + upload_files)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
