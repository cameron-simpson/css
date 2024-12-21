#!/usr/bin/env python3
#
# A web scraper. - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

''' Pilfer, a web scraping tool.
'''

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
  from xml.etree import ElementTree

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

class Pilfer(MultiOpenMixin, RunStateMixin):
  ''' State for the pilfer app.

      Notable attributes include:
      * `flush_print`: flush output after print(), default `False`.
      * `user_agent`: specify user-agent string, default `None`.
      * `user_vars`: mapping of user variables for arbitrary use.
  '''

  @uses_later
  def __init__(self, item=None, later: Later = None):
    self._name = 'Pilfer-%d' % (seq(),)
    self.user_vars = {'_': item, 'save_dir': '.'}
    self.flush_print = False
    self.do_trace = False
    self.flags = PolledFlags()
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    ##self._lock = Lock()
    self._lock = RLock()
    self.rcs = []  # chain of PilferRC libraries
    self.seensets = {}
    self.diversions_map = {}  # global mapping of names to divert: pipelines
    self.opener = build_opener()
    self.opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.opener.add_handler(HTTPCookieProcessor())
    self.later = later

  def __str__(self):
    return "%s[%s]" % (self._name, self._)

  __repr__ = __str__

  @contextmanager
  def startup_shutdown(self):
    with self.later:
      yield

  def copy(self, *a, **kw):
    ''' Convenience function to shallow copy this `Pilfer` with modifications.
    '''
    return obj_copy(self, *a, **kw)

  @property
  def defaults(self):
    ''' Mapping for default values formed by cascading PilferRCs.
    '''
    return MappingChain(mappings=[rc.defaults for rc in self.rcs])

  @property
  def _(self):
    ''' Shortcut to this Pilfer's user_vars['_'] entry - the current item value.
    '''
    return self.user_vars['_']

  @_.setter
  def _(self, value):
    if value is not None and not isinstance(value, str):
      raise TypeError("Pilfer._: expected string, received: %r" % (value,))
    self.user_vars['_'] = value

  @property
  def url(self):
    ''' `self._` as a `URL` object.
    '''
    return URL.promote(self._)

  def test_flags(self):
    ''' Evaluate the flags conjunction.

        Installs the tested names into the status dictionary as side effect.
        Note that it deliberately probes all flags instead of stopping
        at the first false condition.
    '''
    all_status = True
    flags = self.flags
    for flagname in self.flagnames:
      if flagname.startswith('!'):
        status = not flags.setdefault(flagname[1:], False)
      else:
        status = flags.setdefault(flagname[1:], False)
      if not status:
        all_status = False
    return all_status

  @locked
  def seenset(self, name):
    ''' Return the SeenSet implementing the named "seen" set.
    '''
    seen = self.seensets
    if name not in seen:
      backing_path = MappingChain(
          mappings=[rc.seen_backing_paths for rc in self.rcs]
      ).get(name)
      if backing_path is not None:
        backing_path = envsub(backing_path)
        if (not os.path.isabs(backing_path)
            and not backing_path.startswith('./')
            and not backing_path.startswith('../')):
          backing_basedir = self.defaults.get('seen_dir')
          if backing_basedir is not None:
            backing_basedir = envsub(backing_basedir)
            backing_path = os.path.join(backing_basedir, backing_path)
      seen[name] = SeenSet(name, backing_path)
    return seen[name]

  def seen(self, url, seenset='_'):
    ''' Test if the named `url` has been seen.
        The default seenset is named `'_'`.
    '''
    return url in self.seenset(seenset)

  def see(self, url, seenset='_'):
    ''' Mark a `url` as seen.
        The default seenset is named `'_'`.
    '''
    self.seenset(seenset).add(url)

  @property
  @locked
  def diversions(self):
    ''' The current list of named diversions.
    '''
    return list(self.diversions_map.values())

  @property
  @locked
  def diversion_names(self):
    ''' The current list of diversion names.
    '''
    return list(self.diversions_map.keys())

  @property
  @locked
  @logexc
  def open_diversion_names(self):
    ''' The current list of open named diversions.
    '''
    names = []
    for divname in self.diversion_names:
      div = self.diversion(divname)
      if not div.closed:
        names.append(divname)
    return names

  @logexc
  def quiesce_diversions(self):
    D("%s.quiesce_diversions...", self)
    while True:
      D("%s.quiesce_diversions: LOOP: pass over diversions...", self)
      for div in self.diversions:
        D("%s.quiesce_diversions: check %s ...", self, div)
        div.counter.check()
        D("%s.quiesce_diversions: quiesce %s ...", self, div)
        div.quiesce()
      D("%s.quiesce_diversions: now check that they are all quiet...", self)
      quiet = True
      for div in self.diversions:
        if div.counter:
          D("%s.quiesce_diversions: NOT QUIET: %s", self, div)
          quiet = False
          break
      if quiet:
        D("%s.quiesce_diversions: all quiet!", self)
        return

  @locked
  def diversion(self, pipe_name):
    ''' Return the diversion named `pipe_name`.
        A diversion embodies a pipeline of the specified name.
        There is only one of a given name in the shared state.
        They are instantiated at need.
    '''
    diversions = self.diversions_map
    if pipe_name not in diversions:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise KeyError(
            "no diversion named %r and no pipe specification found" %
            (pipe_name,)
        )
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise KeyError(
            "invalid pipe specification for diversion named %r" % (pipe_name,)
        )
      name = "DIVERSION:%s" % (pipe_name,)
      outQ = NullQueue(name=name, blocking=True)
      outQ.open()  # open outQ so it can be closed at the end of the pipeline
      div = pipeline(self.later, pipe_funcs, name=name, outQ=outQ)
      div.open()  # will be closed in main program shutdown
      diversions[pipe_name] = div
    return diversions[pipe_name]

  @logexc
  def pipe_through(self, pipe_name, inputs):
    ''' Create a new pipeline from the specification named `pipe_name`.
        It will collect items from the iterable `inputs`.
        `pipe_name` may be a PipeSpec.
    '''
    with Pfx("pipe spec %r" % (pipe_name,)):
      name = "pipe_through:%s" % (pipe_name,)
      return self.pipe_from_spec(pipe_name, inputs, name=name)

  def pipe_from_spec(self, pipe_name, name=None):
    ''' Create a new pipeline from the specification named `pipe_name`.
    '''
    if isinstance(pipe_name, PipeSpec):
      spec = pipe_name
      pipe_name = str(spec)
    else:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise ValueError("no pipe specification named %r" % (pipe_name,))
    if name is None:
      name = "pipe_from_spec:%s" % (spec,)
    with Pfx(spec):
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise ValueError("invalid pipe specification")
    return pipeline(self.later, pipe_funcs, name=name, inputs=inputs)

  def _rc_pipespecs(self):
    for rc in self.rcs:
      yield rc.pipe_specs

  @property
  def pipes(self):
    return MappingChain(get_mappings=self._rc_pipespecs)

  def _rc_action_maps(self):
    for rc in self.rcs:
      yield rc.action_map

  @property
  def action_map(self):
    return MappingChain(get_mappings=self._rc_action_maps)

  def _print(self, *a, **kw):
    file = kw.pop('file', None)
    if kw:
      raise ValueError("unexpected kwargs %r" % (kw,))
    with self._print_lock:
      if file is None:
        file = self._print_to if self._print_to else sys.stdout
      print(*a, file=file)
      if self.flush_print:
        file.flush()

  ##@require(lambda kw: all(isinstance(v, str) for v in kw))
  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    self.user_vars.update(kw)

  def copy_with_vars(self, **kw):
    ''' Make a copy of `self` with copied .user_vars, update the
        vars and return the copied Pilfer.
    '''
    P = self.copy('user_vars')
    P.set_user_vars(**kw)
    return P

  def print_url_string(self, U, **kw):
    ''' Print a string using approved URL attributes as the format dictionary.
        See Pilfer.format_string.
    '''
    print_string = kw.pop('string', '{_}')
    print_string = self.format_string(print_string, U)
    file = kw.pop('file', self._print_to)
    if kw:
      warning("print_url_string: unexpected keyword arguments: %r", kw)
    self._print(print_string, file=file)

  @property
  def save_dir(self):
    return self.user_vars.get('save_dir', '.')

  @promote
  def save_url(self, U: URL, saveas=None, dir=None, overwrite=False, **kw):
    ''' Save the contents of the URL `U`.
    '''
    debug(
        "save_url(U=%r, saveas=%r, dir=%s, overwrite=%r, kw=%r)...", U, saveas,
        dir, overwrite, kw
    )
    with Pfx("save_url(%s)", U):
      save_dir = self.save_dir
      if saveas is None:
        saveas = os.path.join(save_dir, U.basename)
        if saveas.endswith('/'):
          saveas += 'index.html'
      if saveas == '-':
        outfd = os.dup(sys.stdout.fileno())
        content = U.content
        with self._lock:
          with os.fdopen(outfd, 'wb') as outfp:
            outfp.write(content)
      else:
        with Pfx(saveas):
          if not overwrite and os.path.exists(saveas):
            warning("file exists, not saving")
          else:
            content = U.content
            if content is None:
              error("content unavailable")
            else:
              try:
                with open(saveas, "wb") as savefp:
                  savefp.write(content)
              except Exception:
                exception("save fails")
            # discard contents, releasing memory
            U.flush()

  def import_module_func(self, module_name, func_name):
    with LogExceptions():
      pylib = [
          path
          for path in envsub(self.defaults.get('pythonpath', '')).split(':')
          if path
      ]
      return import_module_name(module_name, func_name, pylib, self._lock)

  def format_string(self, s, U):
    ''' Format a string using the URL `U` as context.
        `U` will be promoted to an URL if necessary.
    '''
    return FormatMapping(self, U=U).format(s)

  def set_user_var(self, k, value, U, raw=False):
    if not raw:
      value = self.format_string(value, U)
    FormatMapping(self)[k] = value

  # Note: this method is _last_ because otherwise it it shadows the
  # @promote decorator, used on earlier methods.
  @classmethod
  def promote(cls, P):
    '''Promote anything to a `Pilfer`.
    '''
    if not isinstance(P, cls):
      P = cls(P)
    return P
