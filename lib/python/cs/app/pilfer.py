#!/usr/bin/env python
#
# Web page utility.
#       - Cameron Simpson <cs@zip.com.au> 07jul2010
#

from __future__ import with_statement, print_function
import sys
import os
import os.path
import errno
import shlex
from collections import defaultdict
from copy import copy
from functools import partial
from itertools import chain
import re
if sys.hexversion < 0x02060000: from sets import Set as set
from getopt import getopt, GetoptError
from string import Formatter
from subprocess import Popen, PIPE
from time import sleep
from threading import Lock, Thread
from urllib import quote, unquote
from urllib2 import HTTPError, URLError, build_opener, HTTPBasicAuthHandler
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from cs.debug import thread_dump
from cs.env import envsub
from cs.excutils import noexc, noexc_gen, LogExceptions
from cs.fileutils import file_property, mkdirn
from cs.later import Later, FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_SELECTOR, FUNC_MANY_TO_MANY
from cs.lex import get_identifier, get_other_chars
from cs.logutils import setup_logging, logTo, Pfx, debug, error, warning, exception, trace, pfx_iter, D
from cs.mappings import MappingChain, SeenSet
from cs.queues import IterableQueue, NullQueue, NullQ
from cs.seq import seq
from cs.threads import locked, locked_property
from cs.urlutils import URL, isURL, NetrcHTTPPasswordMgr
from cs.obj import O
from cs.py3 import input, ConfigParser

if os.environ.get('DEBUG', ''):
  def X(tag, *a):
    D("TRACE: "+tag, *a)
else:
  def X(*a):
    pass

DEFAULT_JOBS = 4

usage = '''Usage: %s [options...] op [args...]
  %s url URL actions...
      URL may be "-" to read URLs from standard input.
  Options:
    -c config
        Load rc file.
    -j jobs
        How many jobs (URL fetches, minor computations) to run at a time.
        Default: %d
    -q  Quiet. Don't recite surviving URLs at the end.
    -u  Unbuffered. Flush print actions as they occur.'''

def main(argv):
  argv = list(argv)
  xit = 0
  argv0 = argv.pop(0)
  cmd = os.path.basename(argv0)
  setup_logging(cmd)
  logTo('.pilfer.log')

  P = Pilfer()
  quiet = False
  jobs = DEFAULT_JOBS

  badopts = False

  try:
    opts, argv = getopt(argv, 'c:j:qu')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    with Pfx("%s", opt):
      if opt == '-c':
        P.rcs[0:0] = load_pilferrcs(val)
      elif opt == '-j':
        jobs = int(val)
      elif opt == '-q':
        quiet = True
      elif opt == '-u':
        P.flush_print = True
      else:
        raise NotImplementedError("unimplemented option")

  dflt_rc = os.environ.get('PILFERRC')
  if dflt_rc is None:
    dflt_rc = envsub('$HOME/.pilferrc')
  if dflt_rc:
    with Pfx("$PILFERRC: %s", dflt_rc):
      P.rcs.extend(load_pilferrcs(dflt_rc))

  if not argv:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    if op.startswith('http://') or op.startswith('https://') :
      # push the URL back and infer missing "url" op word
      argv.insert(0, op)
      op ='url'
    with Pfx(op):
      if op == 'url':
        if not argv:
          error("missing URL")
          badopts = True
        else:
          url = argv.pop(0)

          # load any named pipeline definitions on the command line
          # these are of the form: pipename:{ action... }
          rc = PilferRC(None)
          P.rcs.insert(0, rc)
          while len(argv) and argv[0].endswith(':{'):
            openarg = argv[0]
            with Pfx(openarg):
              spec, argv2, errors = get_pipeline_spec(argv)
              argv = argv2
              if spec is None:
                errors.insert(0, "invalid pipe opening token: %r" % (openarg,))
              if errors:
                badopts = True
                for err in errors:
                  error(err)
              else:
                try:
                  rc.add_pipespec(spec)
                except KeyError as e:
                  error("add pipe: %s", e)
                  badopts = True

          # gather up the remaining definition as the running pipeline
          pipe_funcs, errors = argv_pipefuncs(argv, P.action_map)

          # report accumulated errors and set badopts
          if errors:
            for err in errors:
              error(err)
            badopts = True
          if not badopts:
            with Later(jobs) as L:
              P.later = L
              # commence the main pipeline by converting strings to URL objects
              # and associating the initial Pilfer object as scope
              def add_scope(U):
                return P, URL(U, None, scope=P)
              pipe_funcs.insert(0, (FUNC_ONE_TO_ONE, add_scope))
              # construct the pipeline
              inQ, outQ = L.pipeline(pipe_funcs, outQ=NullQueue(blocking=True, open=True))
              if url != '-':
                # literal URL supplied, deliver to pipeline
                inQ.put(url)
              else:
                # read URLs from stdin
                try:
                  do_prompt = sys.stdin.isatty()
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
                      inQ.put(url)
                else:
                  # read URLs from non-interactive stdin, deliver to pipeline
                  lineno = 0
                  for line in sys.stdin:
                    lineno += 1
                    with Pfx("stdin:%d", lineno):
                      if not line.endswith('\n'):
                        raise ValueError("unexpected EOF - missing newline")
                      line = line.strip()
                      if not line or line.startswith('#'):
                        debug("SKIP: %s", line)
                        continue
                      inQ.put(line)
              # indicate end of input
              inQ.close()
              # await processing of output
              with Pfx("main pipeline"):
                for item in outQ:
                  warning("finalisation collected %r", item)
              # await completion of other diversions also
              for pipe_name, div in P.diversions.items():
                with Pfx("divert:%s", pipe_name):
                  outQ = div.outQ
                  with Pfx(str(outQ)):
                    for item in outQ:
                      warning("finalisation collected %r", item)
            X("L.wait()...")
            L.wait()
            X("L.wait() COMPLETE")
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    print(usage % (cmd, cmd, DEFAULT_JOBS), file=sys.stderr)
    xit = 2

  return xit

