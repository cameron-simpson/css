#!/usr/bin/python

''' Access a pre-T3 Beyonwiz PVR over HTTP.
'''

from __future__ import print_function
import os
from threading import RLock
import xml.etree.ElementTree as etree
from cs.threads import locked_property
from cs.urlutils import URL

WIZ_HTTP_PORT = 49152

class WizPnP(object):
  ''' Class to access a pre-T3 beyonwiz over HTTP.
  '''

  def __init__(self, host, port=None):
    if port is None:
      port = WIZ_HTTP_PORT
    self.host = host
    self.port = port
    self.base = URL('http://%s:%d/' % (host, port), None)
    self._lock = RLock()

  def test(self):
    ''' Simple exercises.
    '''
    print(self.tvdevicedesc_URL)
    print(self._tvdevicedesc_XML)
    print(self.specVersion)
    for label, path in self.index:
      print(label, path)

  def url(self, subpath):
    ''' Construct the URL from a component subpath.
    '''
    U = URL(self.base + subpath, self.base)
    print("url(%s) = %s" % (subpath, U))
    return U

  @locked_property
  def tvdevicedesc_URL(self):
    ''' The tvdevicedesc.xml URL.
    '''
    return self.url('tvdevicedesc.xml')

  @locked_property
  def _tvdevicedesc_XML(self):
    ''' The decoded XML from the tvdevicedesc.xml URL.
    '''
    return etree.fromstring(self.tvdevicedesc_URL.content)

  @locked_property
  def specVersion(self):
    ''' The specification major and minor numbers from the tvdevicedesc.xml URL.
    '''
    xml = self._tvdevicedesc_XML
    specVersion = xml[0]
    major, minor = specVersion
    return int(major.text), int(minor.text)

  @locked_property
  def index_txt(self):
    ''' The text from the index.txt URL.
    '''
    return self.url('index.txt').content

  @locked_property
  def index(self):
    ''' Parse and print the contents of the index.txt URL.
    '''
    idx = []
    for line in self.index_txt.split('\n'):
      if line.endswith('\r'):
        line = line[:-1]
      if not line:
        continue
      try:
        label, path = line.split('|', 1)
        print("label =", label, "path =", path)
      except ValueError:
        print("bad index line:", line)
      else:
        idx.append((label, os.path.dirname(path)))
    return idx

  def tvwiz_header(self, path):
    ''' Fetch the bytes of the tvwiz header file for the specified recording path.
    '''
    return self.url(os.path.join(path, 'header.tvwiz'))
