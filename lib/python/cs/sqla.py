#!/usr/bin/python
#
# Convenience stuff for SQLAlchemy.
#       - Cameron Simpson <cs@cskk.id.au> 02mar2013
#

from __future__ import print_function
import sys
import os.path
from cmd import Cmd
from getopt import GetoptError
import shlex
from sqlalchemy import MetaData, create_engine
from threading import RLock
from types import SimpleNamespace as NS
from cs.logutils import setup_logging, D, error
from cs.pfx import Pfx
from cs.seq import the
from cs.threads import locked_property

usage = '''Usage: %s dburl [op [args...]]
  dburl SQLAlchemy compatible database URL.
  op    Operation to perform.'''

def main(argv):
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)

  xit = 0
  badopts = False
  dburl = None

  if not argv:
    error("missing dburl")
    badopts = True
  else:
    dburl = argv.pop(0)
    if dburl.startswith('$'):
      envvar = dburl[1:]
      try:
        varval = os.environ[envvar]
      except KeyError as e:
        error("dburl: no such envvar: %s", dburl)
        badopts = True
      else:
        dburl = varval

  if not badopts:
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
            if op in DB.table_names:
              xit = DB.op_table([op] + argv)
            else:
              error("unknown op (table_names = %s)", DB.table_names)
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

class SQLA(NS):

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

  @property
  def table_names(self):
    return [ t.name for t in self.tables ]

  @locked_property
  def t(self):
    ts = NS()
    for tbl in self.tables:
      setattr(ts, tbl.name, tbl)
    return ts

  def op_list(self, args):
    if args:
      raise GetoptError("extra arguments: %s" % (' '.join(args),))
    for tbl in self.tables:
      print(tbl.name, type(tbl))

  def op_table(self, args):
    if not args:
      raise GetoptError("missing table name")
    table_name = args.pop(0)
    with Pfx(table_name):
      tbl = getattr(self.t, table_name)
      if not args:
        raise GetoptError("missing node name")
      nodename = args.pop(0)
      row = the(tbl.select(tbl.c.name == nodename).execute())
      if not args:
        for col in sorted(row.keys()):
          print("%-14s: %s" % (col, row[col]))
        return
      for arg in args:
        if '=' in arg:
          col, value = arg.split('=', 1)
          print("SET %s.%s = %s" % (nodename, col, value))
          tbl.update().where(tbl.c.name == nodename).values(**{col: value}).execute()
        else:
          col = arg
          print("%s.%s = %s" % (nodename, col, row[col]))

class CmdLoop(Cmd):

  def __init__(self, dburl):
    Cmd.__init__(self, '\t')
    self.sqla = SQLA(dburl)

  def property(self):
    return self.sqla.dburl

  def emptyline(self):
    pass

  def onecmd(self, line):
    try:
      return Cmd.onecmd(self, line)
    except GetoptError as e:
      error(str(e))

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
        if op in self.sqla.table_names:
          self.sqla.op_table([op] + words)
        else:
          error("unknown command")
      else:
        fn(words)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