# TODO: recursion protection in action_map expansion
def argv_pipefuncs(argv, action_map):
  ''' Process command line strings and return a corresponding list
      of functions to construct a Later.pipeline.
  '''
  rargv = list(reversed(argv))
  errors = []
  pipe_funcs = []
  while rargv:
    action = rargv.pop()
    func, offset = get_identifier(action)
    if func and func in action_map:
      expando = action_map[func]
      rargv.extend(reversed(expando))
      continue
    try:
      func_sig, function = action_func(action)
    except ValueError as e:
      errors.append(str(e))
    else:
      pipe_funcs.append( (func_sig, function) )
  return pipe_funcs, errors

def get_pipeline_spec(argv):
  ''' Parse a leading pipeline specification from the list of arguments `argv`.
      A pipeline specification is specified by a leading argument
      of the form "pipe_name:{", following arguments definition
      functions for the pipeline, and a terminating argument of the
      form "}".

      Return `(spec, argv2, errors)` where `spec` is a PipeSpec
      embodying the specification, `argv2` is the list of arguments
      after the specification and `errors` is a list of error
      messages encountered parsing the function arguments.

      If the leading argument does not commence a function specification
      then `spec` will be None and `argv2` will be `argv`.
  '''
  errors = []
  pipe_name = None
  spec = None
  if not argv:
    # no arguments, no spec
    argv2 = argv
  else:
    arg = argv[0]
    if not arg.endswith(':{'):
      # not a start-of-spec
      argv2 = argv
    else:
      pipe_name, offset = get_identifier(arg)
      if not pipe_name or offset != len(arg)-2:
        # still not a start-of-spec
        argv2 = argv
      else:
        with Pfx(arg):
          # started with "foo:{"; gather spec until "}"
          for i in range(1, len(argv)):
            if argv[i] == '}':
              spec = PipeSpec(pipe_name, argv[1:i])
              argv2 = argv[i+1:]
              break
          if spec is None:
            errors.append('%s: missing closing "}"' % (arg,))
            argv2 = argv[1:]
  return spec, argv2, errors

def notNone(v, name="value"):
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

def url_xml_find(U, match):
  for found in url_io(URL(U, None).xmlFindall, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

def unique(items, seen=None):
  ''' A generator that yields unseen items progressively, as opposed
      to just stuffing them all into a set and returning the set.
  '''
  if seen is None:
    seen = set()
  for I in items:
    if I not in seen:
      yield I
      seen.add(I)

class PilferCommon(O):
  ''' Common state associated with all Pilfers.
      Pipeline definitions, seen sets, etc.
  '''

  def __init__(self):
    self._lock = Lock()
    O.__init__(self)
    self.later = None
    self.seen = defaultdict(set)
    self.rcs = []               # chain of PilferRC libraries
    self.diversions = {}        # global mapping of names to divert: pipelines
    self.opener = build_opener()
    self.opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))

  @property
  def defaults(self):
    return MappingChain(mappings=[ rc.defaults for rc in self.rcs ])

  @locked
  def seenset(self, name):
    ''' Return the SeenSet implementing the named "seen" set.
    '''
    seen = self.seen
    if name not in seen:
      backing_file = MappingChain(mappings=[ rc.seen_backing_files for rc in self.rcs ]).get(name)
      if backing_file is not None:
        backing_file = envsub(backing_file)
        if ( not os.path.isabs(backing_file)
         and not backing_file.startswith('./')
         and not backing_file.startswith('../')
           ):
          backing_basedir = self.defaults.get('seen_dir')
          if backing_basedir is not None:
            backing_basedir = envsub(backing_basedir)
            backing_file = os.path.join(backing_basedir, backing_file)
      seen[name] = SeenSet(name, backing_file)
    return seen[name]

