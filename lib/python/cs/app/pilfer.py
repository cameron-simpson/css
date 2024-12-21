#!/usr/bin/env python3
#
# Web page utility.
#       - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

from collections import namedtuple
from configparser import ConfigParser
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import os.path
import errno
from getopt import GetoptError
import re
import shlex
from string import Formatter, whitespace
from subprocess import Popen, PIPE
import sys
from threading import Lock, RLock, Thread
from time import sleep
from typing import Iterable
from urllib.parse import quote, unquote
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree

from icontract import require
from typeguard import typechecked

from cs.app.flag import PolledFlags
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import promote
from cs.debug import ifdebug
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.fileutils import mkdirn
from cs.later import Later, RetryError
from cs.lex import (
    cutprefix, cutsuffix, get_dotted_identifier, get_identifier, is_identifier,
    get_other_chars, get_qstr
)
import cs.logutils
from cs.logutils import (debug, error, warning, exception, trace, D)
from cs.mappings import MappingChain, SeenSet
from cs.obj import copy as obj_copy
import cs.pfx
from cs.pfx import Pfx
from cs.pipeline import pipeline, StageType
from cs.py.func import funcname
from cs.py.modules import import_module_name
from cs.queues import NullQueue
from cs.resources import MultiOpenMixin, RunStateMixin, uses_runstate
from cs.seq import seq
from cs.threads import locked
from cs.urlutils import URL, NetrcHTTPPasswordMgr
from cs.x import X

# parallelism of jobs
DEFAULT_JOBS = 4

# default flag status probe
DEFAULT_FLAGS_CONJUNCTION = '!PILFER_DISABLE'

@typechecked
def urls(url, stdin=None, cmd=None) -> Iterable[str]:
  ''' Generator to yield input URLs.
  '''
  if stdin is None:
    stdin = sys.stdin
  if cmd is None:
    cmd = cs.pfx.cmd
  if url != '-':
    # literal URL supplied, deliver to pipeline
    yield url
  else:
    # read URLs from stdin
    try:
      do_prompt = stdin.isatty()
    except AttributeError:
      do_prompt = False
    if do_prompt:
      # interactively prompt for URLs, deliver to pipeline
      prompt = cmd + ".url> "
      while True:
        try:
          url = input(prompt)
        except EOFError:
          break
        else:
          yield url
    else:
      # read URLs from non-interactive stdin, deliver to pipeline
      lineno = 0
      for line in stdin:
        lineno += 1
        with Pfx("stdin:%d", lineno):
          if not line.endswith('\n'):
            raise ValueError("unexpected EOF - missing newline")
          url = line.strip()
          if not line or line.startswith('#'):
            debug("SKIP: %s", url)
            continue
          yield url

# TODO: recursion protection in action_map expansion
def argv_pipefuncs(argv, action_map, do_trace):
  ''' Process command line strings and return a corresponding list
      of actions to construct a Later.pipeline.
  '''
  # we reverse the list to make action expansion easier
  argv = list(argv)
  errors = []
  pipe_funcs = []
  while argv:
    action = argv.pop(0)
    # support commenting-out of individual actions
    if action.startswith('#'):
      continue
    # macro - prepend new actions
    func_name, offset = get_identifier(action)
    if func_name and func_name in action_map:
      expando = action_map[func_name]
      argv[:0] = expando
      continue
    if action == "per":
      # fork a new pipeline instance per item
      # terminate this pipeline with a function to spawn subpipelines
      # using the tail of the action list from this point
      if not argv:
        errors.append("no actions after %r" % (action,))
      else:
        tail_argv = list(argv)
        name = "%s:[%s]" % (action, ','.join(argv))
        pipespec = PipeSpec(name, argv)

        def per(P):
          with P:
            pipe = pipeline(
                pipespec.actions, inputs=(P,), name="%s(%s)" % (name, P)
            )
            with P.later.release():
              for P2 in pipe.outQ:
                yield P2

        pipe_funcs.append((StageType.ONE_TO_MANY, per))
      argv = []
      continue
    try:
      A = Action(action, do_trace)
    except ValueError as e:
      errors.append("bad action %r: %s" % (action, e))
    else:
      pipe_funcs.append(A)
  return pipe_funcs, errors

def notNone(v, name="value"):
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

