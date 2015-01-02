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
from cs.logutils import setup_logging, Pfx, info, warning, error
from cs.py.modules import import_module_name

# published URL
URL_BASE = 'https://bitbucket.org/cameron_simpson/css/src/tip/lib/python'

# local directory where the files live
LIBDIR = 'lib/python'

distinfo = {
    'author': "Cameron Simpson",
    'author_email': "cs@zip.com.au",
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

  distinfo = import_module_name(package_name, 'distinfo')

  ok = True

  for kw, value in ( ('name', pypi_package_name),
                     ('version', pypi_version),
                   ):
    if value is not None:
      with Pfx(kw):
        if kw in distinfo:
          if distinfo[kw] != value:
            info("publishing %s instead of %s", value, distinfo[kw])
        else:
          distinfo[kw] = value

  ##wkdir = mkdtemp()
  ##pkgdir = os.path.join(wkdir, pypi_package_name)
  pkgdir = os.path.join('TESTDIR', pypi_package_name)
  needdir(pkgdir)

  setup_py = os.path.join(pkgdir, 'setup.py')
  if not write_setup(setup_py, distinfo):
    ok = False

  pkgsubdir = os.path.join(pkgdir, pypi_package_name)
  copyin(package_name, LIBDIR, pkgsubdir)

  if not ok:
    error("aborting package build")
    return 1

  print(pkgdir)
  return 0

def write_setup(setup_path, distinfo):
  ''' Transcribe a setup.py file.
  '''
  with Pfx("write_setup(%r)", setup_path):
    ok = True
    with open(setup_path, "w") as setup:
      out = partial(print, file=setup)
      out("#!/usr/bin/python")
      out("from distutils.core import setup")
      out("")
      out("setup(")
      for kw in ( 'description', 'author', 'author_email', 'version',
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
    return ok

def pathify(package_name):
  ''' Translate foo.bar.zot into foo/bar/zot.
  '''
  return package_name.replace('.', os.path.sep)

def package_paths(package_name, libdir):
  ''' Generator to yield the file paths from a package relative to the `libdir` subdirectory.
  '''
  package_subpath = pathify(package_name)
  if os.path.exists(os.path.join(libdir, package_subpath + '.py')):
    yield package_subpath + '.py'
    return
  if not os.path.exists(os.path.join(libdir, package_subpath, '__init__.py')):
    raise ValueError("no %r in %r" % (package_name, libdir))
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

def needdir(dirpath):
  if not os.path.isdir(dirpath):
    info("makedirs(%r)", dirpath)
    os.makedirs(dirpath)

def copyin(package_name, libdir, dstdir):
  for rpath in package_paths(package_name, libdir):
    dstsubdir = os.path.join(dstdir, os.path.dirname(rpath))
    needdir(dstsubdir)
    srcfile = os.path.join(libdir, rpath)
    dstfile = os.path.join(dstdir, rpath)
    info("copy %s ==> %s", srcfile, dstfile)
    shutil.copyfile(srcfile, dstfile)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
