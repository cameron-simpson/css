#!/bin/usr/binpython
#
# Python 3 specific implementations.
# Provided to separate non-portable syntax across python 2 and 3.
#   - Cameron Simpson <cs@cskk.id.au> 12nov2015
#

from builtins import bytes

DISTINFO = {
    'description':
    "python 3 specific support for cs.py3 module",
    'keywords': ["python2"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def raise3(exc_type, exc_value, exc_traceback):
  raise exc_value.with_traceback(exc_traceback)

def raise_from(e1, from_e2):
  raise e1 from from_e2

exec_code = exec

# file interface returning bytes from binary file
BytesFile = lambda fp: fp

joinbytes = b''.join
