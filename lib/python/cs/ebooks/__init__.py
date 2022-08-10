#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, Kindle, Mobi.

    These form the basis of my personal Kindle and Calibre workflow.
'''

__version__ = '20220805-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.osx.plist',
        'cs.cmdutils',
        'cs.context',
        'cs.deco',
        'cs.fileutils',
        'cs.fs',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.numeric',
        'cs.obj',
        'cs.pfx',
        'cs.progress',
        'cs.psutils',
        'cs.resources',
        'cs.seq',
        'cs.sqlalchemy_utils',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'cs.upd',
        'icontract',
        'mobi',
        'sqlalchemy',
        'typeguard',
    ],
}
