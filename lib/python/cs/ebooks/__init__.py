#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, Kindle, Mobi.
'''

import os
from os.path import (
    expanduser,
    isabs as isabspath,
    join as joinpath,
    realpath,
)
from threading import Lock
from typing import Optional

from icontract import require
from typeguard import typechecked

from cs.obj import SingletonMixin

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
