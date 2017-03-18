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
import cs.sh
from cs.threads import locked_property
from cs.py.modules import import_module_name
from cs.obj import O

URL_PYPI_PROD = 'https://pypi.python.org/pypi'
URL_PYPI_TEST = 'https://testpypi.python.org/pypi'

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/'

# local directory where the files live
LIBDIR = 'lib/python'

# defaults for packages without their own specifics
DISTINFO_DEFAULTS = {
    'author': "Cameron Simpson",
    'author_email': "cs@zip.com.au",
    'url': 'https://bitbucket.org/cameron_simpson/css/commits/all',
}

DISTINFO_CLASSIFICATION = {
    "Programming Language": "Python",
    "Development Status": "4 - Beta",
    "Intended Audience": "Developers",
    "Operating System": "OS Independent",
    "Topic": "Software Development :: Libraries :: Python Modules",
    "License": "OSI Approved :: GNU General Public License v3 (GPLv3)",
}


USAGE = '''Usage: %s [-n pypi-pkg-name] [-v pypi_version] pkg-name op [op-args...]
  -n pypi-pkg-name
        Name of package in PyPI. Default the same as the local package.
  -r pypi_repo_url
        Use the specified PyPI repository URL.
        Default: %s, or from the environment variable $PYPI_URL.
        Official site: %s
  -v pypi-version
        Version number for PyPI. Default from last release tag for pkg-name.
  Operations:
    check   Run setup.py check on the resulting package.
    register Register/update the package description and version.
    upload   Upload the package source distribution.'''

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd, URL_PYPI_TEST, URL_PYPI_PROD)
  setup_logging(cmd)

  badopts = False

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
    warning("missing pkg-name")
    badopts = True
  else:
    package_name = argv.pop(0)
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
  if not os.path.isdir(dirpath):
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

  def __init__(self, pypi_url, package_name, package_version, pypi_package_name=None, pypi_version=None):
    ''' Initialise: save package_name and its name in PyPI.
    '''
    self.pypi_url = pypi_url
    if pypi_package_name is None:
      pypi_package_name = package_name
    if pypi_version is None:
      pypi_version = package_version
    self.package_name = package_name
    self.pypi_package_name = pypi_package_name
    self.version = package_version
    self.pypi_version = pypi_version
    self.libdir = LIBDIR
    self._prep_distinfo()

  @property
  def hg_tag(self):
    return self.package_name + '-' + self.version

  def _prep_distinfo(self):
    ''' Property containing the distutils infor for this package.
    '''
    global DISTINFO_DEFAULTS
    global DISTINFO_CLASSIFICATION

    dinfo = dict(import_module_name(self.package_name, 'DISTINFO'))

    dinfo['package_dir'] = {'': self.libdir}

    for kw, value in DISTINFO_DEFAULTS.items():
      with Pfx(kw):
        if kw not in dinfo:
          dinfo[kw] = value

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
                      ('version', self.pypi_version),
                      ):
      if value is None:
        warning("_prep: no value for %r", name)
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
    rpath = os.path.join(*package_paths)
    if prefix_dir:
      rpath = os.path.join(prefix_dir, rpath)
    return rpath

  def pkg_readme_rpath(self, package_name=None, prefix_dir=None):
    if package_name is None:
      package_name = self.package_name
    package_paths = package_name.split('.')
    if self.is_package(package_name):
      return os.path.join(
          self.pkg_rpath(
              package_name=package_name,
              prefix_dir=prefix_dir),
          'README.rst')
    else:
      return os.path.join(
          self.pkg_rpath(
              package_name=package_name,
              prefix_dir=prefix_dir,
              up=True),
          'README-' + package_paths[-1] + '.rst')

  def make_package(self, pkg_dir=None):
    ''' Prepare package contents in the directory `pkg_dir`, return `pkg_dir`.
        If `pkg_dir` is not supplied, create a temporary directory.
    '''
    if pkg_dir is None:
      pkg_dir = mkdtemp(prefix='pkg-' + self.pypi_package_name + '-', dir='.')

    distinfo = self.distinfo

    manifest_path = os.path.join(pkg_dir, 'MANIFEST.in')
    with open(manifest_path, "w") as mfp:
      # TODO: support extra files
      pass

    self.copyin(self.package_name, pkg_dir)

    readme_subpath = self.pkg_readme_rpath(prefix_dir=self.libdir)
    readme_path = os.path.join(pkg_dir, readme_subpath)
    if os.path.exists(readme_path):
      if 'long_description' in distinfo:
        info(
            'long_description: already provided, ignoring %s', readme_subpath)
      else:
        with open(readme_path) as readmefp:
          distinfo['long_description'] = readmefp.read()
      shutil.copy2(readme_path, os.path.join(pkg_dir, 'README.rst'))
      with open(manifest_path, "a") as mfp:
        mfp.write('include README.rst\n')
    else:
      warning('no README at %r', readme_path)

    # final step: write setup.py with information gathered earlier
    self.write_setup(os.path.join(pkg_dir, 'setup.py'))

    return pkg_dir

  def checkout(self):
    return PyPI_PackageCheckout(self)

  def check(self):
    with self.checkout() as pkg_co:
      pkg_co.check()

  def register(self):
    with self.checkout() as pkg_co:
      pkg_co.register()

  def upload(self):
    with self.checkout() as pkg_co:
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
    base = os.path.join(self.libdir, package_subpath)
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
      test_path = os.path.join(libdir, test_subpath)
      if os.path.exists(test_path):
        yield test_subpath
    else:
      # packages - all .py files in directory
      # warning about unexpected other files
      libprefix = libdir + os.path.sep
      for dirpath, dirnames, filenames in os.walk(os.path.join(libdir, package_subpath)):
        for filename in filenames:
          if filename.startswith('.'):
            continue
          if filename.endswith('.pyc'):
            continue
          if filename.endswith('.py'):
            yield os.path.join(dirpath[len(libprefix):], filename)
            continue
          warning("skipping %s", os.path.join(dirpath, filename))
    readme_subpath = self.pkg_readme_rpath(package_name)
    readme_path = os.path.join(libdir, readme_subpath)
    if os.path.exists(readme_path):
      yield readme_subpath

  def copyin(self, package_name, dstdir):
    hgargv = ['set-x', 'hg',
              'archive',
              '-r', '"%s"' % self.hg_tag,
              ]
    first = True
    package_parts = package_name.split('.')
    while package_parts:
      superpackage_name = '.'.join(package_parts)
      base = self.package_base(superpackage_name)
      if first:
        # collect entire package contents
        for subpath in self.package_paths(superpackage_name, self.libdir):
          hgargv.extend(['-I', os.path.join(self.libdir, subpath)])
      else:
        # just collecting required __init__.py files
        hgargv.extend(['-I', os.path.join(base, '__init__.py')])
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
    ##self.inpkg("find . -type f | sort | xxargs ls -ld -- ")
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

  def check(self):
    self.setup_py('check', '-s', '--restructuredtext')

  def register(self):
    self.setup_py('register', '-r', self.pypi_url)

  def upload(self):
    self.setup_py('sdist', 'upload', '-r', self.pypi_url)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
