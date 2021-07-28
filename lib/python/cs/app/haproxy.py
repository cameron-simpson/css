#!/usr/bin/python
#
# Convenience facilities for working with HAProxy.
# - Cameron Simpson <cs@cskk.id.au> 14jul2012
#

from __future__ import print_function
from collections import namedtuple
from getopt import GetoptError
import csv
import os.path
import sys
from types import SimpleNamespace
try:
  from urllib.request import urlopen
except ImportError:
  from urllib2 import urlopen
from cs.cmdutils import BaseCommand
from cs.logutils import warning, error
from cs.pfx import Pfx

USAGE = '''Usage: {cmd} stats host:port print [columns...]'''

def main(argv=None):
  ''' haproxy-tool main programme.
  '''
  return HAProxyToolCommand(argv).run()

class HAProxyToolCommand(BaseCommand):
  ''' haproxy-tool command line implementation.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} subcmd [subcmd-args...]'''

  def cmd_stats(argv):
    ''' Usage: {cmd} host:port print [columns...]
          Fetch the statistics from the haproxy at host:port.
    '''
    badopts = False
    if not argv:
      warning("missing host:port")
      badopts = True
    else:
      host_port = argv.pop(0)
      with Pfx("host:port %r", host_port):
        try:
          host, port = host_port.rsplit(':', 1)
          port = int(port)
        except ValueError as e:
          warning("invalid: %s", e)
          badopts = True
    cols = argv
    if badopts:
      raise GetoptError("invalid arguments")
    S = Stats(host, port)
    for row in S.csvdata():
      if cols:
        print(*[ getattr(row, col) for col in cols ])
      else:
        print(*row)

class Stats(SimpleNamespace):
  ''' An interface to the stats report of a running HAProxy instance.
  '''

  def __init__(self, host, port, basepath='/'):
    self.host = host
    self.port = port
    self.basepath = basepath

  @property
  def url(self):
    ''' The URL f the HTML stats data.
    '''
    return "http://%s:%d%s" % (self.host, self.port, self.basepath)

  @property
  def urlcsv(self):
    ''' The URL of the CSV stats data.
    '''
    return self.url + ';csv'

  def csvdata(self):
    ''' Generator yielding CSV data as namedtuples.
    '''
    url = self.urlcsv
    with Pfx(url):
      fp = urlopen(self.urlcsv)
      line1 = fp.readline()
      if not line1.endswith('\n'):
        raise ValueError("1: incomplete line, no newline")
      line1 = line1.rstrip()
      if not line1.startswith("# "):
        raise ValueError('1: expected header line commencing with "# ", got: ' + line1)
      cols = [ (col if len(col) else 'A') for col in line1[2:].split(',') ]
      rowifier = namedtuple('HAProxy_CSV_Row', cols)
      for row in csv.reader(fp):
        yield rowifier(*row)

def quote(s):
  ''' Quote a string for use in an HAProxy config file.
  '''
  return ''.join(quoted_char(c) for c in s)

def quoted_char(c):
  ''' Quote a character for use in a quoted string.
  '''
  qc = {
        '\t': '\\t',
        '\r': '\\r',
        '\n': '\\n',
        ' ': '\\ ',
        '#': '\\#',
        '\\': '\\\\',
       }.get(c)
  if not qc:
    if c.isalnum() or c in '.:/-+()?':
      qc = c
    else:
      o = ord(c)
      if o >= 128:
        qc = c
      else:
        qc = '\\x%02d' % o
  return qc

if __name__ == '__main__':
  sys.exit(main(sys.argv))