class Pilfer(O):
  ''' State for the pilfer app.
      Notable attribute include:
        .flush_print    Flush output after print(), default False.
        .user_agent     Specify user-agent string, default None.
        .user_vars      Mapping of user variables for arbitrary use.
  '''

  def __init__(self, **kw):
    self._name = 'Pilfer-%d' % (seq(),)
    self._lock = Lock()
    self.flush_print = False
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    self.user_vars = { 'save_dir': '.' }
    self._urlsfile = None
    O.__init__(self, **kw)
    if not hasattr(self, '_shared'):
      self._shared = PilferCommon()                  # common state - seen URLs, etc

  def __str__(self):
    return self._name
  __repr__ = __str__

  def __copy__(self):
    ''' Copy this Pilfer state item, preserving shared state.
    '''
    return Pilfer(user_vars=dict(self.user_vars),
                  _shared=self._shared,
                 )

  @property
  def defaults(self):
    return self._shared.defaults

  def seen(self, url, seenset='_'):
    return url in self._shared.seenset(seenset)

  def see(self, url, seenset='_'):
    self._shared.seenset(seenset).add(url)

  @property
  def later(self):
    return self._shared.later

  @later.setter
  def later(self, L):
    self._shared.later = L

  @property
  def diversions(self):
    return self._shared.diversions

  @locked
  def diversion(self, pipe_name):
    ''' Return the diversion named `pipe_name`.
        A diversion enbodies a pipeline of the specified name.
        There is only one of a given name in the shared state.
        They are instantiated at need.
    '''
    diversions = self.diversions
    if pipe_name not in diversions:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise KeyError("no diversion named %r and no pipe specification found" % (pipe_name,))
      pipe_funcs, errors = spec.pipe_funcs(self.action_map)
      if errors:
        for err in errors:
          error(err)
        raise KeyError("invalid pipe specification for diversion named %r" % (pipe_name,))
      inQ, outQ = self.later.pipeline(pipe_funcs, outQ=NullQueue(blocking=True))
      diversions[pipe_name] = O(name=pipe_name, inQ=inQ, outQ=outQ)
    return diversions[pipe_name]

  def pipe_through(self, pipe_name, inputs):
    ''' Create a new cs.later.Later.pipeline from the specification named `pipe_name`.
        It will collect items from the iterable `inputs`.
        Return the output Queue from which to get results.
    '''
    spec = self.pipes.get(pipe_name)
    if spec is None:
      raise KeyError("no pipe specification named %r" % (pipe_name,))
    pipe_funcs, errors = spec.pipe_funcs(self.action_map)
    if errors:
      for err in errors:
        error(err)
      raise KeyError("invalid pipe specification for diversion named %r" % (pipe_name,))
    inQ, outQ = self.later.pipeline(pipe_funcs, inputs=inputs)
    return outQ

  @property
  def rcs(self):
    return self._shared.rcs

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

  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    self.user_vars.update(kw)

  def print_url_string(self, U, **kw):
    ''' Print a string using approved URL attributes as the format dictionary.
        See Pilfer.format_string.
    '''
    print_string = kw.pop('string', '{url}')
    print_string = self.format_string(print_string, U)
    file = kw.pop('file', self._print_to)
    if kw:
      warning("print_url_string: unexpected keyword arguments: %r", kw)
    self._print(print_string, file=file)

  @property
  def save_dir(self):
    return self.user_vars.get('save_dir', '.')

  def save_url(self, U, saveas=None, dir=None, overwrite=False, **kw):
    ''' Save the contents of the URL `U`.
    '''
    debug("save_url(U=%r, saveas=%r, dir=%s, overwrite=%r, kw=%r)...", U, saveas, dir, overwrite, kw)
    with Pfx("save_url(%s)", U):
      save_dir = self.save_dir
      if saveas is None:
        saveas = os.path.join(save_dir, U.basename)
        if saveas.endswith('/'):
          saveas += 'index.html'
      if saveas == '-':
        sys.stdout.write(U.content)
        sys.stdout.flush()
      else:
        with Pfx(saveas):
          if not overwrite and os.path.exists(saveas):
            warning("file exists, not saving")
          else:
            content = U.content
            try:
              with open(saveas, "wb") as savefp:
                savefp.write(content)
            except:
              exception("save fails")

  def format_string(self, s, U):
    ''' Format a string using the URL `U` as context.
        `U` will be promoted to an URL if necessary.
    '''
    return FormatMapping(self, U).format(s)

  def set_user_var(self, k, value, U, raw=False):
    if not raw:
      value = self.format_string(value, U)
    FormatMapping(self, U)[k] = value

  def import_module_func(self, module_name, func_name):
    with LogExceptions():
      import importlib
      pylib = [ path for path in self.defaults.get('pythonpath', '').split(':') if path ]
      with self._lock:
        osyspath = sys.path
        if pylib:
          sys.path = [ envsub(path) for path in pylib ] + sys.path
        try:
          M = importlib.import_module(module_name)
        except ImportError as e:
          exception("%s", e)
          M = None
        if pylib:
          sys.path = osyspath
      if M is not None:
        try:
          return getattr(M, func_name)
        except AttributeError as e:
          error("%s: no entry named %r: %s", module_name, func_name, e)
      return None

class FormatMapping(object):
  ''' A mapping object to set or fetch user variables or URL attributes.
      Various URL attributes are known, and may not be assigned to.
      This mapping is used with str.format to fill in {value}s.
  '''

  _approved = (
                'archives',
                'basename',
                'dirname',
                'domain',
                'hrefs',
                'hostname',
                'parent',
                'path',
                'referer',
                'srcs',
                'page_title',
                'url',
              )

  def __init__(self, P, U):
    self.pilfer = P
    self.url = URL(U, None)

  def keys(self):
    return set(self._approved) + set(self.pilfer.user_vars.keys())

  def __getitem__(self, k):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if k in self._approved:
        if k == 'url':
          return url
        try:
          return getattr(url, k)
        except AttributeError as e:
          raise KeyError("no such attribute: .%s (%s)" % (k, e))
      else:
        return P.user_vars[k]

  def get(self, k, default):
    try:
      return self[k]
    except KeyError:
      return default

  def __setitem__(self, k, value):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if k in self._approved:
        raise KeyError("it is forbidden to assign to attribute .%s" % (k,))
      else:
        P.user_vars[k] = value

  def format(self, s):
    ''' Format the string `s` using this mapping.
    '''
    return Formatter().vformat(s, (), self)

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
      suffixes = [ sfx.lower() for sfx in suffixes ]
    for sfx in suffixes:
      if base.endswith('.'+sfx):
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
        suffixes = [ sfx.lower() for sfx in suffixes ]
      for sfx in suffixes:
        if base.endswith('.'+sfx):
          ok = True
          break
    if ok:
      yield U
    else:
      debug("with_exts: discard %s", U)

