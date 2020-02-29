#!/usr/bin/env python3
#
# Basic XML conveniences.
#   - Cameron Simpson <cs@cskk.id.au> 12feb2017
#

from __future__ import print_function
import sys
try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree

def pprint(xml, fp=None):
  ''' Directly derived from the suggestion at:
       http://stackoverflow.com/questions/749796/pretty-printing-xml-in-python
  '''
  if fp is None:
    fp = sys.stdout
  if isinstance(xml, str):
    xml = etree.fromstring(xml)
  print(etree.tostring(xml, pretty_print=True).decode(), file=fp)
