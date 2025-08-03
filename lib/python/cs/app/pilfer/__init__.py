#!/usr/bin/env python3
#
# A web scraper. - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

''' Pilfer, a web scraping tool.

    Presently this has two modes, a scraper and a proxy.
    The scraper is invoked as `pilfer from` *URL* *action-pipeline...*
    The proxy is invoked as `pilfer mitm` [`@`*IP*`:`*port*] *actions*...

    The scraper feeds URLs through a pipeline which processes the
    URLs, or data derived from them. Where it needs the URL's content
    (eg the `hrefs` pipeline element) it uses `requests.get`.

    The proxy uses the _excellent_ `mitmproxy` package in "upstream"
    mode to proxy requests and optionally process their content.
    One of my initial use cases is to operate a local cache to improve
    browser behaviour on my soggy internet link.
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
    'python_requires':
    '>=3.13',  # for configparser.UNNAMED_SECTION
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
        'cs.naysync>=amap_progressive',
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
        'bs4',
        'lxml',  # default bs4/BeautifulSoup parser
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
