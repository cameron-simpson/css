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

  with Maker() as M:
    try:
      args, badopts = M.getopt(args)
    except GetoptError as e:
      warning("bad options: %s", e)
      badopts = True
    if badopts:
      print >>sys.stderr, usage % (cmd,)
      return 2

    M.loadMakefiles(M.makefiles)
    M.loadMakefiles(M.appendfiles)

    if args:
      targets = args
    else:
      target = M.default_target
      if target is None:
        targets = ()
      else:
        targets = (M.default_target,)

    if not targets:
      error("no default target")
      xit = 1
    else:
      xit = 0 if M.make(targets) else 1

  return xit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
