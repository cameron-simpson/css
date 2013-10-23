#!/usr/bin/env python
#
# Web page utility.
#       - Cameron Simpson <cs@zip.com.au> 07jul2010
#

from __future__ import with_statement, print_function
import sys
import os
import errno
import os.path
from collections import defaultdict
from copy import copy
from functools import partial
from itertools import chain
import re
if sys.hexversion < 0x02060000: from sets import Set as set
from getopt import getopt, GetoptError
from string import Formatter
from time import sleep
from threading import Lock, Thread
from urllib import quote, unquote
from urllib2 import HTTPError, URLError
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from cs.debug import thread_dump
from cs.fileutils import file_property
from cs.later import Later, FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_SELECTOR, FUNC_MANY_TO_MANY
from cs.lex import get_identifier
from cs.logutils import setup_logging, logTo, Pfx, debug, error, warning, exception, pfx_iter, D
from cs.queues import IterableQueue
from cs.urlutils import URL
from cs.obj import O
from cs.py3 import input

if os.environ.get('DEBUG', ''):
  def X(tag, *a):
    D("TRACE: "+tag, *a)
else:
  def X(*a):
    pass

ARCHIVE_SUFFIXES = ( 'tar', 'tgz', 'tar.gz', 'tar.bz2', 'cpio', 'rar', 'zip', 'dmg' )
IMAGE_SUFFIXES = ( 'png', 'jpg', 'jpeg', 'gif', 'ico', )
VIDEO_SUFFIXES = ( 'mp2', 'mp4', 'avi', 'wmv', )

DEFAULT_JOBS = 4

