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

PYPI_PROD_URL = 'https://pypi.python.org/pypi'
PYPI_TEST_URL = 'https://testpypi.python.org/pypi'
PYPI_DFLT_URL = PYPI_TEST_URL

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
        Default: %s
        Official site: %s
  -v pypi-version
        Version number for PyPI. Default from last release tag for pkg-name.
  Operations:
    check   Run setup.py check on the resulting package.
    register Register/update the package description and version.
    upload   Upload the package source distribution.'''

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd, PYPI_DFLT_URL, PYPI_PROD_URL)
  setup_logging(cmd)

  badopts = False

  pypi_package_name = None
  pypi_version = None
  pypi_url = PYPI_DFLT_URL

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

  if pypi_package_name is None:
    pypi_package_name = package_name

  PKG = PyPI_Package(package_name,
                     pypi_package_name=pypi_package_name,
                     pypi_url=pypi_url,
                     pypi_version=pypi_version)

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
  ''' Class for creating and administering cs.* packages for PyPI.
  '''

  def __init__(self, package_name, pypi_package_name = None, pypi_url=None, pypi_version=None):
    ''' Iinitialise: save package_name and its name in PyPI.
    '''
    if pypi_package_name is None:
      pypi_package_name = package_name
    if pypi_url is None:
      pypi_url = PYPI_DFLT_URL
    self.package_name = package_name
    self.pypi_package_name = pypi_package_name
    self.pypi_url = pypi_url
    self.libdir = LIBDIR
    self._lock = RLock()
    self._prep_distinfo()
    if pypi_version is not None:
      self._version = pypi_version

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
    global DISTINFO_DEFAULTS
    global DISTINFO_CLASSIFICATION

    info = dict(import_module_name(self.package_name, 'DISTINFO'))

    info['package_dir'] = {'': self.libdir}

    for kw, value in DISTINFO_DEFAULTS.items():
      with Pfx(kw):
        if kw not in info:
          X("%s = %r", kw, value)
          info[kw] = value

    classifiers = info['classifiers']
    for classifier_topic, classifier_subsection in DISTINFO_CLASSIFICATION.items():
      classifier_prefix = classifier_topic + " ::"
      classifier_value = classifier_topic + " :: " + classifier_subsection
      X("look for %r ...", classifier_prefix)
      if not any( classifier.startswith(classifier_prefix)
                  for classifier in classifiers
                ):
        X("classifiers + %s", classifier_value)
        info['classifiers'].append(classifier_value)
      else:
        X("already has %r*", classifier_prefix)

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

  def pkg_rpath(self, package_name=None, prefix_dir=None, up=False):
    ''' Return a path based on a `package_name` (default self.package_name).
        `prefix_dir`: is supplied, prefixed to the returned relative path.
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
      pkg_dir = mkdtemp(prefix='pkg-'+self.pypi_package_name+'-', dir='.')

    distinfo = self.distinfo

    manifest_path = os.path.join(pkg_dir, 'MANIFEST.in')
    with open(manifest_path, "w") as mfp:
      # TODO: support extra files
      pass

    self.copyin(self.package_name, pkg_dir)

    readme_subpath = self.pkg_readme_rpath(prefix_dir=self.libdir)
    readme_path = os.path.join(pkg_dir, readme_subpath)
    X("make_package: readme_path = %r", readme_path)
    if os.path.exists(readme_path):
      if 'long_description' in distinfo:
        warning('long_description: already provided, ignoring %s', readme_subpath)
      else:
        with open(readme_path) as readmefp:
          distinfo['long_description'] = readmefp.read().decode('utf-8')
      shutils.copy2(readme_path, os.path.join(pkg_dir, readme_rst))
      with open(manifest_path, "a") as mfp:
        mfp.write(readme_rst)
        mfp.write('\n')
    else:
      warning('no README at %r', readme_subpath)

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
        out("#!/usr/bin/python")
        out("from distutils.core import setup")
        out("setup(")
        # mandatory fields, in preferred order
        for kw in ( 'name',
                    'description', 'author', 'author_email', 'version',
                    'url',
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

  def package_paths(self, package_name, libdir):
    ''' Generator to yield the file paths from a package relative to the `libdir` subdirectory.
    '''
    X("PACKAGE_PATHS...")
    package_subpath = pathify(package_name)
    if not self.is_package(package_name):
      yield package_subpath + '.py'
      test_subpath = package_subpath + '_tests.py'
      test_path = os.path.join(libdir, test_subpath)
      X("check for tests at: %r", test_path)
      if os.path.exists(test_path):
        yield test_subpath
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
    readme_subpath = self.pkg_readme_rpath(package_name)
    readme_path = os.path.join(libdir, readme_subpath)
    X("PROBE %r", readme_path)
    if os.path.exists(readme_path):
      yield readme_subpath

  def copyin(self, package_name, dstdir):
    hgargv = [ 'set-x', 'hg',
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
          hgargv.extend([ '-I', os.path.join(self.libdir, subpath) ])
      else:
        # just collecting requires __init__.py files
        hgargv.extend([ '-I',  os.path.join(base, '__init__.py') ])
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
    return self.inpkg_argv([ 'python3', 'setup.py' ] + list(argv))

  def check(self):
    self.setup_py('check')

  def register(self):
    self.setup_py('register', '-r', self.pypi_url)

  def upload(self):
    self.setup_py('sdist', 'upload', '-r', self.pypi_url)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
