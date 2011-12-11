#!/usr/bin/python

from cs.logutils import setup_logging, warning, error
from cs.app.myke.make import Maker

usage="Usage: %s [options...] [targets...]"

def main(argv):
  cmd, args = argv[0], argv[1:]
  setup_logging(cmd)

  M = Maker()
  badopts = False
  warning("MISSING OPTION PARSING")
  if badopts:
    print >>sys.stderr, usage % (cmd,)
    return 2

  xit = M.make(args)
  return xit

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))
