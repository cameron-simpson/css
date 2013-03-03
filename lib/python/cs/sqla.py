#!/usr/bin/python
#
# Convenience stuff for SQLAlchemy.
#       - Cameron Simpson <cs@zip.com.au> 02mar2013
#

from __future__ import print_function
import sys
import os.path
from cmd import Cmd
from getopt import GetoptError
import shlex
from sqlalchemy import MetaData, create_engine
from threading import RLock
from cs.logutils import setup_logging, D, Pfx, error
from cs.threads import locked_property
from cs.obj import O

usage = '''Usage: %s dburl [op [args...]]
  dburl SQLAlchemy compatible database URL.
  op    Operation to perform.'''

def main(argv):
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)

  xit = 0
  badopts = False

  if not argv:
    error("missing dburl")
    badopts = True
  else:
    dburl = argv.pop(0)

  if not argv:
    cmdloop = CmdLoop(dburl)
    cmdloop.prompt = cmd+'> '
    cmdloop.cmdloop()
  else:
    op = argv.pop(0)
    DB = SQLA(dburl=dburl)
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
    self._lock = RLock()

  @locked_property
  def engine(self):
    return create_engine(self.dburl, echo=len(os.environ.get('DEBUG','')) > 0)

  @locked_property
  def connection(self):
    return self.engine.connect()

  @locked_property
  def metadata(self):
    m = MetaData()
    m.bind = self.engine
    return m

  @locked_property
  def tables(self):
    m = self.metadata
    m.reflect()
    return m.sorted_tables

  def op_list(self, args):
    if args:
      raise GetoptError("extra arguments: %s" % (' '.join(args),))
    for t in self.tables:
      print(t.name, type(t))

class CmdLoop(Cmd):

  def __init__(self, dburl):
    Cmd.__init__(self, '\t')
    self.sqla = SQLA(dburl)

  def property(self):
    return self.sqla.dburl

  def emptyline(self):
    pass

  def default(self, line):
    words = shlex.split(line, comments=True)
    print("words =", words)
    if not words:
      return
    op = words.pop(0)
    if op == 'EOF':
      return True
    with Pfx(op):
      try:
        fn = getattr(self.sqla, 'op_'+op)
      except AttributeError as e:
        error("unknown command")
      else:
        fn(words)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