usage = '''Usage: %s [options...] op [args...]
  %s url URL actions...
      URL may be "-" to read URLs from standard input.
  Options:
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
    opts, argv = getopt(argv, 'j:qu')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    with Pfx("%s", opt):
      if opt == '-j':
        jobs = int(val)
      elif opt == '-q':
        quiet = True
      elif opt == '-u':
        P.flush_print = True
      else:
        raise NotImplementedError("unimplemented option")

  if not argv:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    if op.startswith('http://') or op.startswith('https://') :
      # infer missing "url" op word
      argv.insert(0, op)
      op ='url'
    with Pfx(op):
      if op == 'url':
        if not argv:
          error("missing URL")
          badopts = True
        else:
          url = argv.pop(0)
          # commence the pipeline by converting strings to URL objects
          def urlise(item):
            yield URL(item, None, scope=P)
          pipe_funcs = [urlise]
          for action in argv:
            try:
              func_sig, function = action_func(action)
            except ValueError as e:
              error("invalid action %r: %s", action, e)
              badopts = True
            else:
              pipe_funcs.append( (func_sig, function) )
          # append a function to discard inputs
          # to avoid filling the outQ
          def discard(item):
            if False:
              yield item
          pipe_funcs.append(discard)
          if not badopts:
            with Later(jobs) as L:
              inQ, outQ = L.pipeline(pipe_funcs)
              # dispatch a consumer of the output queue
	      # (which will be empty, but needs to be consulted to
	      # drain the run queue)
              consumer = Thread(name="pilfer.consumer", target=lambda: list(outQ))
              consumer.start()
              if url != '-':
                # literal URL supplied, deliver to pipeline
                inQ.put(url)
              else:
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
                      inQ.put(url)
              inQ.close()
              consumer.join()
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    print(usage % (cmd, cmd, DEFAULT_JOBS), file=sys.stderr)
    xit = 2

  return xit

def notNone(v, name="value"):
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

def url_xml_find(U, match):
  for found in url_io(URL(U, None).xmlFindall, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

def unique(items, seen=None):
  ''' A generator that yields unseen items, as opposed to just
      stuffing them all into a set and returning the set.
  '''
  if seen is None:
    seen = set()
  for I in items:
    if I not in seen:
      yield I
      seen.add(I)

class PilferCommon(O):

  def __init__(self):
    O.__init__(self)
    self.seen = defaultdict(set)

class Pilfer(O):
  ''' State for the pilfer app.
      Notable attribute include:
        .flush_print    Flush output after print(), default False.
        .user_agent     Specify user-agent string, default None.
        .user_vars      Mapping of user variables for arbitrary use.
  '''

  def __init__(self, **kw):
    self._lock = Lock()
    self._shared = PilferCommon()                  # common state - seen URLs, etc
    self.flush_print = False
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    self.user_vars = {}
    self._urlsfile = None
    O.__init__(self, **kw)

  def __copy__(self):
    ''' Copy this Pilfer state item, preserving shared state.
    '''
    return Pilfer(user_vars=dict(self.user_vars),
                  _shared=self._shared,
                 )

  def seen(self, url, seenset='_'):
    return url in self._shared.seen[seenset]

  def see(self, url, seenset='_'):
    self._shared.seen[seenset].add(url)

  def read_seen_urls(self, urlspath=None):
    ''' Read "seen" URLs from file `urlspath` (default self.seen_urls_path)
        into the .seen_urls set.
    '''
    if urlspath is None:
      urlspath = self.seen_urls_path
    try:
      with open(urlspath) as sfp:
        lineno = 0
        for line in sfp:
          lineno += 1
          if not line.endswith('\n'):
            warning("%s:%d: unexpected EOF - no newline", urlspath, lineno)
          else:
            self.seen_urls.add(line[:-1])
    except IOError as e:
      if e.errno != errno.ENOENT:
        warning("%s: %s", urlspath, e)

  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    self.user_vars.update(kw)

  def print_url_string(self, U, **kw):
    print_string = kw.pop('string', None)
    if print_string is None:
      print_string = '{url}'
    print_string = self.format_string(print_string, U)
    print_to = kw.get('file', None)
    if print_to is None:
      print_to = self._print_to
      if print_to is None:
        print_to = sys.stdout
    kw['file'] = print_to
    with self._print_lock:
      print(print_string, **kw)
      if self.flush_print:
        print_to.flush()

  def save_url(self, U, saveas=None, dir=None, overwrite=False, **kw):
    ''' Save the contents of the URL `U`.
    '''
    with Pfx("save_url(%s)", U):
      if kw:
        kws = sorted(kw.keys())
        if len(kws) > 1:
          raise ValueError("multiple `save' arguments: %s" % (kws,))
        kw = kws[0]
        if saveas is not None:
          raise ValueError("saveas already specified (%s), illegal `save' argument: %s" % (saveas, kw))
        saveas = kw
      if saveas is None:
        saveas = U.basename
      if saveas == '-':
        sys.stdout.write(U.content)
        sys.stdout.flush()
      else:
        with Pfx(saveas):
          if not overwrite and os.path.exists(saveas):
            warning("file exists, not saving")
          else:
            content = U.content
            with open(saveas, "wb") as savefp:
              savefp.write(content)

  class URLkeywords(object):
    ''' A proxy object to fetch approved attributes from a URL.
    '''

    _approved = (
                  'archives',
                  'basename',
                  'dirname',
                  'hrefs',
                  'images',
                  'parent',
                  'path',
                  'referer',
                  'srcs',
                  'title',
                  'url',
                  'videos',
                )

    def __init__(self, U):
      self.url = U

    def keys(self):
      return Pilfer.URLkeywords._approved

    def __getitem__(self, k):
      url = self.url
      with Pfx(url):
        if k not in Pilfer.URLkeywords._approved:
          raise KeyError("not in Pilfer.URLkeywords._approved: %s" % (k,))
        if k == 'url':
          return url
        try:
          return getattr(url, k)
        except AttributeError:
          raise KeyError("no such attribute: .%s" % (k,))

  def format_string(self, s, U):
    ''' Format a string using the URL as context.
    '''
    F = Formatter()
    return F.vformat(s, (), Pilfer.URLkeywords(U))

def new_dir(self, dir):
  ''' Create the directory `dir` or `dir-n` if `dir` exists.
      Return the path of the directory created.
  '''
  try:
    os.makedirs(dir)
  except OSError as e:
    if e.errno != errno.EEXIST:
      raise
    n = 2
    while True:
      ndir = "%s-%d" % (dir, n)
      try:
        os.makedirs(ndir)
      except OSError as e:
        if e.errno != errno.EEXIST:
          raise
        n += 1
        continue
      dir = ndir
      break
  return dir

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

def substitute(src, regexp, replacement, replace_all):
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
  return result

def url_delay(U, delay, *a):
  sleep(float(delay))
  yield U

def url_query(U, *a):
  U = URL(U, None)
  if not a:
    yield U.query
  qsmap = dict( [ ( qsp.split('=', 1) if '=' in qsp else (qsp, '') ) for qsp in U.query.split('&') ] )
  yield ','.join( [ unquote(qsmap.get(qparam, '')) for qparam in a ] )

def url_print_type(self, U):
  self.print(U, U.content_type)
  yield U

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

def url_io_iter(iter):
  ''' Iterator over `iter` and yield its values.
      If it raises URLError or HTTPError, report the error.
  '''
  while 1:
    try:
      i = iter.next()
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield i

def url_hrefs(U, referrer=None):
  return url_io_iter(URL(U, referrer).hrefs(absolute=True))

def url_srcs(U, referrer=None):
  return url_io_iter(URL(U, referrer).srcs(absolute=True))

# actions that work on the whole list of in-play URLs
many_to_many = {
      'sort':         lambda Us, *a, **kw: sorted(Us, *a, **kw),
      'unique':       lambda Us: unique(Us),
      'first':        lambda Us: Us[:1],
      'last':         lambda Us: Us[-1:],
    }

one_to_many = {
      'hrefs':        lambda U, *a: url_hrefs(U, *a),
      'images':       lambda U, *a: with_exts(url_hrefs(U, *a), IMAGE_SUFFIXES ),
      'iimages':      lambda U, *a: with_exts(url_srcs(U, *a), IMAGE_SUFFIXES ),
      'srcs':         lambda U, *a: url_srcs(U, *a),
      'xml':          lambda U, match: url_xml_find(U, match),
      'xmltext':      lambda U, match: XML(U).findall(match),
    }

# actions that work on individual URLs
one_to_one = {
      '..':           lambda U: URL(U, None).parent,
      'delay':        lambda U, P, delay: (U, sleep(float(delay)))[0],
      'domain':       lambda U: URL(U, None).domain,
      'hostname':     lambda U: URL(U, None).hostname,
      'print':        lambda U, **kw: (U, U.print_url_string(U, **kw))[0],
      'query':        lambda U, *a: url_query(U, *a),
      'quote':        lambda U: quote(U),
      'unquote':      lambda U: unquote(U),
      'save':         lambda U, **kw: (U, P.save_url(U, **kw))[0],
      'see':          lambda U: (U, P.see(U))[0],
      's':            substitute,
      'title':        lambda U: U.title if U.title else U,
      'type':         lambda U: url_io(U.content_type, ""),
      'xmlattr':      lambda U, attr: [ A for A in (ElementTree.XML(U).get(attr),) if A is not None ],
    }

def _search_re(U, P, regexp):
  ''' Search for `regexp` in `U`, return resulting MatchObject or None.
      The result is also stored as `P.re` for subsequent use.
  '''
  m = P.re = regexp.search(U)
  return m

ONE_TEST = {
      'has_title':    lambda U: U.title is not None,
      'is_archive':   lambda U: has_exts( U, ARCHIVE_SUFFIXES ),
      'is_archive':   lambda U: has_exts( U, ARCHIVE_SUFFIXES ),
      'is_image':     lambda U: has_exts( U, IMAGE_SUFFIXES ),
      'is_video':     lambda U: has_exts( U, VIDEO_SUFFIXES ),
      'reject_re':    lambda U, regexp: not regexp.search(U),
      'same_domain':  lambda U: notNone(U.referer, "U.referer") and U.domain == U.referer.domain,
      'same_hostname':lambda U: notNone(U.referer, "U.referer") and U.hostname == U.referer.hostname,
      'same_scheme':  lambda U: notNone(U.referer, "U.referer") and U.scheme == U.referer.scheme,
      'seen':         lambda U: P.seen(U),
      'select_re':    _search_re,
      'unseen':       lambda U: not P.seen(U),
    }

re_COMPARE = re.compile(r'([a-z]\w*)==')
re_ASSIGN  = re.compile(r'([a-z]\w*)=')

def action_func(action):
  ''' Accept a string `action` and return a tuple of:
        func_sig, function
      `func_sig` and `function` are used with Later.pipeline
      and `kwargs` is used as extra parameters for `function`.
  '''
  function = None
  kwargs = {}
  # parse action into function and kwargs
  with Pfx("%s", action):
    action0 = action
    func, offset = get_identifier(action)
    if func:
      with Pfx("%s", func):
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
          kwargs['all'] = repl_all
          kwargs['icase'] = repl_icase
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
                  kwargs[kw] = True
          else:
            raise ValueError("unrecognised marker %r" % (marker,))
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
      elif func in one_test:
        function = one_test[func]
        func_sig = FUNC_SELECTOR
      else:
        raise ValueError("unknown action named \"%s\"" % (func,))
    # select URLs matching regexp
    # /regexp/
    elif action.startswith('/'):
      if action.endswith('/'):
        regexp = action[1:-1]
      else:
        regexp = action[1:]
      kwargs['regexp'] = re.compile(regexp)
      function = lambda U, regexp: regexp.search(U)
      func_sig = FUNC_SELECTOR
    # select URLs not matching regexp
    # -/regexp/
    elif action.startswith('-/'):
      if action.endswith('/'):
        regexp = action[2:-1]
      else:
        regexp = action[2:]
      kwargs['regexp'] = re.compile(regexp)
      function = lambda U, regexp: regexp.search(U)
      func_sig = FUNC_SELECTOR
    # parent
    # ..
    elif action == '..':
      function = lambda U, P: U.parent
      func_sig = FUNC_ONE_TO_ONE
    # select URLs ending in particular extensions
    elif action.startswith('.'):
      if action.endswith('/i'):
        exts, case = action[1:-2], False
      else:
        exts, case = action[1:], True
      exts = exts.split(',')
      kwargs['case'] = case
      kwargs['exts'] = exts
      function = lambda U, exts, case: has_exts( U, exts, case_sensitive=case )
      func_sig = FUNC_SELECTOR
    # select URLs not ending in particular extensions
    elif action.startswith('-.'):
      if action.endswith('/i'):
        exts, case = action[2:-2], False
      else:
        exts, case = action[2:], True
      exts = exts.split(',')
      kwargs['case'] = case
      kwargs['exts'] = exts
      function = lambda U, exts, case: not has_exts( U, exts, case_sensitive=case )
      func_sig = FUNC_SELECTOR
    else:
      # comparison
      # varname==
      m = re_COMPARE.match(action)
      if m:
        kwargs['var'] = m.group(1)
        kwargs['value'] = action[m.end():]
        def function(U, P, var, value):
          if var not in P.user_vars:
            return False
          return P.user_vars[var] == P.format(value, U)
        func_sig = FUNC_SELECTOR
      else:
        # assignment
        # varname=
        m = re_ASSIGN.match(action)
        if m:
          kwargs['var'] = m.group(1)
          kwargs['value'] = action[m.end():]
          def function(U, P, var, value):
            P.user_vars[var] = P.format_string(value, U)
            return U
          func_sig = FUNC_ONE_TO_ONE
        else:
          raise ValueError("unknown function %r" % (func,))

    if kwargs:
      function = partial(function, **kwargs)
    func0 = function
    def trace_function(*a, **kw):
      debug("DO %s ...", action0)
      with Pfx(action0):
        return func0(*a, **kw)
    function = trace_function
    return func_sig, function

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))
