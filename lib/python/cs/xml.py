#!/usr/bin/python
#
# Basic XML conveniences. Initially just imports etree.
#   - Cameron Simpson <cs@zip.com.au> 12feb2017
#

try:
  from lxml import etree
  print("running with lxml.etree")
except ImportError:
  import xml.etree.ElementTree as etree
  print("running with ElementTree on Python 2.5+")
