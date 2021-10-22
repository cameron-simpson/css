#!/usr/bin/python

from __future__ import print_function
from getopt import GetoptError
import os.path
import sys
from cs.logutils import setup_logging, error
from cs.pfx import Pfx
from .backend import Backend, Update, ExtendUpdate, ResetUpdate
from .node import Node, NodeDB, NodeDBFromURL

DISTINFO = {
    'description':
    "a simple and versatile collection of nodes with attributes, accessed as direct Python objects and automatically transcribed to assorted backing stores (CSV, SQL, GDBM, etc); the CSV backend can be (loosely) shared between multiple clients",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.csvutils',
        'cs.debug',
        'cs.excutils',
        'cs.fileutils',
        'cs.html',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.py3',
        'cs.seq',
        'cs.sh',
        'cs.sharedfile',
        'cs.threads',
        'cs.timeutils',
        'cs.x',
    ]
}

def main(argv):
  xit = 0
  cmd = 'cs.nodedb'
  argv.pop(0)
  usage = '''Usage:
    %s dburl create
      Create a database.
    %s dburl dump >dump.csv
      Dump a database in CSV format.
    %s dburl load <dump.csv
      Load information from a CSV formatted file.
    %s dburl httpd bindhost:bindport
      Present the db via a web interface.
'''
  setup_logging(cmd)

  with Pfx(cmd, absolute=True):
    badopts = False

    if len(argv) < 1:
      error("missing dburl")
      badopts = True
    else:
      dburl = argv.pop(0)

    if len(argv) < 1:
      error("missing op")
      badopts = True
    else:
      op = argv.pop(0)
      # special commands that happen before opening the dburl
      ops = {
          "create": _create,
          "dump": _dump,
          "load": _load,
      }
      if op in ops:
        with Pfx(op):
          xit = ops[op](dburl, argv)
      else:
        DB = NodeDBFromURL(dburl)
        try:
          xit = DB.do_command([op] + argv)
        except GetoptError as e:
          error("%s: %s", op, e)
          badopts = True
        else:
          DB.close()

    if badopts or xit == 2:
      print(usage % (cmd, cmd, cmd, cmd), file=sys.stderr)

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
  sys.exit(main(sys.argv))
