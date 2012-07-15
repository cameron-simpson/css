#!/usr/bin/python
#
# Convenience facilities for working with HAproxy.
#       - Cameron Simpson <cs@zip.com.au> 14jul2012
#

from __future__ import print_function
from collections import namedtuple
import csv
import os.path
import sys
from urllib2 import urlopen
from cs.logutils import setup_logging, Pfx, D, warning, error
from cs.misc import O

USAGE = '''Usage: {cmd} stats host:port print [columns...]'''

def main(argv):
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)
  usage = USAGE.format(cmd=cmd)
  xit = 1

  badopts = False

  if len(argv) == 0:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == "stats":
        xit = op_stats(argv)
      else:
        error("unrecognised op")
        badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    xit = 2

  return xit

def op_stats(argv):
  argv = list(argv)
  if len(argv) == 0:
    error("missing host:port")
    return 2

  try:
    host, port = argv.pop(0).rsplit(':', 1)
    port = int(port)
  except ValueError, e:
    error("invalid host:port: %s" % (e,))
    return 2

  if len(argv) == 0:
    error("missing subop")
    return 2
  op = argv.pop(0)
  cols = argv

  S = Stats(host, port)
  for row in S.csvdata():
    if cols:
      print(*[ getattr(row, col) for col in cols ])
    else:
      print(*row)

  return 0

class Stats(O):
  ''' An interface to the stats report of a running HAproxy instance.
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
      rowifier = namedtuple('HAproxy_CSV_Row', cols)
      for row in csv.reader(fp):
        yield rowifier(*row)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
