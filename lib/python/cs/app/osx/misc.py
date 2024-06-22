#!/usr/bin/env python3

''' Assorted ad hoc MacOS/OSX things.
    They'd be in `cs.app.osx` directly except that I haven't looked
    into namespace packages yet.
'''

import platform

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Beta",
        "Environment :: MacOS X",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
    ],
}

# The MacOS version as a tuple of ints, eg `(14,5)` for MacOS Sonoma
macos_version = tuple(int(vs) for vs in platform.mac_ver()[0].split('.'))
