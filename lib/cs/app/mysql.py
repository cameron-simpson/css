#!/usr/bin/python -tt
#
#       - Cameron Simpson <cs@zip.com.au> 01may2009
#
#       

from datetime import datetime
import re
import sys

class BinLogParser(object):
  ''' Read mysqlbinlog(1) output and report.
  '''

  re_QUERY_MARKER = re.compile(r'#(\d\d)(\d\d)(\d\d) +(\d\d?):(\d\d):(\d\d) +server +id +(\d+) +end_log_pos +(\d+) +Query +thread_id=(\d+) +exec_time=(\d+) +error_code=(\d)+')
  re_USE_DBNAME = re.compile(r'use +([a-z[a-z0-9_]*)')
  re_SET_TIMESTAMP = re.compile(r'SET TIMESTAMP=(\d+)')

  def __init__(self):
    self.db_threads = {}
    self.db_queries = []

  class DBThread(object):
    ''' A BDThread tracks a database thread state.
    '''
    def __init__(self, thread_id):
      self.thread_id = thread_id
      self.queries = []

    def append_query(self, query):
      self.queries.append(query)

  class DBQuery(object):
    def __init__(self, dbname, when, server_id, log_pos, thread, exec_time, error_code):
      self.dbname = dbname
      self.when = when
      self.server_id = server_id
      self.log_pos = log_pos
      self.thread = thread
      self.exec_time = exec_time
      self.error_code = error_code
      thread.append_query(self)

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

      if line.startswith('#') \
      or (line.startswith('/*') and line.endswith('*/;')) \
      or line.startswith('SET ') \
      or line.startswith('DELIMITER ') \
      or line.startswith('ROLLBACK') \
      :
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
      for Q in T.queries:
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
