#!/usr/bin/env python3

''' Some 3d modelling code, using panda3d for rendering (and pandas for computation!)
'''

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.panda3dutils',
        'icontract',
        'numpy',
        'panda3d',
        'pandas',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': [],
    },
    'extras_requires': {},
}
