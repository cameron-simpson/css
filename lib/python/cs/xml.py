#!/usr/bin/env python
#

''' Trite XML conveniences.
'''

from __future__ import print_function
import sys
try:
  import lxml.etree as etree
except ImportError:
  try:
    import xml.etree.cElementTree as etree
  except ImportError:
    import xml.etree.ElementTree as etree

def pprint(xml, fp=None):
  ''' Pretty print an XML object.

      Directly derived from the suggestion at:
      http://stackoverflow.com/questions/749796/pretty-printing-xml-in-python
  '''
  if fp is None:
    fp = sys.stdout
  if isinstance(xml, str):
    xml = etree.fromstring(xml)
  print(etree.tostring(xml, pretty_print=True).decode(), file=fp)
