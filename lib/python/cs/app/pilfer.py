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
from copy import copy
from functools import partial
from itertools import chain
import re
if sys.hexversion < 0x02060000: from sets import Set as set
from getopt import getopt, GetoptError
from string import Formatter
from time import sleep
from threading import Lock
from urllib import quote, unquote
from urllib2 import HTTPError, URLError
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from cs.fileutils import file_property
from cs.later import Later
from cs.logutils import setup_logging, logTo, Pfx, debug, error, warning, exception, pfx_iter, D
from cs.threads import runTree, RunTreeOp
from cs.urlutils import URL
from cs.misc import O

ARCHIVE_SUFFIXES = ( 'tar', 'tgz', 'tar.gz', 'tar.bz2', 'cpio', 'rar', 'zip', 'dmg' )
IMAGE_SUFFIXES = ( 'png', 'jpg', 'jpeg', 'gif', 'ico', )
VIDEO_SUFFIXES = ( 'mp2', 'mp4', 'avi', 'wmv', )

usage = '''Usage: %s [options...] op [args...]
  %s url URL actions...
  Options:
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

  badopts = False

  try:
    opts, argv = getopt(argv, 'qu')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    if opt == '-q':
      quiet = True
    elif opt == '-u':
      P.flush_print = True
    else:
      raise NotImplementedError("%s: unimplemented option" % (opt,))

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
          if url == '-':
            urls = []
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
                urls.append(URL(line, None, P.user_agent))
          else:
            urls = [ URL(url, None, P.user_agent) ]
          run_ops = [ action_operator(action) for action in argv ]
          ##D("run_ops = %r", run_ops)
          with Later(4) as PQ:
            runTree(urls, run_ops, P, PQ)
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    print(usage % (cmd, cmd), file=sys.stderr)
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
  ##items = list(items)
  ##D("unique(items=%s,..)...", items)
  if seen is None:
    seen = set()
  for I in items:
    if I not in seen:
      D("unique: yield I=%r", I)
      yield I
      seen.add(I)

class Pilfer(O):
  ''' State for the pilfer app.
  '''

  def __init__(self, **kw):
    self._lock = Lock()
    self.flush_print = False
    self._print_to = None
    self.user_agent = None
    self.user_vars = {}
    self._urlsfile = None
    self.seen_urls = None
    self.seen_urls_path = '.urls-seen'
    self._seen_urls_lock = Lock()
    O.__init__(self, **kw)
    if self.seen_urls is None:
      self.seen_urls = set()
      self.read_seen_urls()

  def read_seen_urls(self, urlspath=None):
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
    v = self.user_vars
    for k in kw:
      v[k] = kw[k]

  def __copy__(self):
    ''' Copy this Pilfer state item, preserving shared state.
    '''
    return Pilfer(user_vars=dict(self.user_vars),
                  seen_urls=self.seen_urls,
                  _seen_urls_lock=self._seen_urls_lock,
                 )

  def seen(self, U):
    with self._seen_urls_lock:
      return U in self.seen_urls

  def see(self, U):
    with self._seen_urls_lock:
      if U not in self.seen_urls:
        self.seen_urls.add(U)
        with open(self.seen_urls_path, "a") as fp:
          fp.write(U)
          fp.write("\n")

  def print(self, *a, **kw):
    print_to = kw.get('file', None)
    if print_to is None:
      print_to = self._print_to
      if print_to is None:
        print_to = sys.stdout
    kw['file'] = print_to
    print(*a, **kw)
    if self.flush_print:
      print_to.flush()

  def url_save_dir(self, U):
    ''' Return the current URL save dir.
        If unset, create one based on the URL `U`.
        Return its path and also save the path in user_vars['save_dir'].
    '''
    with self._lock:
      dir = self.user_vars.get('save_dir')
      if dir is None:
        dir = ( ("%s-%s--%s" % (U.hostname,
                                os.path.dirname(U.path),
                                '-'.join(U.title.split())))
                .replace('/', '-')
              )[:os.statvfs('.').f_namemax-6]
        dir = new_dir(dir)
        self.user_var['save_dir'] = dir
    return dir

  def act(self, urls, actions):
    ''' Return an iterable of the results of the actions applied to the URLs.
        Actions apply breadth first, except at the "per" action, which kicks
        off a breadth first action sequence for each current URL (this is important
        for some actions like "new_dir" etc).
    '''
    if not isinstance(actions, list):
      action = list(actions)
    while actions:
      action = actions.pop(0)
      with Pfx(action):

        # s/this/that/g
        if len(action) > 4 and action.startswith('s') and not action[1].isalnum() and action[1] != '_':
          marker = action[1]
          parts = action.split(marker)
          if len(parts) < 3:
            error("unsufficient parts in s%s: %s", marker, action)
            continue
          elif len(parts) == 3:
            parts = parts + ['']
          elif len(parts) > 4:
            error("too many parts in s%s: %s", marker, action)
            continue
          if parts[3] == '':
            do_all = False
          elif parts[3] == 'g':
            do_all = True
          else:
            error("invalid optional 'g' part, found: %s", parts[3])
            continue
          regexp = re.compile(parts[1])
          urls = pfx_iter(action,
                          [ URL(regexp.sub(parts[2], U, ( 0 if do_all else 1 )), U, user_agent=self.user_agent)
                            for U in urls ] )
          continue

        # compute and create a new save dir based on the first URL
        if action == 'new_dir':
          urls = list(urls)
          if not urls:
            # no URLs - do nothing
            continue
          try:
            save_dir = self.url_save_dir(urls[0], ignore_save_dir=True)
          except (HTTPError, URLError) as e:
            error("%s: %s", urls[0], e)
            return ()
          self.save_dir = self.new_save_dir(save_dir)
          continue

        # select URLs where attr==value
        if '==' in action:
          param, value = action.split('==', 1)
          if param in ('scheme', 'netloc', 'path', 'params', 'query', 'fragment', 'username', 'password', 'hostname', 'port'):
            urls = pfx_iter(action,  [ U for U in urls if getattr(U, param) == value ] )
            continue

        # save_dir=value, user_agent=value
        if '=' in action:
          param, value = action.split('=', 1)
          if len(param) == 1 and param.isalpha():
            urls = list(urls)
            if not urls:
              return ()
            if len(urls) > 1:
              actions = ['per', action ] + actions
              continue
            self.user_vars[param] = self.format_string(value, URL(urls[0]))
            continue
          if param in ('save_dir', 'user_agent'):
            setattr(self, param, value)
            if param == 'user_agent':
              for U in urls:
                U.user_agent = value
            continue

        # arbitrary other action, call url_action(blah)
        if action in self.action_map_all:
          urls = pfx_iter(action, self.url_action_all(action, urls))
        else:
          urls = pfx_iter(action, chain( *[ self.url_action(action, U) for U in urls ] ) )
    return urls

  class URLkeywords(object):
    ''' A proxy object to fetch approved attributes from a URL.
    '''

    _approved = (
                  'archives',
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
          raise KeyError(k)
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

  def url_save(self, U, *a):
    with Pfx(U):
      try:
        content = U.content
      except (HTTPError, URLError) as e:
        error("%s", e)
        return
      if a:
        a = list(a)
        saveas = a.pop(0)
        if a:
          raise ValueError("extra arguments to 'save': "+", ".join(a))
        if saveas == '-':
          sys.stdout.write(content)
          sys.stdout.flush()
        else:
          try:
            with open(saveas, "wb") as savefp:
              savefp.write(content)
          except IOError as e:
            error("%s: %s", saveas, e)
      else:
        dir = self.url_save_dir(U)
        try:
          self.url_save_full(U, dir, overwrite_dir=True)
        except (HTTPError, URLError) as e:
          error("%s", e)
          return
      yield U

  def url_save_full(self, U, dir=None, full_path=False, require_dir=False, overwrite_dir=False, overwrite_file=False):
    with Pfx("save(%s)", U):
      if dir is None:
        if self.save_dir:
          dir = self.save_dir
        elif full_path:
          dir = os.path.join( '.', U.hostname, os.path.dirname(U.path), )
        else:
          dir = os.path.join( '.', U.hostname, os.path.basename(os.path.dirname(U.path)) )
      if require_dir:
        if not os.path.isdir(dir):
          raise ValueError("not a directory: %s" % (dir,))
      else:
        try:
          os.makedirs(dir)
        except OSError as e:
          if e.errno != errno.EEXIST:
            raise
          if not overwrite_dir:
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
      filename = os.path.basename(U.path)
      savepath = os.path.join(dir, filename)
      if os.path.exists(savepath) and not overwrite_file:
        n = 2
        if '.' in filename:
          filebase, fileext = filename.split('.', 1)
          fileext = '.' + fileext
        else:
          filebase, fileext = filename, ''
        while True:
          nsavepath = os.path.join(dir, "%s-%d%s" % (filebase, n, fileext))
          if not os.path.exists(nsavepath):
            savepath = nsavepath
            break
          n += 1
      debug("save to %s", savepath)
      content = U.content
      savefp = open(savepath, "wb")
      try:
        savefp.write(content)
        savefp.close()
        U.flush()
      except Exception as e:
        U.flush()
        exception("exception writing content: %s", e)
        os.remove(savepath)
        savefp.close()
        raise e

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
MANY_TO_MANY = {
      'sort':         lambda Us, P, *a, **kw: sorted(Us, *a, **kw),
      'unique':       lambda Us, P: unique(Us),
      'first':        lambda Us, P: Us[:1],
      'last':         lambda Us, P: Us[-1:],
    }

ONE_TO_MANY = {
      'hrefs':        lambda U, P, *a: url_hrefs(U, *a),
      'images':       lambda U, P, *a: with_exts(url_hrefs(U, *a), IMAGE_SUFFIXES ),
      'iimages':      lambda U, P, *a: with_exts(url_srcs(U, *a), IMAGE_SUFFIXES ),
      'srcs':         lambda U, P, *a: url_srcs(U, *a),
      'xml':          lambda U, P, match: url_xml_find(U, match),
      'xmltext':      lambda U, P, match: XML(U).findall(match),
    }

# actions that work on individual URLs
ONE_TO_ONE = {
      '..':           lambda U, P: URL(U, None).parent,
      'delay':        lambda U, P, delay: (U, sleep(float(delay)))[0],
      'domain':       lambda U, P: URL(U, None).domain,
      'hostname':     lambda U, P: URL(U, None).hostname,
      'new_dir':      lambda U, P: (U, P.url_save_dir(U))[0],
      'per':          lambda U, P: (U, P.set_user_vars(save_dir=None))[0],
      'print':        lambda U, P: (U, print(U))[0],
      'query':        lambda U, P, *a: url_query(U, *a),
      'quote':        lambda U, P: quote(U),
      'unquote':      lambda U, P: unquote(U),
      'save':         lambda U, P, *a: url_io(P.url_save, (), U, *a),
      'see':          lambda U, P: (U, P.see(U))[0],
      'title':        lambda U, P: U.title if U.title else U,
      'type':         lambda U, P: url_io(U.content_type, ""),
      'xmlattr':      lambda U, P, attr: [ A for A in (ElementTree.XML(U).get(attr),) if A is not None ],
    }

def _search_re(U, P, regexp):
  ''' Search for `regexp` in `U`, return resulting MatchObject or None.
      The result is also stored as `P.re` for subsequent use.
  '''
  m = P.re = regexp.search(U)
  return m

ONE_TEST = {
      'has_title':    lambda U, P: U.title is not None,
      'is_archive':   lambda U, P: has_exts( U, ARCHIVE_SUFFIXES ),
      'is_archive':   lambda U, P: has_exts( U, ARCHIVE_SUFFIXES ),
      'is_image':     lambda U, P: has_exts( U, IMAGE_SUFFIXES ),
      'is_video':     lambda U, P: has_exts( U, VIDEO_SUFFIXES ),
      'reject_re':    lambda U, P, regexp: not regexp.search(U),
      'same_domain':  lambda U, P: notNone(U.referer, "U.referer") and U.domain == U.referer.domain,
      'same_hostname':lambda U, P: notNone(U.referer, "U.referer") and U.hostname == U.referer.hostname,
      'same_scheme':  lambda U, P: notNone(U.referer, "U.referer") and U.scheme == U.referer.scheme,
      'seen':         lambda U, P: P.seen(U),
      'select_exts':  lambda U, P, exts, case: has_exts( U, exts, case_sensitive=case ),
      'select_re':    _search_re,
      'unseen':       lambda U, P: not P.seen(U),
    }

re_COMPARE = re.compile(r'([a-z]\w*)==')
re_ASSIGN  = re.compile(r'([a-z]\w*)=')

def conv_one_to_one(func):
  ''' Convert a one-to-one function to a one to many.
  '''
  def func_one_to_one(U, P):
    yield func(U, P)
  return func_one_to_one

def conv_one_test(func):
  ''' Convert a test-one function to one-to-many.
  '''
  def func_one_test(U, P):
    ok = func(U, P)
    if ok:
      yield U
  return func_one_test

def action_operator(action,
                    many_to_many=None,
                    one_to_many=None,
                    one_to_one=None,
                    one_test=None):
  ''' Accept a string `action` and return a RunTreeOp for use with
      cs.threads.runTree.
  '''
  if many_to_many is None:
    many_to_many = MANY_TO_MANY
  if one_to_many is None:
    one_to_many = ONE_TO_MANY
  if one_to_one is None:
    one_to_one = ONE_TO_ONE
  if one_test is None:
    one_test = ONE_TEST
  with Pfx("%s", action):
    kwargs = {}
    # select URLs matching regexp
    if action.startswith('/'):
      if action.endswith('/'):
        regexp = action[1:-1]
      else:
        regexp = action[1:]
      kwargs['regexp'] = re.compile(regexp)
      action = 'select_re'
    # select URLs not matching regexp
    elif action.startswith('-/'):
      if action.endswith('/'):
        regexp = action[2:-1]
      else:
        regexp = action[2:]
      kwargs['regexp'] = re.compile(regexp)
      action = 'reject_re'
    # select URLs ending in particular extensions
    elif action == '..':
      pass
    elif action.startswith('.'):
      if action.endswith('/i'):
        exts, case = action[1:-2], False
      else:
        exts, case = action[1:], True
      exts = exts.split(',')
      kwargs['case'] = case
      kwargs['exts'] = exts
      action = 'select_exts'
    else:
      m = re_COMPARE.match(action)
      if m:
        var = m.group(1)
        value = action[m.end():]
        k
      else:
        m = re_ASSIGN.match(action)
        if m:
          var = m.group(1)
          value = action[m.end():]
          def assign(U, P, var, value):
            P.user_vars[var] = P.format(value)
            return U
        else:
          # regular action: split off parameters if any
          if ':' in action:
            action, kws = action.split(':', 1)
            for kw in kws.split(','):
              if '=' in kwarg:
                kw, v = kw.split('=', 1)
                kwargs[kw] = v
              else:
                kwargs[kwarg] = True
    op_mode = None
    do_copystate = False
    branch_func = None
    if action == "per":
      op_mode = 'FORK'
      do_copystate = True
    if action in many_to_many:
      # many-to-many functions get passed straight in
      func = many_to_many[action]
      if kwargs:
        func = partial(func, **kwargs)
    elif action in one_to_many:
      # one-to-many is converted into many-to-many
      op_mode = 'PARALLEL'
      func = one_to_many[action]
      if kwargs:
        func = partial(func, **kwargs)
    elif action in one_to_one:
      op_mode = 'PARALLEL'
      func = one_to_one[action]
      if kwargs:
        func = partial(func, **kwargs)
      func = conv_one_to_one(func)
    elif action in one_test:
      op_mode = 'PARALLEL'
      func = one_test[action]
      if kwargs:
        func = partial(func, **kwargs)
      func = conv_one_test(func)
    else:
      raise ValueError("unknown action")
    return RunTreeOp(func, op_mode, do_copystate, branch_func)

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))
