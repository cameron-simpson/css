#!/usr/bin/env python3

''' My make programme, a parallel make tool with superior expression syntax.
'''

DISTINFO = {
    'description':
    "my make program; parallel make tool with superior expression syntax",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.debug',
        'cs.deco',
        'cs.excutils',
        'cs.inttypes',
        'cs.later',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.queues',
        'cs.result',
        'cs.threads',
    ],
    'entry_points': {
        'console_scripts': [
            'myke = cs.app.myke__main__:main',
        ],
    },
}

DEFAULT_MAKE_COMMAND = 'myke'
