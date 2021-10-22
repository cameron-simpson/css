#!/usr/bin/python -tt
#
# Import the C buffer scanner, building it if necessary.
# Fall back to the pure python one if we fail.
#   - Cameron Simpson <cs@cskk.id.au>
#

''' The byte scanning function scanbuf.
    In C by choice, in Python if not.
'''

from distutils.core import setup, Extension
from os import chdir, getcwd
from os.path import dirname, join as joinpath
import sys
##from time import sleep
from cs.logutils import error, warning
from cs.x import X

try:
  from ._scan import scanbuf
except ImportError:
  warning("building _scan from _scan.c")

  def do_setup():
    ''' Run distutils.core.setup from the top of the lib tree.
        Side effect: changes directory, needs undoing.
    '''
    pkgdir = dirname(__file__)
    chdir(dirname(dirname(pkgdir)))
    return setup(
        ext_modules=[Extension("cs.vt._scan", [joinpath(pkgdir, '_scan.c')])],
    )

  ### delay, seemingly needed to make the C version look "new"
  ##sleep(2)
  oargv = sys.argv
  owd = getcwd()
  sys.argv = [oargv[0], 'build_ext', '--inplace']
  try:
    do_setup()
  except SystemExit as e:
    chdir(owd)
    error("SETUP FAILS: %s:%s", type(e), e)
    scanbuf = None
  else:
    chdir(owd)
    try:
      from ._scan import scanbuf
    except ImportError as e:
      error("import fails after setup: %s", e)
      scanbuf = None
  finally:
    sys.argv = oargv

if scanbuf is None:
  warning("using pure Python scanbuf")

  def scanbuf(hash_value, chunk):
    ''' Pure Python scanbuf if there's no C version.
    '''
    offsets = []
    for offset, b in enumerate(chunk):
      hash_value = (
          ((hash_value & 0x001fffff) << 7)
          | ((b & 0x7f) ^ ((b & 0x80) >> 7))
      )
      if hash_value % 4093 == 4091:
        offsets.append(offset)
    return hash_value, offsets

if False:
  # debugging wrapper
  scanbuf0 = scanbuf

  def scanbuf(h, data):
    ''' Debugging scan function.
    '''
    X("scan %d bytes", len(data))
    h2, offsets = scanbuf0(h, data)
    ##X("scan => %r", offsets)
    return h2, offsets
