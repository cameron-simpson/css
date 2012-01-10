#!/usr/bin/python

from getopt import GetoptError
import sys
from cs.logutils import setup_logging, warning, error, info
from cs.app.myke.make import Maker

default_cmd = 'myke'

usage="Usage: %s [options...] [targets...]"

def main(argv):
  cmd, args = argv[0], argv[1:]
  setup_logging(cmd)

  M = Maker()
  try:
    args, badopts = M.getopt(args)
  except GetoptError, e:
    warning("bad options: %s", e)
    badopts = True
  if badopts:
    print >>sys.stderr, usage % (cmd,)
    return 2

  M.loadMakefile()

  xit = 0 if M.make(args) else 1
  return xit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
