#!/usr/bin/python
#
# Default distutils info for all packages in cs.* and utility
# functions to prep and release packages to PyPI.
#   - Cameron Simpson <cs@zip.com.au> 01jan2015
#
from __future__ import print_function
import sys
import os.path
from functools import partial
from getopt import getopt, GetoptError
import shutil
from tempfile import mkdtemp
from cs.logutils import setup_logging, Pfx, info, warning, error, X
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

  try:
    pkgdir = PKG.make_package()
  except NameError as e:
    X("NameError: %s", e)
    raise
  except Exception as e:
    raise
    error("failed to make package: %s: %s", type(e), e)
    xit = 1

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
    self._distinfo = import_module_name(package_name, 'distinfo')
    X("_distinfo = %r", self._distinfo)
    self.libdir = LIBDIR

  @property
  def version(self):
    :n bin-cs/cs-rel    

  @property
  def distinfo(self):
    ''' Property containing the distutils infor for this package.
    '''
    X("COMPUTE .distinfo")
    info = dict(self._distinfo)
    for kw, value in DISTINFO.items():
      X("from DISTINFO: %r ==> %r", kw, value)
      if value is not None:
        with Pfx(kw):
          if kw not in info:
            info[kw] = value

    ispkg = self.is_package
    if ispkg:
      ## # stash the package in a top level directory of that name
      ## info['package_dir'] = {package_name: package_name}
      info['packages'] = [self.package_name]
    else:
      info['py_modules'] = [self.package_name]

    for kw, value in ( ('name', self.pypi_package_name),
                       ('version', self.pypi_version),
                     ):
      X("SET %r ==> %r", kw, value)
      if value is not None:
        with Pfx(kw):
          if kw in info:
            if info[kw] != value:
              info("publishing %s instead of %s", value, info[kw])
          else:
            info[kw] = value
    X("COMPUTED .distinfo = %r", info)
    return info

  def make_package(self):
    ''' Create a temporary directory, populate it with the package contents, return the directory name.
    '''
    X("MAKE_PACKAGE...")
    distinfo = self.distinfo

    pkgdir = mkdtemp(prefix='pkg-'+self.pypi_package_name+'-', dir='.')

    self.write_setup(os.path.join(pkgdir, 'setup.py'))

    manifest_in = os.path.join(pkgdir, 'MANIFEST.in')
    with open(manifest_in, "w") as mfp:
      # TODO: support extra files
      pass

    self.copyin(package_name, libdir, pkgdir)
    pkgparts = pypi_package_name.split('.')
    # make missing __init__.py files; something of a hack
    if len(pkgparts) > 1:
      for dirpath, dirnames, filenames in os.walk(os.path.join(pkgdir, pkgparts[0])):
        initpath = os.path.join(dirpath, '__init__.py')
        if not os.path.exists(initpath):
          warning("making stub %s", initpath)
          with open(os.path.join(dirpath, '__init__.py'), "a"):
            pass

    return pkgdir

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

  @property
  def is_package(self):
    ''' Test whether `package_name` is a package (a directory with a __init__.py file).
        Do some sanity checks and complain loudly.
    '''
    libdir = self.libdir
    package_name = self.package_name
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

  def package_paths(package_name, libdir):
    ''' Generator to yield the file paths from a package relative to the `libdir` subdirectory.
    '''
    package_subpath = pathify(package_name)
    if not is_package(package_name, libdir):
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

  def copyin(package_name, libdir, dstdir):
    for rpath in package_paths(package_name, libdir):
      srcfile = os.path.join(libdir, rpath)
      dstfile = os.path.join(dstdir, rpath)
      info("copy %s ==> %s", srcfile, dstfile)
      needdir(os.path.dirname(dstfile))
      shutil.copyfile(srcfile, dstfile)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
