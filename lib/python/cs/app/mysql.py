#!/usr/bin/python -tt
#
#       - Cameron Simpson <cs@zip.com.au> 01may2009
#
#

from datetime import datetime
import os.path
import re
import sys
from getopt import getopt, GetoptError
from cs.logutils import setup_logging, Pfx, warning, error
from cs.psutils import stop

ETC_MYCNF = '/etc/my.cnf'
PID_FILE = '/var/run/mysqld.pid'

def main(argv):
  argv = list(argv)
  xit = 2
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd = cmd)
  usage = r'''Usage:
    %s [-F mycnf] run [/path/to/mysqld] [mysqld-options...]
    %s [-F mycnf] start [/path/to/mysqld] [mysqld-options...]
    %s [-F mycnf] stop
    %s [-F mycnf] status
''' % (cmd, cmd, cmd, cmd)
  badopts = False

  mycnf = ETC_MYCNF
  pid_file = PID_FILE

  try:
    opts, argv = getopt(argv, 'F:')
  except GetoptError, e:
    error(e)
    badopts = True
  else:
    for opt, arg in opts:
      with Pfx(opt):
        if opt == '-F':
          mycnf = arg
        else:
          error("unimplemented option")
          badopts = True

  if len(argv) == 0:
    warning("missing op, expected {run,start,stop,status}")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      try:
        if op == 'run':
          xit = mysqld_start(mycnf, pid_file, argv)
        elif op == 'start':
          xit = mysqld_start(mycnf, pid_file, argv)
        elif op == 'stop':
          xit = mysqld_stop(mycnf, pid_file, argv)
        elif op == 'status':
          xit = mysqld_status(mycnf, pid_file, argv)
        else:
          warning("unrecognised operation")
          badopts = True
      except GetoptError, e:
        warning(e)
        badopts = True

  if badopts:
    sys.stderr.write(usage)
    xit = 2

  return xit

def mycnf_read(mycnf, section):
  ''' Read a my.cnf file and yield (option, value) tuples from the named
      `section`. We're not using ConfigParser because you can have repeated
      options in a my.cnf.
  '''
  with Pfx(mycnf):
    with open(mycnf) as mfp:
      in_section = False
      lineno = 0
      for line in mfp:
        lineno += 1
        if not line.endswith("\n"):
          warning("line %d: unexpected EOF", lineno)
        if line.startswith('#'):
          continue
        line = line.strip()
        if len(line) == 0:
          continue
        if line.startswith('['):
          in_section = False
          if not line.endswith(']'):
            warning("line %d: %s: missing ']'", lineno, line)
          else:
            line = line[1:-1].strip()
            in_section = (line == section)
          continue
        if not in_section:
          continue
        try:
          option, value = line.split('=', 1)
        except ValueError:
          option = line
          value = None
          #warning("line %d: %s: missing '='", lineno, option)
        else:
          option = option.rstrip()
          value = value.lstrip()
        yield option.strip(), value

def mycnf_options(mycnf, section, optmap=None):
  ''' Read a my.cnf file and return a map from option to values.
      Failure to open the my.cnf file returns an empty map.
  '''
  if optmap is None:
    optmap = {}
  try:
    options = mycnf_read(mycnf, section)
    for opt, value in options:
      optmap.setdefault(opt, []).append(value)
  except IOError, e:
    options = ()
  return optmap

def mycnf_argv(argv, optmap=None):
  ''' Apply a list of "--option=value" string to `optmap`.
  '''
  if optmap is None:
    optmap = {}
  for arg in argv:
    if not arg.startswith('--'):
      raise ValueError, "bad option, no leading --: "+arg
    try:
      option, value = arg[2:].split('=', 1)
    except ValueError:
      option = arg[2:]
      value = None
    optmap.setdefault(option, []).append(value)
  return optmap

def load_config(mycnf, section, argv, optmap=None):
  if optmap is None:
    optmap = {}
  optmap = mycnf_options(mycnf, section, optmap)
  optmap = mycnf_argv(argv, optmap)
  return optmap

def mysqld_run(mycnf, pid_file, argv, dofork=False):
  ''' Exec a mysqld process. Write our process id to `pid_file`.
      If `do_fork` then fork first and return the child process id.
  '''
  mysqld = 'mysqld'
  if len(argv) > 0 and argv[0].startswith('/'):
    mysqld = argv.pop(0)
  optmap = load_config(mycnf, 'mysqld', argv, {'pid-file': [pid_file]})
  pid_file = optmap['pid-file'][-1]
  optmap['pid-file'] = [pid_file]
  mysqld_argv = [ mysqld, '--no-defaults' ]
  for opt, values in optmap.iteritems():
    for value in values:
      if value is None:
        mysqld_argv.append('--' + opt)
      else:
        mysqld_argv.append('--' + opt + '=' + value)
  mysqld_argv.extend(argv)
  print mysqld_argv
  if dofork:
    pid = os.fork()
    if pid > 0:
      with open(pid_file, "w") as pfp:
        pfp.write("%d\n" % (os.getpid(),))
    return pid
  os.execvp(mysqld_argv[0], mysqld_argv)
  raise RuntimeError, "NOTREACHED"

def mysqld_start(mycnf, pid_file, argv):
  ''' Start a mysqld process. Write its process id to `pid_file`. Return the process id.
  '''
  return mysqld_run(mycnf, pid_file, argv, dofork=True)