def substitute( (P, src), regexp, replacement, replace_all):
  ''' Perform a regexp substitution on `src`.
      `replacement` is a format string for the replacement text
      using the str.format method.
      The matched groups from the regexp take the positional arguments 1..n,
      with 0 used for the whole matched string.
      The keyword arguments consist of '_' for the whole matched string
      and any named groups.
  '''
  debug("SUBSTITUTE: src=%r, regexp=%r, replacement=%r, replace_all=%s)...",
        src, regexp.pattern, replacement, replace_all)
  strs = []
  sofar = 0
  for m in regexp.finditer(src):
    repl_args = [ m.group(0) ] + list(m.groups())
    repl_kw = { '_': m.group(0) }
    repl_kw.update(m.groupdict())
    strs.append(src[sofar:m.start()])
    strs.append(replacement.format(*repl_args, **repl_kw))
    sofar = m.end()
    if not replace_all:
      break
  strs.append(src[sofar:])
  result = ''.join(strs)
  debug("SUBSTITUTE: src=%r, result=%r", src, result)
  if isURL(src):
    result = URL(result, src.referer)
  return result

def url_delay(U, delay, *a):
  sleep(float(delay))
  return U

def url_query(U, *a):
  U = URL(U, None)
  if not a:
    return U.query
  qsmap = dict( [ ( qsp.split('=', 1) if '=' in qsp else (qsp, '') ) for qsp in U.query.split('&') ] )
  return ','.join( [ unquote(qsmap.get(qparam, '')) for qparam in a ] )

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

def url_io_iter(I):
  ''' Generator that calls `I.next()` until StopIteration, yielding
      its values.
      If the call raises URLError or HTTPError, report the error
      instead of aborting.
  '''
  while True:
    try:
      item = I.next()
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield item

def url_hrefs(U):
  ''' Yield the HREFs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(URL(U, None).hrefs(absolute=True))

def url_srcs(U):
  ''' Yield the SRCs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(URL(U, None).srcs(absolute=True))

def grok(module_name, func_name, (P, U), *a, **kw):
  ''' Grok performs a user-specified analysis on the URL U.
      Import `func_name` from module `module_name`.
      Call `func_name( (P, U), *a, **kw ).
      Receive a mapping of variable names to values in return,
      which is applied to P.set_user_vars().
      Returns U, as this is a one-to-one function.
  '''
  with Pfx("grok: call %s.%s( (P=%r, U=%r), *a=%r, **kw=%r )...", module_name, func_name, P, U, a, kw):
    mfunc = P.import_module_func(module_name, func_name)
    if mfunc is None:
      error("import fails")
    else:
      try:
        var_mapping = mfunc((P, U), *a, **kw)
      except Exception as e:
        exception("call")
      else:
        P.set_user_vars(**var_mapping)
    return U

def grokall(module_name, func_name, items, *a, **kw):
  ''' Grokall performs a user-specified analysis on the items.
      Import `func_name` from module `module_name`.
      Call `func_name( items, *a, **kw ).
      Receive a mapping of variable names to values in return,
      which is applied to each item[0] via .set_user_vars().
  '''
  with Pfx("grokall: call %s.%s( items=%r, *a=%r, **kw=%r )...", module_name, func_name, P, U, a, kw):
    mfunc = P.import_module_func(module_name, func_name)
    if mfunc is None:
      error("import fails")
    else:
      try:
        var_mapping = mfunc(items, *a, **kw)
      except Exception as e:
        exception("call")
      else:
        for item in items:
          item[0].set_user_vars(**var_mapping)
          yield item

def _test_grokfunc( (P, U), *a, **kw ):
  v={ 'grok1': 'grok1value',
      'grok2': 'grok2value',
    }
  return v

# actions that work on the whole list of in-play URLs
many_to_many = {
      'sort':         lambda Ps, Us, *a, **kw: sorted(Us, *a, **kw),
      'unique':       lambda Ps, Us: unique(Us),
      'first':        lambda Ps, Us: Us[:1],
      'last':         lambda Ps, Us: Us[-1:],
    }

one_to_many = {
      'hrefs':        lambda (P, U): url_hrefs(U),
      'srcs':         lambda (P, U): url_srcs(U),
      'xml':          lambda (P, U), match: url_xml_find(U, match),
      'xmltext':      lambda (P, U), match: XML(U).findall(match),
    }

# actions that work on individual URLs
one_to_one = {
      '..':           lambda (P, U): URL(U, None).parent,
      'delay':        lambda (P, U), delay: (U, sleep(float(delay)))[0],
      'domain':       lambda (P, U): URL(U, None).domain,
      'hostname':     lambda (P, U): URL(U, None).hostname,
      'new_save_dir': lambda (P, U): (U, P.set_user_vars(save_dir=new_dir(P.save_dir)))[0],
      'per':          lambda (P, U): (copy(P), U),
      'print':        lambda (P, U), **kw: (U, P.print_url_string(U, **kw))[0],
      'query':        lambda (P, U), *a: url_query(U, *a),
      'quote':        lambda (P, U): quote(U),
      'unquote':      lambda (P, U): unquote(U),
      'save':         lambda (P, U), *a, **kw: (U, P.save_url(U, *a, **kw))[0],
      's':            substitute,
      'title':        lambda (P, U): U.page_title,
      'type':         lambda (P, U): url_io(U.content_type, ""),
      'xmlattr':      lambda (P, U), attr: [ A for A in (ElementTree.XML(U).get(attr),) if A is not None ],
    }
one_to_one_scoped = ('per',)