@promote
def url_xml_find(U: URL, match):
  for found in url_io(U.xml_find_all, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

def new_dir(dirpath):
  ''' Create the directory `dirpath` or `dirpath-n` if `dirpath` exists.
      Return the path of the directory created.
  '''
  try:
    os.makedirs(dirpath)
  except OSError as e:
    if e.errno != errno.EEXIST:
      exception("os.makedirs(%r): %s", dirpath, e)
      raise
    dirpath = mkdirn(dirpath, '-')
  return dirpath

def has_exts(U, suffixes, case_sensitive=False):
  ''' Test if the .path component of a URL ends in one of a list of suffixes.
      Note that the .path component does not include the query_string.
  '''
  ok = False
  path = U.path
  if not path.endswith('/'):
    base = os.path.basename(path)
    if not case_sensitive:
      base = base.lower()
      suffixes = [sfx.lower() for sfx in suffixes]
    for sfx in suffixes:
      if base.endswith('.' + sfx):
        ok = True
        break
  return ok

def with_exts(urls, suffixes, case_sensitive=False):
  for U in urls:
    ok = False
    path = U.path
    if not path.endswith('/'):
      base = os.path.basename(path)
      if not case_sensitive:
        base = base.lower()
        suffixes = [sfx.lower() for sfx in suffixes]
      for sfx in suffixes:
        if base.endswith('.' + sfx):
          ok = True
          break
    if ok:
      yield U
    else:
      debug("with_exts: discard %s", U)

def url_delay(U, delay, *a):
  sleep(float(delay))
  return U

@promote
def url_query(U: URL, *a):
  if not a:
    return U.query
  qsmap = dict(
      [
          (qsp.split('=', 1) if '=' in qsp else (qsp, ''))
          for qsp in U.query.split('&')
      ]
  )
  return ','.join([unquote(qsmap.get(qparam, '')) for qparam in a])

def url_io(func, onerror, *a, **kw):
  ''' Call `func` and return its result.
      If it raises URLError or HTTPError, report the error and return `onerror`.
  '''
  debug("url_io(%s, %s, %s, %s)...", func, onerror, a, kw)
  try:
    return func(*a, **kw)
  except (URLError, HTTPError) as e:
    warning("%s", e)
    return onerror

@uses_runstate
def url_io_iter(it, *, runstate):
  ''' Generator that calls `it.next()` until `StopIteration`, yielding
      its values.
      If the call raises URLError or HTTPError, report the error
      instead of aborting.
  '''
  while True:
    runstate.raiseif("url_io_iter(it=%s): cancelled", it)
    try:
      item = next(it)
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield item

@promote
@typechecked
def url_hrefs(U: URL) -> Iterable[URL]:
  ''' Yield the HREFs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.hrefs(absolute=True))

@promote
@typechecked
def url_srcs(U: URL) -> Iterable[URL]:
  ''' Yield the SRCs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.srcs(absolute=True))

# actions that work on the whole list of in-play URLs
# these return Pilfers
many_to_many = {
    'sort':
    lambda Ps, key=lambda P: P._, reverse=False:
    sorted(Ps, key=key, reverse=reverse),
    'last':
    lambda Ps: Ps[-1:],
}

# actions that work on individual Pilfer instances, returning multiple strings
one_to_many = {
    'hrefs': lambda P: url_hrefs(P._),
    'srcs': lambda P: url_srcs(P._),
    'xml': lambda P, match: url_xml_find(P._, match),
    'xmltext': lambda P, match: ElementTree.XML(P._).findall(match),
}

# actions that work on individual Pilfer instances, returning single strings
one_to_one = {
    '..':
    lambda P: URL(P._, None).parent,
    'delay':
    lambda P, delay: (P._, sleep(float(delay)))[0],
    'domain':
    lambda P: URL(P._, None).domain,
    'hostname':
    lambda P: URL(P._, None).hostname,
    'print':
    lambda P, **kw: (P._, P.print_url_string(P._, **kw))[0],
    'query':
    lambda P, *a: url_query(P._, *a),
    'quote':
    lambda P: quote(P._),
    'unquote':
    lambda P: unquote(P._),
    'save':
    lambda P, *a, **kw: (P._, P.save_url(P._, *a, **kw))[0],
    'title':
    lambda P: P._.page_title,
    'type':
    lambda P: url_io(P._.content_type, ""),
    'xmlattr':
    lambda P, attr:
    [A for A in (ElementTree.XML(P._).get(attr),) if A is not None],
}

one_test = {
    'has_title':
    lambda P: P._.page_title is not None,
    'reject_re':
    lambda P, regexp: not regexp.search(P._),
    'same_domain':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.domain == P._.referer.domain,
    'same_hostname':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.hostname == P._.referer.hostname,
    'same_scheme':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.scheme == P._.referer.scheme,
    'select_re':
    lambda P, regexp: regexp.search(P._),
}

def pilferify11(func):
  ''' Decorator for 1-to-1 Pilfer=>nonPilfer functions to return a Pilfer.
  '''

  def pf(P, *a, **kw):
    return P.copy_with_vars(_=func(P, *a, **kw))

  pf.__name__ = "@pilferify11(%s)" % funcname(func)
  return pf

def pilferify1m(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  @promote
  def pf(P: Pilfer, *a, **kw):
    for value in func(P, *a, **kw):
      yield P.copy_with_vars(_=value)

  pf.__name__ = "@pilferify1m(%s)" % funcname(func)
  return pf

def pilferifymm(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    if not isinstance(Ps, list):
      Ps = list(Ps)
    if Ps:
      P0 = Ps[0]
      for value in func(Ps, *a, **kw):
        yield P0.copy_with_vars(_=value)

  pf.__name__ = "@pilferifymm(%s)" % funcname(func)
  return pf

def pilferifysel(func):
  ''' Decorator for selector Pilfer=>bool functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    for P in Ps:
      if func(P, *a, **kw):
        yield P

  pf.__name__ = "@pilferifysel(%s)" % funcname(func)
  return pf

def retriable(func):
  ''' A decorator for a function to probe the `Pilfer` flags
      and raise `RetryError` if unsatisfied.
  '''

  def retry_func(P, *a, **kw):
    ''' Call `func` after testing `P.test_flags()`.
    '''
    if not P.test_flags():
      raise RetryError('flag conjunction fails: %s' % (' '.join(P.flagnames)))
    return func(P, *a, **kw)

  retry_func.__name__ = 'retriable(%s)' % (funcname(func),)
  return retry_func

class PipeSpec(namedtuple('PipeSpec', 'name argv')):
  ''' A pipeline specification: a name and list of actions.
  '''

  @logexc
  def pipe_funcs(self, L, action_map, do_trace):
    ''' Compute a list of functions to implement a pipeline.

        It is important that this list is constructed anew for each
        new pipeline instance because many of the functions rely
        on closures to track state.
    '''
    with Pfx(self.name):
      pipe_funcs, errors = argv_pipefuncs(self.argv, L, action_map, do_trace)
    return pipe_funcs, errors
