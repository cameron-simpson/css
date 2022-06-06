#!/usr/bin/env python3
#

''' A couple of trite XML conveniences:
    preferred `etree` import and a `pprint` function.
'''

from __future__ import print_function
import sys
try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree

__version__ = '20220606'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 5 - Production/Stable",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
    'extras_requires': {
        'lxml': ['lxml'],
    },
}

def pprint(xml, fp=None):
  ''' Directly derived from the suggestion at:
       http://stackoverflow.com/questions/749796/pretty-printing-xml-in-python
  '''
  if fp is None:
    fp = sys.stdout
  if isinstance(xml, str):
    xml = etree.fromstring(xml)
  print(etree.tostring(xml, pretty_print=True).decode(), file=fp)
