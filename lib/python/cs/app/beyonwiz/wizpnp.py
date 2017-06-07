#!/usr/bin/python

from __future__ import print_function
from threading import RLock
from cs.threads import locked_property
from cs.urlutils import URL

class WizPnP(object):
  ''' Class to access a pre-T3 beyonwiz over HTTP.
  '''

  def __init__(self, host, port=None):
    if port is None:
      port = 49152
    self.host = host
    self.port = port
    self.base = URL('http://%s:%d/' % (host, port), None)
    self._lock = RLock()

  def test(self):
    print(self.tvdevicedesc_URL)
    print(self._tvdevicedesc_XML)
    print(self.specVersion)
    for label, path in self.index:
      print(label, path)

  def url(self, subpath):
    U = URL(self.base + subpath, self.base)
    print("url(%s) = %s" % (subpath, U))
    return U

  @locked_property
  def tvdevicedesc_URL(self):
    return self.url('tvdevicedesc.xml')

  @locked_property
  def _tvdevicedesc_XML(self):
    return XML(self.tvdevicedesc_URL.content)

  @locked_property
  def specVersion(self):
    xml = self._tvdevicedesc_XML
    specVersion = xml[0]
    major, minor = specVersion
    return int(major.text), int(minor.text)

  @locked_property
  def index_txt(self):
    return self.url('index.txt').content

  @locked_property
  def index(self):
    idx = []
    for line in self.index_txt.split('\n'):
      if line.endswith('\r'):
        line = line[:-1]
      if len(line) == 0:
        continue
      try:
        label, path = line.split('|', 1)
        print("label =", label, "path =", path)
      except ValueError:
        print("bad index line:", line)
      else:
        idx.append( (label, os.path.dirname(path)) )
    return idx

  def tvwiz_header(self, path):
    ''' Fetch the bytes of the tvwiz header file for the specified recording path.
    '''
    return self.url(os.path.join(path, 'header.tvwiz'))