one_test = {
      'has_title':    lambda (P, U): U.page_title is not None,
      'reject_re':    lambda (P, U), regexp: not regexp.search(U),
      'same_domain':  lambda (P, U): notNone(U.referer, "%r.referer" % (U,)) and U.domain == U.referer.domain,
      'same_hostname':lambda (P, U): notNone(U.referer, "%r.referer" % (U,)) and U.hostname == U.referer.hostname,
      'same_scheme':  lambda (P, U): notNone(U.referer, "%r.referer" % (U,)) and U.scheme == U.referer.scheme,
      'select_re':    lambda (P, U), regexp: regexp.search(U),
    }

re_COMPARE = re.compile(r'([a-z]\w*)==')
re_ASSIGN  = re.compile(r'([a-z]\w*)=')
re_TEST    = re.compile(r'([a-z]\w*)~')
re_GROK    = re.compile(r'([a-z]\w*(\.[a-z]\w*)*)\.([_a-z]\w*)', re.I)

def action_func(action):
  ''' Accept a string `action` and return a tuple of:
        func_sig, function
      `func_sig` and `function` are used with Later.pipeline
      and `kwargs` is used as extra parameters for `function`.
  '''
  function = None
  func_sig = None
  scoped = False        # function output is (P,U), not just U
  args = []             # collect foo and foo=bar operator arguments
  kwargs = {}
  # parse action into function and kwargs
  with Pfx("%s", action):
    action0 = action

    if action.startswith('!'):
      # ! shell command to generate items based off current item
      function, func_sig = action_shcmd(action[1:])
    elif action.startswith('|'):
      # | shell command to pipe though
      function, func_sig = action_pipecmd(action[1:])
    else:
      # comparison
      # varname==
      m = re_COMPARE.match(action)
      if m:
        function, func_sig = action_compare(m.group(1), action[m.end():])
      else:
        # assignment
        # varname=
        m = re_ASSIGN.match(action)
        if m:
          function, func_sig = action_assign(m.group(1), action[m.end():])
        else:
          # test of variable value
          # varname~selector
          m = re_TEST.match(action)
          if m:
            function, func_sig = action_test(m.group(1), action[m.end():])
          else:
            # catch "a.b.c" and convert to "grok:a.b.c"
            m = re_GROK.match(action)
            if m:
              action = 'grok:' + action
            # operator or s//
            func, offset = get_identifier(action)
            if func:
              with Pfx(func):
                # an identifier
                if func == 's':
                  # s/this/that/
                  if offset == len(action):
                    raise ValueError("missing delimiter")
                  delim = action[offset]
                  delim2pos = action.find(delim, offset+1)
                  if delim2pos < offset + 1:
                    raise ValueError("missing second delimiter (%r)" % (delim,))
                  regexp = action[offset+1:delim2pos]
                  if not regexp:
                    raise ValueError("empty regexp")
                  delim3pos = action.find(delim, delim2pos+1)
                  if delim3pos < delim2pos+1:
                    raise ValueError("missing third delimiter (%r)" % (delim,))
                  repl_format = action[delim2pos+1:delim3pos]
                  offset = delim3pos + 1
                  repl_all = False
                  repl_icase = False
                  re_flags = 0
                  while offset < len(action):
                    modchar = action[offset]
                    offset += 1
                    if modchar == 'g':
                      repl_all = True
                    elif modchar == 'i':
                      repl_icase = True
                      re_flags != re.IGNORECASE
                    else:
                      raise ValueError("unknown s///x modifier: %r" % (modchar,))
                  debug("s: regexp=%r, replacement=%r, repl_all=%s, repl_icase=%s", regexp, repl_format, repl_all, repl_icase)
                  kwargs['regexp'] = re.compile(regexp, flags=re_flags)
                  kwargs['replacement'] = repl_format
                  kwargs['replace_all'] = repl_all
                elif func == "divert" or func == "pipe":
                  # divert:pipe_name[:selector]
                  # pipe:pipe_name[:selector]
                  func_sig, function, scoped = action_divert_pipe(func, action, offset)
                elif func == 'grok' or func == 'grokall':
                  # grok:a.b.c.d[:args...]
                  # grokall:a.b.c.d[:args...]
                  func_sig, function = action_grok(func, action, offset)
                elif func == 'for':
                  # for:var=value
                  # warning: implies 'per'
                  func_sig, function, scoped = action_for(func, action, offset)
                elif func in ('see', 'seen', 'unseen'):
                  # see[:seenset,...[:value]]
                  # seen[:seenset,...[:value]]
                  # unseen[:seenset,...[:value]]
                  func_sig, function = action_sight(func, action, offset)
                # some other function: gather arguments
                elif offset < len(action):
                  marker = action[offset]
                  if marker == ':':
                    # followed by :kw1=value,kw2=value,...
                    kwtext = action[offset+1:]
                    if func == "print":
                      # print is special - just a format string relying on current state
                      kwargs['string'] = kwtext
                    else:
                      for kw in kwtext.split(','):
                        if '=' in kw:
                          kw, v = kw.split('=', 1)
                          kwargs[kw] = v
                        else:
                          args.append(kw)
                  else:
                    raise ValueError("unrecognised marker %r" % (marker,))
              if not function:
                function, func_sig, scoped = function_by_name(func, func_sig)
              else:
                if func_sig is None:
                  raise RuntimeError("function is set (%r) but func_sig is None" % (function,))
            # select URLs matching regexp
            # /regexp/
            elif action.startswith('/'):
              if action.endswith('/'):
                regexp = action[1:-1]
              else:
                regexp = action[1:]
              regexp = re.compile(regexp)
              function = lambda (P, U): regexp.search(U)
              function.__name__ = '/%s/' % (regexp,)
              func_sig = FUNC_SELECTOR
            # select URLs not matching regexp
            # -/regexp/
            elif action.startswith('-/'):
              if action.endswith('/'):
                regexp = action[2:-1]
              else:
                regexp = action[2:]
              regexp = re.compile(regexp)
              function = lambda (P, U): not regexp.search(U)
              function.__name__ = '-/%s/' % (regexp,)
              func_sig = FUNC_SELECTOR
            # parent
            # ..
            elif action == '..':
              function = lambda (P, U): U.parent
              func_sig = FUNC_ONE_TO_ONE
            # select URLs ending in particular extensions
            elif action.startswith('.'):
              if action.endswith('/i'):
                exts, case = action[1:-2], False
              else:
                exts, case = action[1:], True
              exts = exts.split(',')
              function = lambda (P, U): has_exts( U, exts, case_sensitive=case )
              func_sig = FUNC_SELECTOR
            # select URLs not ending in particular extensions
            elif action.startswith('-.'):
              if action.endswith('/i'):
                exts, case = action[2:-2], False
              else:
                exts, case = action[2:], True
              exts = exts.split(',')
              function = lambda (P, U): not has_exts( U, exts, case_sensitive=case )
              func_sig = FUNC_SELECTOR
            else:
              raise ValueError("unknown function %r" % (func,))

    # The pipeline itself passes (P, U) item tuples.
    #
    # All functions accept a leading (P, U) tuple argument but most emit only
    # a U result (or just a Boolean for selectors).
    # A few, like "per", emit a (P, U) because they change the "scope" P argument.
    # If "scoped" is true, we expect the latter.
    # Otherwise we wrap FUNC_ONE_TO_ONE and FUNC_ONE_TO_MANY to emit the
    # supplied P value with their outputs.
    # FUNC_MANY_TO_MANY functions have their own convoluted wrapper.
    #
    func0 = function
    if scoped and func_sig not in (FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_MANY_TO_MANY):
      raise RuntimeError("scoped is true but func_sig == %r" % (func_sig,))
    if func_sig == FUNC_SELECTOR:
      def funcPU(item):
        return func0(item, *args, **kwargs)
    elif func_sig == FUNC_ONE_TO_ONE:
      if scoped:
        def funcPU(item):
          return func0(item, *args, **kwargs)
      else:
        def funcPU(item):
          P, U = item
          return P, func0(item, *args, **kwargs)
    elif func_sig == FUNC_ONE_TO_MANY:
      if scoped:
        def funcPU(item):
          for P, U in func0(item, *args, **kwargs):
            yield P, U
      else:
        def funcPU(item):
          P, U = item
          for i in func0(item, *args, **kwargs):
            yield P, i
    elif func_sig == FUNC_MANY_TO_MANY:
      if scoped:
        def funcPU(items):
          return func0(items)
      else:
        # Many-to-many functions are different.
        # We split out the Ps and Us from the input items.
        # 
        # and re-attach the P components by reverse mapping from the U results;
        # unrecognised Us get associated with Ps[0].
        #
        def funcPU(items):
          if not isinstance(items, list):
            items = list(items)
          if items:
            # preserve the first Pilfer context to attach to unknown items
            P0 = items[0][0]
            idmap = dict( [ ( id(item), item ) for item in items ] )
            Ps = [ item[0] for item in items ]
            Us = [ item[1] for item in items ]
          else:
            P0 = None
            idmap = {}
            Ps = []
            Us = []
          Us2 = func0(Ps, Us, *args, **kwargs)
          return [ (idmap.get(id(U), P0), U) for U in Us2 ]
    else:
      raise RuntimeError("unhandled func_sig %r" % (func_sig,))

    # wrap functions in exception catchers to report but not reraise
    if func_sig in (FUNC_ONE_TO_MANY, FUNC_MANY_TO_MANY):
      funcPU = noexc_gen(funcPU)
    else:
      funcPU = noexc(funcPU)

    def trace_function(*a, **kw):
      ##D("DO %s(a=(%d args; %r),kw=%r)", action0, len(a), a, kw)
      ##D("   funcPU<%s:%d>=%r %r ...", funcPU.func_code.co_filename, funcPU.func_code.co_firstlineno, funcPU, dir(funcPU))
      with Pfx(action0):
        try:
          retval = funcPU(*a, **kw)
        except Exception as e:
          exception("TRACE: EXCEPTION: %s", e)
          raise
        return retval

    return func_sig, trace_function

