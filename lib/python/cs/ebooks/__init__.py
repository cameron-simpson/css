#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, Kindle, Mobi.

    These form the basis of my personal Kindle and Calibre workflow.
'''

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.oxs.plist',
        'cs.cmdutils',
        'cs.context',
        'cs.deco',
        'cs.fileutils',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.resources',
        'cs.sqlalchemy_utils',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'mobi',
    ],
}

def intif(f: float):
  ''' Return `int(f)` if that equals `f`, otherwise `f`.
  '''
  i = int(f)
  return i if i == f else f
