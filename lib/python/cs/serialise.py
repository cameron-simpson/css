#!/usr/bin/python -tt
#
# Common serialisation functions.
# - Cameron Simpson <cs@cskk.id.au>
#

''' OBSOLETE: some serialising functions. Please use by cs.binary instead.

    Porting guide:
    * `get_bs` is now `BSUInt.parse_bytes`.
    * `put_bs` is now `BSUInt.transcribe_value`.
'''

import sys

__version__ = '20210316.1'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

print("cs.serialise: OBSOLETE, please use cs.binary instead", file=sys.stderr)
