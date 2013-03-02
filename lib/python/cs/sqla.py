#!/usr/bin/python
#
# Convenience stuff for SQLAlchemy.
#       - Cameron Simpson <cs@zip.com.au> 02mar2013
#

from __future__ import print_function
import sys
import os.path
from getopt import GetoptError
import sqlalchemy
from cs.logutils import setup_logging, D, Pfx, error
from cs.obj import O

usage = '''Usage: %s dburl op [args...]
  dburl SQLAlchemy compatible database URL.
  op    Operation to perform.'''

def main(argv):
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)

  badopts = False

  if not argv:
    error("missing dburl")
    badopts = True
  else:
    dburl = argv.pop(0)

  if not argv:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    DB = SQLA(ur=dburl)
    with Pfx(op):
      try:
        try:
          opfunc = getattr(DB, 'op_'+op)
        except AttributeError as e:
          error("unknown op")
          badopts = True
        else:
          xit = opfunc(argv)
      except GetoptError as e:
        error(str(e))
        badopts = True

  if badopts:
    print(usage % (cmd,), file=sys.stderr)
    return 2

  return xit

class SQLA(O):

  def __init__(self, dburl):
    self.dburl = dburl

if __name__ == '__main__':
  sys.exit(main(sys.argv))