def mysqld_status(mycnf, pid_file, argv):
  optmap = load_config(mycnf, 'mysqld', argv, {'pid-file': [pid_file]})
  pid_file = optmap['pid-file'][-1]
  optmap['pid-file'] = [pid_file]
  if stop(pid_file, signum=0):
    info("%s: running", pid_file)
  else:
    info("%s: not running", pid_file)

def mysqld_stop(mycnf, pid_file, argv):
  optmap = load_config(mycnf, 'mysqld', argv, {'pid-file': [pid_file]})
  pid_file = optmap['pid-file'][-1]
  optmap['pid-file'] = [pid_file]
  stop(pid_file, wait=10, do_SIGKILL=True)

class BinLogParser(object):
  ''' Read mysqlbinlog(1) output and report.
  '''

  re_QUERY_MARKER = re.compile(r'#(\d\d)(\d\d)(\d\d) +(\d\d?):(\d\d):(\d\d) +server +id +(\d+) +end_log_pos +(\d+) +Query +thread_id=(\d+) +exec_time=(\d+) +error_code=(\d)+')
  re_USE_DBNAME = re.compile(r'use +([a-z[a-z0-9_]*)')
  re_SET_TIMESTAMP = re.compile(r'SET TIMESTAMP=(\d+)')

  def __init__(self):
    self.db_threads = {}
    self.db_queries = []

  class DBThread(list):
    ''' A DBThread tracks a database thread state.
        The list contains DBQueries associated with the thread.
    '''
    def __init__(self, thread_id):
      self.thread_id = thread_id

  class DBQuery(object):
    ''' An object representing an SQL query from the binlog.
    '''

    def __init__(self, dbname, when, server_id, log_pos, thread, exec_time, error_code):
      self.dbname = dbname
      self.when = when
      self.server_id = server_id
      self.log_pos = log_pos
      self.exec_time = exec_time
      self.error_code = error_code
      self.thread = thread
      thread.append(self)

  def thread_by_id(self, thread_id):
    thread = self.db_threads.get(thread_id, None)
    if thread is None:
      thread = self.db_threads[thread_id] = BinLogParser.DBThread(thread_id)
    return thread

  def parse(self, fp):
    ''' Read lines from the file-like object 'fp'.
    '''
    dbname = None
    query = None
    query_text = None
    last_ts = None
    for line in fp:
      assert line[-1] == '\n', "unexpected EOF: "+line
      line = line[:-1].rstrip().expandtabs()
      ##print >>sys.stderr, ">", line

  #(\d\d)(\d\d)(\d\d) +(\d\d?):(\d\d):(\d\d) +server +id +(\d+) +end_log_pos +(\d+) +Query +thread_id=(\d+) +exec_time=(\d+) +error_code=(\d)+
      m = BinLogParser.re_QUERY_MARKER.match(line)
      if m:
        # new query - tidy up old one and set up new one
        if query is not None:
          query.query_text = query_text;
          query_text = None
          query = None
        when = datetime( *[int(m.group(i+1)) for i in range(6)] )
        server_id = int(m.group(7))
        log_pos = int(m.group(8))
        thread_id = int(m.group(9))
        exec_time = int(m.group(10))
        error_code = int(m.group(11))
        thread = self.thread_by_id(thread_id)
        query = BinLogParser.DBQuery(dbname, when, server_id, log_pos, thread, exec_time, error_code)
        self.db_queries.append(query)
        query_text = ''
        continue

      m = BinLogParser.re_USE_DBNAME.match(line)
      if m:
        dbname = query.dbname = m.group(1)
        continue

      m = BinLogParser.re_SET_TIMESTAMP.match(line)
      if m:
        ts = query.timestamp = int(m.group(1))
        continue

      if ( line.startswith('#')
         or ( line.startswith('/*') and line.endswith('*/;') )
         or line.startswith('SET ')
         or line.startswith('DELIMITER ')
         or line.startswith('ROLLBACK')
         ):
        ##print >>sys.stderr, "SKIP", line
        continue

      if len(line) > 0:
        if query_text is None:
          print >>sys.stderr, "LINE=[%s]" % (line,)
        query_text += ' ' + line.lstrip()

    if query is not None:
      query.query_text = query_text


  def report(self):
    print "thread_ids = %s" % ([ T.thread_id for T in self.db_threads.values() ],)
    print "%d queries" % (len(self.db_queries),)
    by_time = {}
    by_dbname = {}
    for tid in self.db_threads.keys():
      T = self.thread_by_id(tid)
      for Q in T:
        by_time.setdefault(Q.timestamp, []).append(Q)
        by_dbname.setdefault(Q.dbname, []).append(Q)
        if Q.exec_time > 0:
          print tid, Q.exec_time, Q.query_text

    timestamps = by_time.keys()
    timestamps.sort()
    for ts in timestamps:
      Qs = by_time[ts]
      print ts, len(Qs), "queries"
      continue
      print ts, Qs[0].query_text
      if len(Qs) > 1:
        for Q in Qs[1:]:
          print "\t", Q.dbname, Q.query_text

    dbnames = by_dbname.keys()
    dbnames.sort()
    for dbname in dbnames:
      print "%-16s %d queries" % (dbname, len(by_dbname[dbname]))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