def function_by_name(func, func_sig):
  ''' Look up `func` in mappings of named functions.
      Return (function, func_sig, scoped).
  '''
  scoped = False
  # look up function by name in mappings
  if func_sig is not None:
    raise RuntimeError("func_sig is set (%r) but function is None" % (func_sig,))
  if func in many_to_many:
    # many-to-many functions get passed straight in
    function = many_to_many[func]
    func_sig = FUNC_MANY_TO_MANY
  elif func in one_to_many:
    function = one_to_many[func]
    func_sig = FUNC_ONE_TO_MANY
  elif func in one_to_one:
    function = one_to_one[func]
    func_sig = FUNC_ONE_TO_ONE
    scoped = func in one_to_one_scoped
  elif func in one_test:
    function = one_test[func]
    func_sig = FUNC_SELECTOR
  else:
    raise ValueError("unknown action")
  return function, func_sig, scoped

def action_divert_pipe(func, action, offset):
  # divert:pipe_name[:selector]
  # pipe:pipe_name[:selector]
  #
  # Divert selected items to the named pipeline
  # or filter selected items through an instance of the named pipeline.
  if offset == len(action):
    raise ValueError("missing marker")
  marker = action[offset]
  offset += 1
  pipe_name, offset = get_identifier(action, offset)
  if not pipe_name:
    raise ValueError("no pipe name")
  if offset >= len(action):
    sel_function = lambda (P, U): True
  else:
    if marker != action[offset]:
      raise ValueError("expected second marker to match first: expected %r, saw %r"
                       % (marker, action[offset]))
    sel_func_sig, sel_function = action_func(action[offset+1:])
    if sel_func_sig != FUNC_SELECTOR:
      raise ValueError("expected selector function but found: %r" % (action[offset+1:],))
  if func == "divert":
    # function to divert selected items to a single named pipeline
    func_sig = FUNC_ONE_TO_MANY
    scoped = False
    def function(item):
      P, U = item
      try:
        if sel_function(item):
          try:
            pipe = P.diversion(pipe_name)
          except KeyError:
            error("no pipe named %r", pipe_name)
          else:
            pipe.inQ.put(item)
        else:
          yield U
      except Exception as e:
        exception("OUCH")
  elif func == "pipe":
    # gather all items and feed to an instance of the specified pipeline
    func_sig = FUNC_MANY_TO_MANY
    scoped = True
    def function(items):
      pipe_items = []
      for item in items:
        if sel_function(item):
          pipe_items.append(item)
        else:
          yield item
      if pipe_items:
        P = pipe_items[0][0]
        with P.later.more_capacity(1):
          outQ = P.pipe_through(pipe_name, pipe_items)
          for item in outQ:
            yield item
  else:
    raise ValueError("expected \"divert\" or \"pipe\", got func=%r" % (func,))
  return func_sig, function, scoped

