#!/usr/bin/python
#
# Default distutils info for all packages in cs.* and utility
# functions to prep and release packages to PyPI.
#   - Cameron Simpson <cs@zip.com.au> 01jan2015
#
from __future__ import print_function
import sys
import os
import os.path
from functools import partial
from getopt import getopt, GetoptError
from subprocess import Popen, PIPE
import shutil
from tempfile import mkdtemp
from threading import RLock
from cs.logutils import setup_logging, Pfx, info, warning, error, X
from cs.threads import locked_property
from cs.py.modules import import_module_name
from cs.obj import O

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/'

# local directory where the files live
LIBDIR = 'lib/python'

# defaults for packages without their own specifics
DISTINFO = {
    'author': "Cameron Simpson",
    'author_email': "cs@zip.com.au",
    'url': 'https://bitbucket.org/cameron_simpson/css/commits/all',
    'classifiers': [
        "Programming Language :: Python",
        "Development Status :: 4 - Beta",
        "Environment :: Other Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        ],
}


USAGE = '''Usage: %s [-n pypi-pkg-name] [-v pypi_version] pkg-name'''

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)

  badopts = False

  pypi_package_name = None
  pypi_version = None

  try:
    opts, argv = getopt(argv, 'n:v:')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
  else:
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-n':
          pypi_package_name = val
        elif opt == '-v':
          pypi_version = val
        else:
          warning('unhandled option')
          badopts = True

  if not argv:
    warning("missing pkg-name")
    badopts = True
  else:
    package_name = argv.pop(0)
    if argv:
      warning("extra arguments after pkg-name: %s", ' '.join(argv))
      badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  if pypi_package_name is None:
    pypi_package_name = package_name

  PKG = PyPI_Package(package_name, pypi_package_name = pypi_package_name)

  xit = 0

  with PKG as pkg_dir:
    os.system("ls -la %s" % (pkg_dir,))

  return xit

def pathify(package_name):
  ''' Translate foo.bar.zot into foo/bar/zot.
  '''
  return package_name.replace('.', os.path.sep)

def needdir(dirpath):
  ''' Create the directory `dirpath` if missing.
  '''
  if not os.path.isdir(dirpath):
    warning("makedirs(%r)", dirpath)
    os.makedirs(dirpath)

def runcmd(argv):
  ''' Run command.
  '''
  X("runcmd: argv = %r", argv)
  P = Popen(argv)
  xit = P.wait()
  if xit != 0:
    raise ValueError("command failed, exit code %d: %r" % (xit, argv))

def cmdstdout(argv):
  ''' Run command, return output in string.
  '''
  X("cmdstdout: argv = %r", argv)
  P = Popen(argv, stdout=PIPE)
  output = P.stdout.read()
  xit = P.wait()
  if xit != 0:
    raise ValueError("command failed, exit code %d: %r" % (xit, argv))
  return output

def test_is_package(libdir, package_name):
  ''' Test whether `package_name` is a package (a directory with a __init__.py file).
      Do some sanity checks and complain loudly.
  '''
  package_subpath = pathify(package_name)
  package_dir = os.path.join(libdir, package_subpath)
  package_py = package_dir + '.py'
  package_init_path = os.path.join(package_dir, '__init__.py')
  is_pkg = os.path.isdir(package_dir)
  if is_pkg:
    if os.path.exists(package_py):
      error("both %s/ and %s exist", package_dir, package_py)
      is_pkg = False
    if not os.path.exists(package_init_path):
      error("%s/ exists, but not %s", package_dir, package_init_path)
      is_pkg = False
  else:
    if not os.path.exists(package_py):
      error("neither %s/ nor %s exist", package_dir, package_py)
  return is_pkg

