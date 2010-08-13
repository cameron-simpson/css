#!/usr/bin/python

import os.path
import sys
from cs.nodedb.node import Node, NodeDB, Backend, NodeDBFromURL
from cs.logutils import error

def main(argv):
  import logging
  from cs.logutils import Pfx, error
  xit = 0
  cmd = os.path.basename(argv.pop(0))
  logging.basicConfig(format=cmd.replace("%", "%%") + ": %(levelname)s: %(message)s")
  usage = '''Usage:
    %s dburl create
      Create a database.
    %s dburl dump >dump.csv
      Dump a database in CSV format.
    %s dburl load <dump.csv
      Load information from a CSV formatted file.
'''

  badopts = False

  if badopts:
    print >>sys.stderr, usage % (cmd, cmd, cmd)

  if len(argv) < 1:
    error("missing dburl")
    badopts=True
  else:
    dburl = argv.pop(0)

  if len(argv) < 1:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    ops = { "create": _create,
            "dump":   _dump,
            "load":   _load,
          }
    with Pfx(op):
      if op in ops:
        xit = ops[op](dburl, argv)
      else:
        error("unsupported op")
        badopts = True

  if badopts or xit == 2:
    print >>sys.stderr, usage % (cmd, cmd, cmd)

  return xit

def _create(dburl, argv):
  xit = 0
  raise NotImplementedError

def _dump(dburl, argv):
  xit = 0
  if len(argv) > 0:
    error("extra arguments: %s" % (argv,))
    xit = 2
  else:
    DB = NodeDBFromURL(dburl)
    DB.dump(sys.stdout)
  return xit

def _load(dburl, argv):
  xit = 0
  if len(argv) > 0:
    error("extra arguments: %s" % (argv,))
    xit = 2
  else:
    DB = NodeDBFromURL(dburl)
    DB.load(sys.stdin)
  return xit


if __name__ == '__main__':
  main(sys.argv)
