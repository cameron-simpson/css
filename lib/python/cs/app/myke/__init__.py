#!/usr/bin/env python3

''' My make programme, a parallel make tool with superior expression syntax.
'''

DISTINFO = {
    'description':
    "my make program; parallel make tool with superior expression syntax",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.debug', 'cs.logutils', 'cs.inttypes', 'cs.threads', 'cs.later',
        'cs.queues', 'cs.result', 'cs.lex'
    ],
}

DEFAULT_MAKE_COMMAND = 'myke'
