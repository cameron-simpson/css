#!/usr/bin/env python3
#
# A web scraper. - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

''' Pilfer, a web scraping tool.
'''

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Internet :: Proxy Servers",
        "Topic :: Utilities",
    ],
    'entry_points': {
        'console_scripts': {
            'pilfer': 'cs.app.pilfer:main'
        },
    },
    'install_requires': [
        'cs.app.flag',
        'cs.cmdutils',
        'cs.context',
        'cs.debug',
        'cs.deco',
        'cs.env',
        'cs.excutils',
        'cs.fileutils>=atomic_filename',
        'cs.fs',
        'cs.hashutils',
        'cs.later',
        'cs.lex',
        'cs.logutils',
        'cs.mappings',
        'cs.naysync',
        'cs.obj',
        'cs.pfx>=pfx_call',
        'cs.pipeline',
        'cs.progress',
        'cs.py.func',
        'cs.py.modules',
        'cs.queues',
        'cs.resources',
        'cs.seq',
        'cs.threads',
        'cs.upd',
        'cs.urlutils',
        'icontract',
        'mitmproxy',
        'requests',
        'typeguard',
    ],
}

# parallelism of jobs
DEFAULT_JOBS = 4

# default flag status probe
DEFAULT_FLAGS_CONJUNCTION = '!PILFER_DISABLE'

DEFAULT_MITM_LISTEN_HOST = '127.0.0.1'
DEFAULT_MITM_LISTEN_PORT = 3131
