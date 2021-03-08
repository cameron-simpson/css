#!/usr/bin/python -tt
#
# Common serialisation functions.
# - Cameron Simpson <cs@cskk.id.au>
#

''' Some serialising functions, entirely obsoleted by cs.binary.

    Porting guide:
    * `get_bs` is now `BSUInt.parse_bytes`.
    * `put_bs` is now `BSUInt.transcribe_value`.
'''

from cs.binary import BSUInt, BSData, BSString

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.binary'],
}

raise RuntimeError("please just use cs.binary")