def action_sight(func, action, offset):
  # see[:seenset,...[:value]]
  # seen[:seenset,...[:value]]
  # unseen[:seenset,...[:value]]
  seensets = ('_',)
  value = '{url}'
  if offset < len(action):
    if action[offset] != ':':
      raise ValueError("bad marker after %r, expected ':', found %r", func, action[offset])
    seensets, offset = get_other_chars(action, ':', offset+1)
    seensets = seensets.split(',')
    if not seensets:
      seensets = ('_',)
    if offset < len(action):
      if action[offset] != ':':
        raise RuntimeError("parse should have a second colon after %r", action[:offset])
      value = action[offset+1:]
      if not value:
        value = '{url}'
  if func == 'see':
    func_sig = FUNC_ONE_TO_ONE
    def function( (P, U) ):
      see_value = P.format_string(value, U)
      for seenset in seensets:
        P.see(see_value, seenset)
      return U
  elif func == 'seen':
    func_sig = FUNC_SELECTOR
    def function( (P, U) ):
      see_value = P.format_string(value, U)
      return any( [ P.seen(see_value, seenset) for seenset in seensets ] )
  elif func == 'unseen':
    func_sig = FUNC_SELECTOR
    def function( (P, U) ):
      see_value = P.format_string(value, U)
      return not any( [ P.seen(see_value, seenset) for seenset in seensets ] )
  else:
    raise RuntimeError("action_sight called with unsupported action %r", func)
  return func_sig, function

def action_for(func, action, offset):
  # for:varname=values
  #
  func_sig = FUNC_ONE_TO_MANY
  scoped = True
  if offset == len(action) or action[offset] != ':':
    raise ValueError("missing colon")
  offset += 1
  varname, offset = get_identifier(action, offset)
  if not varname:
    raise ValueError("missing varname")
  if offset == len(action) or action[offset] != '=':
    raise ValueError("missing =values")
  values = action[offset+1:]
  def function( (P, U) ):
    # expand "values", split on whitespace, iterate with new Pilfer
    value_list = P.format_string(values, U).split()
    for value in value_list:
      P2 = copy(P)
      P2.set_user_vars(**{varname: value})
      yield P2, U
  return func_sig, function, scoped

def action_grok(func, action, offset):
  # grok:a.b.c.d[:args...]
  # grokall:a.b.c.d[:args...]
  #
  # Import "d" from the python module "a.b.c".
  # d() should return a mapping of varname to value.
  #
  # For grok, call d((P, U), kwargs) and apply the
  # returned mapping to P.user_vars.
  #
  # From grokall, call d(Ps, Us, kwargs) and apply
  # the returned mapping to each P.user_vars.
  #
  is_grokall = func == "grokall"
  if offset == len(action):
    raise ValueError("missing marker")
  marker = action[offset]
  offset += 1
  m = re_GROK.match(action[offset:])
  if not m:
    raise ValueError("expected a.b.c.d name at \"%s\"" % (action[offset:],))
  grok_module = m.group(1)
  grok_funcname = m.group(3)
  offset += m.end()
  if offset < len(action):
    if marker != action[offset]:
      raise ValueError("expected second marker to match first: expected %r, saw %r"
                       % (marker, action[offset]))
    offset += 1
    raise RuntimeError("arguments to %s not yet implemented" % (func,))
  if is_grokall:
    func_sig = FUNC_MANY_TO_MANY
    def function(items):
      for item in grokall(grok_module, grok_funcname, items):
        yield item
  else:
    func_sig = FUNC_ONE_TO_ONE
    def function( (P, U), *a, **kw):
      return grok(grok_module, grok_funcname, (P, U), *a, **kw)
  return func_sig, function

