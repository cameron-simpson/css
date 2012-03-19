#!/usr/bin/python -tt
#
#       - Cameron Simpson <cs@zip.com.au> 01may2009
#
#       

from datetime import datetime
import re
import sys
from collections import namedtuple
from cs.alg import collate
from cs.lex import str1
from cs.logutils import Pfx

BinLog_DBQuery = namedtuple('BinLog_DBQuery',
                            'sql dbname when server_id log_pos thread_id exec_time error_code')
BinLog_DBThread = namedtuple('BinLog_DBThread',
                             'thread_id queries')

class BinLogParser(object):
  ''' Read mysqlbinlog(1) output and report.
  '''

  re_QUERY_MARKER = re.compile(r'#(\d\d)(\d\d)(\d\d) +(\d\d?):(\d\d):(\d\d) +server +id +(\d+) +end_log_pos +(\d+) +Query +thread_id=(\d+) +exec_time=(\d+) +error_code=(\d)+')
  re_USE_DBNAME = re.compile(r'use +([a-z[a-z0-9_]*)')
  ##re_SET_TIMESTAMP = re.compile(r'SET TIMESTAMP=(\d+)')

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

  def thread_by_id(self, thread_id):
    thread = self.db_threads.get(thread_id, None)
    if thread is None:
      thread = self.db_threads[thread_id] = BinLogParser.DBThread(thread_id)
    return thread

  def parse(self, fp):
    ''' Read lines from the file-like object 'fp', yield BinLog_DBQuery
        namedtuples.
    '''
    with Pfx(fp):
      dbname = None
      query_info = None
      query_text = None
      last_ts = None
      lineo = 0
      for line in fp:
        lineo += 1
        with Pfx(str(lineno)):
          assert line.endswith('\n'), "unexpected EOF: "+line
          line = line.expandtabs()
          ##print >>sys.stderr, ">", line

          # ^#(\d\d)(\d\d)(\d\d) +(\d\d?):(\d\d):(\d\d) +server +id +(\d+) +end_log_pos +(\d+) +Query +thread_id=(\d+) +exec_time=(\d+) +error_code=(\d)+
          m = BinLogParser.re_QUERY_MARKER.match(line)
          if m:
            # new query - tidy up old one and set up new one
            if query_info:
              yield BinLogParser.DBQuery(query_text, *query_info)
              ##yield BinLogParser.DBQuery(query_text, dbname, when, server_id, log_pos, thread, exec_time, error_code)
              query_text = ''
              query_info = None
            when = datetime( *[int(m.group(i+1)) for i in range(6)] )
            server_id = int(m.group(7))
            log_pos = int(m.group(8))
            thread_id = int(m.group(9))
            exec_time = int(m.group(10))
            error_code = int(m.group(11))
            query_info = [None, when, server_id, log_pos, thread_id, exec_time, error_code]
            continue

          # use database
          m = BinLogParser.re_USE_DBNAME.match(line)
          if m:
            dbname = str1(m.group(1))
            query_info[0] = dbname
            continue

          if ( line.startswith('#')
             or (line.startswith('/*') and line.endswith('*/;'))
             or line.startswith('SET ')
             ##or line.startswith('DELIMITER ')
             ##or line.startswith('ROLLBACK')
          ):
            ##print >>sys.stderr, "SKIP", line
            continue

          query_text += line

        if query_info:
          yield BinLogParser.DBQuery(query_text, *query_info)

  def collate_queries(self, queries):
    queries = list(queries)
    by_thread = collate(queries, 'thread_id')
    by_dbname = collate(queries, 'dbname')
    return by_thread, by_dbname
