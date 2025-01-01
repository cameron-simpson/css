#!/usr/bin/env python3
#
# A web scraper. - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

''' Pilfer, a web scraping tool.
'''

from contextlib import contextmanager
import os
import os.path
import sys
from threading import Lock, RLock
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor

from cs.app.flag import PolledFlags
from cs.deco import default_params, promote
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.later import Later, uses_later
from cs.lex import r
from cs.logutils import (debug, error, warning, exception, D)
from cs.mappings import MappingChain, SeenSet
from cs.obj import copy as obj_copy
from cs.pfx import Pfx
from cs.pipeline import pipeline
from cs.py.modules import import_module_name
from cs.queues import NullQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import seq
from cs.threads import locked, HasThreadState, ThreadState
from cs.urlutils import URL, NetrcHTTPPasswordMgr

from .pilfer import Pilfer
from .pipelines import PipeLineSpec

from cs.debug import trace, X, r, s

# parallelism of jobs
DEFAULT_JOBS = 4

# default flag status probe
DEFAULT_FLAGS_CONJUNCTION = '!PILFER_DISABLE'