def action_shcmd(shcmd):
  ''' Return (function, func_sig) for a shell command.
  '''
  shcmd = shcmd.strip()
  def function(item):
    P, U = item
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, U)
    except KeyError as e:
      warning("shcmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        with open('/dev/null') as fp0:
          fd0 = fp0.fileno()
          try:
            subp = Popen(['/bin/sh', '-c', 'sh -uex; '+v], stdin=fd0, stdout=PIPE, close_fds=True)
          except Exception as e:
            exception("Popen: %r", e)
            return
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)
  return function, FUNC_ONE_TO_MANY

def action_pipecmd(shcmd):
  ''' Return (function, func_sig) for pipeline through a shell command.
  '''
  shcmd = shcmd.strip()
  def function(items):
    if not isinstance(items, list):
      items = list(items)
    if not items:
      return
    P, U = items[0]
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, U)
    except KeyError as e:
      warning("pipecmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        # spawn the shell command
        try:
          subp = Popen(['/bin/sh', '-c', 'sh -uex; '+v], stdin=PIPE, stdout=PIPE, close_fds=True)
        except Exception as e:
          exception("Popen: %r", e)
          return
        # spawn a daemon thread to feed items to the pipe
        def feedin():
          for P, U in items:
            print(U, file=subp.stdin)
          subp.stdin.close()
        T = Thread(target=feedin, name='feedin to %r' % (v,))
        T.daemon = True
        T.start()
        # read lines from the pipe, trim trailing newlines and yield
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)
  return function, FUNC_MANY_TO_MANY

def action_compare(var, value):
  ''' Return (function, func_sig) for a variable value comparison.
  '''
  def function(item):
    P, U = item
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      raise
    cvalue = M.format(value)
    return vvalue == cvalue
  return function, FUNC_SELECTOR

def action_test(var, selector):
  ''' Return (function, func_sig) for a selector applied to the variable `var`.
  '''
  sel_func_sig, sel_function = action_func(selector)
  if sel_func_sig != FUNC_SELECTOR:
    raise ValueError("expected selector function but found: %r" % (selector,))
  def function(item):
    P, U = item
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      return False
    result = sel_function( (P, vvalue) )
    return result
  return function, FUNC_SELECTOR

def action_assign(var, value):
  ''' Return (function, func_sig) for a variable value assignment.
  '''
  def function(item):
    P, U = item
    P.set_user_var(var, value, U)
    return U
  return function, FUNC_ONE_TO_ONE

class PipeSpec(O):

  def __init__(self, name, argv):
    O.__init__(self)
    self.name = name
    self.argv = argv

  def pipe_funcs(self, action_map):
    with Pfx(self.name):
      pipe_funcs, errors = argv_pipefuncs(self.argv, action_map)
    return pipe_funcs, errors

def load_pilferrcs(pathname):
  rcs = []
  with Pfx(pathname):
    if os.path.isfile(pathname):
      # filename: load pilferrc file
      rcs.append(PilferRC(pathname))
    elif os.path.isdir(pathname):
      # directory: load pathname/rc and then pathname/*.rc
      # recurses if any of these are also directories
      rcpath = os.path.join(pathname, "rc")
      if os.path.exists(rcpath):
        rcs.extend(load_pilferrcs(rcpath))
      subrcs = sorted( name for name in os.listdir(pathname)
                       if not name.startswith('.') and name.endswith('.rc')
                     )
      for subrc in subrcs:
        rcpath = os.path.join(pathname, subrc)
        rcs.extend(load_pilferrcs(rcpath))
    else:
      warning("neither a file nor a directory, ignoring")
  return rcs

class PilferRC(O):

  def __init__(self, filename):
    O.__init__(self)
    self.filename = filename
    self._lock = Lock()
    self.defaults = {}
    self.pipe_specs = {}
    self.action_map = {}
    self.seen_backing_files = {}
    if filename is not None:
      self.loadrc(filename)

  @locked
  def add_pipespec(self, spec, pipe_name=None):
    ''' Add a PipeSpec to this Pilfer's collection, optionally with a different `pipe_name`.
    '''
    if pipe_name is None:
      pipe_name = spec.name
    specs = self.pipe_specs
    if pipe_name in specs:
      raise KeyError("pipe %r already defined" % (pipe_name,))
    specs[pipe_name] = spec

  def loadrc(self, filename):
    ''' Read a pilferrc file and load pipeline definitions.
    '''
    trace("load %s", filename)
    with Pfx(filename):
      cfg = ConfigParser()
      with open(filename) as fp:
        cfg.readfp(fp)
      self.defaults.update(cfg.defaults().iteritems())
      if cfg.has_section('actions'):
        for action_name in cfg.options('actions'):
          with Pfx('[actions].%s', action_name):
            self.action_map[action_name] = shlex.split(cfg.get('actions', action_name))
      if cfg.has_section('pipes'):
        for pipe_name in cfg.options('pipes'):
          with Pfx('[pipes].%s', pipe_name):
            pipe_spec = cfg.get('pipes', pipe_name)
            debug("loadrc: pipe = %s", pipe_spec)
            self.add_pipespec(PipeSpec(pipe_name, shlex.split(pipe_spec)))
      # load [seen] name=>backing_file mapping
      # NB: not yet envsub()ed
      if cfg.has_section('seen'):
        for setname in cfg.options('seen'):
          backing_file = cfg.get('seen', setname).strip()
          self.seen_backing_files[setname] = backing_file

  def __getitem__(self, pipename):
    ''' Fetch PipeSpec by name.
    '''
    return self.pipe_specs[pipename]

  def __setitem__(self, pipename, pipespec):
    specs = self.pipespecs
    if pipename in specs:
      raise KeyError("repeated definition of pipe named %r", pipename)
    specs[pipename] = pipespec

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))