class PyPI_Package(O):
  ''' Class for creating and administering cs.* packages for PyPI.
  '''

  def __init__(self, package_name, pypi_package_name = None):
    ''' Iinitialise: save package_name and its name in PyPI.
    '''
    if pypi_package_name is None:
      pypi_package_name = package_name
    self.package_name = package_name
    self.pypi_package_name = pypi_package_name
    self.libdir = LIBDIR
    self._lock = RLock()
    self._prep_distinfo()

  def __enter__(self):
    ''' Prep the package in a temporary directory, return the directory.
    '''
    if hasattr(self, '_working_dir'):
      raise RuntimeError("already using ._working_dir = %r" % (self._working_dir,))
    self._working_dir = self.make_package()
    return self._working_dir

  def __exit__(self, exc_type, exc_value, traceback):
    ''' Remove the temporary directory.
    '''
    shutil.rmtree(self._working_dir)
    del self._working_dir
    return False

  @locked_property
  def version(self):
    return cmdstdout(['cs-release', '-p', self.package_name, 'last']).rstrip()

  @property
  def pypi_version(self):
    return self.version

  @property
  def hg_tag(self):
    return self.package_name + '-' + self.version

  def _prep_distinfo(self):
    ''' Property containing the distutils infor for this package.
    '''
    info = dict(import_module_name(self.package_name, 'distinfo'))

    info['package_dir'] = {'': self.libdir}

    for kw, value in DISTINFO.items():
      if value is not None:
        with Pfx(kw):
          if kw not in info:
            info[kw] = value

    ispkg = self.is_package(self.package_name)
    if ispkg:
      ## # stash the package in a top level directory of that name
      ## info['package_dir'] = {package_name: package_name}
      info['packages'] = [self.package_name]
    else:
      info['py_modules'] = [self.package_name]

    for kw, value in ( ('name', self.pypi_package_name),
                       ('version', self.pypi_version),
                     ):
      if value is not None:
        with Pfx(kw):
          if kw in info:
            if info[kw] != value:
              info("publishing %s instead of %s", value, info[kw])
          else:
            info[kw] = value

    self.distinfo = info

  def make_package(self, pkg_dir=None):
    ''' Prepare package contents in the directory `pkg_dir`, return `pkg_dir`.
        If `pkg_dir` is not supplied, create a temporary directory.
    '''
    if pkg_dir is None:
      pkg_dir = mkdtemp(prefix='pkg-'+self.pypi_package_name+'-', dir='.')

    distinfo = self.distinfo

    manifest_in = os.path.join(pkg_dir, 'MANIFEST.in')
    with open(manifest_in, "w") as mfp:
      # TODO: support extra files
      pass

    self.copyin(self.package_name, pkg_dir)
    pkgparts = self.pypi_package_name.split('.')
    # make missing __init__.py files; something of a hack
    if len(pkgparts) > 1:
      for dirpath, dirnames, filenames in os.walk(os.path.join(pkg_dir, pkgparts[0])):
        initpath = os.path.join(dirpath, '__init__.py')
        if not os.path.exists(initpath):
          warning("making stub %s", initpath)
          with open(os.path.join(dirpath, '__init__.py'), "a"):
            pass

    # final step: write setup.py with information gathered earlier
    self.write_setup(os.path.join(pkg_dir, 'setup.py'))

    return pkg_dir

  def write_setup(self, setup_path):
    ''' Transcribe a setup.py file.
    '''
    with Pfx("write_setup(%r)", setup_path):
      ok = True
      with open(setup_path, "w") as setup:
        X("GET .DISTINFO")
        distinfo = self.distinfo
        X("GOT .DISTINFO: %r", distinfo)
        out = partial(print, file=setup)
        out("#!/usr/bin/python")
        out("from distutils.core import setup")
        out("setup(")
        # mandatory fields, in preferred order
        for kw in ( 'name',
                    'description', 'author', 'author_email', 'version',
                    'url',
                    'long_description',
                  ):
          try:
            value = distinfo.pop(kw)
          except KeyError:
            warning("missing distinfo[%r]", kw)
            ok = False
          else:
            out("  %s = %r," % (kw, value))
        for kw in sorted(distinfo.keys()):
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
    base = os.path.join(self.libdir, package_subpath)
    if not self.is_package(package_name):
      base += '.py'
    return base

  def package_paths(self, package_name):
    ''' Generator to yield the file paths from a package relative to the `libdir` subdirectory.
    '''
    libdir = self.libdir
    package_subpath = pathify(package_name)
    if not self.is_package(package_name):
      yield package_subpath + '.py'
    else:
      libprefix = libdir + os.path.sep
      for dirpath, dirnames, filenames in os.walk(os.path.join(libdir, package_subpath)):
        for filename in filenames:
          if filename.startswith('.'):
            continue
          if filename.endswith('.pyc'):
            continue
          if filename.endswith('.py'):
            yield os.path.join(dirpath[len(libprefix):], filename)
          warning("skipping %s", os.path.join(dirpath, filename))

  def copyin(self, package_name, dstdir):
    base = self.package_base(package_name)
    runcmd([ 'hg',
               'archive',
                 '-r', '"%s"' % self.hg_tag,
                 '-I', base,
                 dstdir
           ])

if __name__ == '__main__':
  sys.exit(main(sys.argv))
