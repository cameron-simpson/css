#!/usr/bin/python
#
# Convenience facilities for working with HAproxy.
#       - Cameron Simpson <cs@zip.com.au> 14jul2012
#

from collections import namedtuple
import csv
from urllib2 import urlopen
from cs.logutils import Pfx, D
from cs.misc import O

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

  @property
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
      D("cols = %r", cols)
      rowifier = namedtuple('HAproxy_CSV_Row', cols)
      for row in csv.reader(fp):
        yield rowifier(*row)

if __name__ == '__main__':
  import sys
  host, port = sys.argv[1:]
  port = int(port)
  for row in Stats(host, port).csvdata:
    print row
