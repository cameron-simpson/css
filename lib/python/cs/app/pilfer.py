#!/usr/bin/env python
#
# Web page utility.
#       - Cameron Simpson <cs@zip.com.au> 07jul2010
#

from __future__ import with_statement
import sys
import os
import errno
import os.path
from itertools import chain
import re
if sys.hexversion < 0x02060000: from sets import Set as set
from urllib2 import HTTPError, URLError
from cs.logutils import setup_logging, Pfx, debug, error, warning, exception
from cs.urlutils import URL

IMAGE_SUFFIXES = ( 'png', 'jpg', 'jpeg', 'gif', 'ico', )
VIDEO_SUFFIXES = ( 'mp2', 'mp4', 'avi', 'wmv', )

usage = '''Usage: %s op [args...]
  %s url URL actions...'''

def main(argv):
  argv = list(argv)
  xit = 0
  argv0 = argv.pop(0)
  cmd = os.path.basename(argv0)
  setup_logging(cmd)

  P = Pilfer()

  badopts = False
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
              with Pfx("stdin:%d" % (lineno,)):
                if not line.endswith('\n'):
                  raise ValueError("unexpected EOF - missing newline")
                line = line.strip()
                if not line or line.startswith('#'):
                  debug("SKIP: %s", line)
                  continue
                urls.append(URL(line, None, P.user_agent))
          else:
            urls = [ URL(url, None, P.user_agent) ]
          urls = P.act( urls, argv)
          for url in urls:
            print url
      else:
        error("unsupported op")
        badopts = True
      
    if badopts:
      print >>sys.stderr, usage % (cmd, cmd,)
      xit = 2

  return xit

def unique(items, seen=None):
  ''' A generator that yields unseen items, as opposed to just
      stuffing them all into a set.
  '''
  if seen is None:
    seen = set()
  for I in items:
    if I not in seen:
      yield I
      seen.add(I)

class Pilfer(object):
  ''' State for the pilfer app.
  '''

  def __init__(self):
    self.save_dir = None
    self.user_agent = None
    self.user_vars = {}

  def act(self, urls, actions):
    ''' Return an iterable of the results of the actions applied to the URLs.
    '''
    for action in actions:
      with Pfx(action):
        if action.startswith('/'):
          # select URLs matching regexp
          if action.endswith('/'):
            regexp = action[1:-1]
          else:
            regexp = action[1:]
          regexp = re.compile(regexp)
          urls = list( chain( *[ self.select_by_re(U, regexp) for U in urls ] ) )
          continue
        if action.startswith('-/'):
          # select URLs matching regexp
          if action.endswith('/'):
            regexp = action[2:-1]
          else:
            regexp = action[2:]
          regexp = re.compile(regexp)
          urls = list( chain( *[ self.deselect_by_re(U, regexp) for U in urls ] ) )
          continue
        if action.startswith('.'):
          # select URLs endsing in suffix
          if action.endswith('/i'):
            exts, case = action[1:-2], False
          else:
            exts, case = action[1:], True
          exts = exts.split(',')
          urls = list( self.with_exts( urls, suffixes=exts, case_sensitive=case) )
          continue
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
          urls = [ URL(regexp.sub(parts[2], U, ( 0 if do_all else 1 )), U, user_agent=self.user_agent) for U in urls ]
          continue
        if action == 'sort':
          urls = sorted(list(urls))
          continue
        if action == 'unique':
          urls = unique(urls)
          continue
        if action == 'new_dir':
          urls = list(urls)
          if not urls:
            error("no URLs in play, not making a directory")
          else:
            self.save_dir = self.new_save_dir(self.url_save_dir(urls[0]))
          continue
        if '==' in action:
          param, value = action.split('==', 1)
          if param in ('scheme', 'netloc', 'path', 'params', 'query', 'fragment', 'username', 'password', 'hostname', 'port'):
            urls = [ U for U in urls if getattr(U, param) == value ]
            continue
        if '=' in action:
          param, value = action.split('=', 1)
          if len(param) == 1 and param.isalpha():
            self.user_vars[param] = value
            continue
          if param in ('save_dir', 'user_agent'):
            setattr(self, param, value)
            if param == 'user_agent':
              for U in urls:
                U.user_agent = value
            continue
        urls = chain( *[ self.url_action(action, U) for U in urls ] )
    return urls

  def new_save_dir(self, dir):
    try:
      os.makedirs(dir)
    except OSError, e:
      if e.errno != errno.EEXIST:
        raise
      n = 2
      while True:
        ndir = "%s-%d" % (dir, n)
        try:
          os.makedirs(ndir)
        except OSError, e:
          if e.errno != errno.EEXIST:
            raise
          n += 1
          continue
        dir = ndir
        break
    return dir

  def url_save_dir(self, U):
    ''' Return save directory for supplied URL.
    '''
    if self.save_dir:
      dir = self.save_dir
    else:
      dir = U.hostname+'-'+os.path.dirname(U.path).replace('/', '-')
    return dir

  def url_save(self, U):
    with Pfx(U):
      if self.save_dir:
        dir = self.save_dir
      else:
        dir = U.hostname+'-'+os.path.dirname(U.path).replace('/', '-')
      try:
        self.url_save_full(U, dir, overwrite_dir=True)
      except HTTPError, e:
        error("%s", e)
        return
      except URLError, e:
        error("%s", e)
        return
      yield U

  def url_save_full(self, U, dir=None, full_path=False, require_dir=False, overwrite_dir=False, overwrite_file=False):
    with Pfx("save(%s)" % (U,)):
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
        except OSError, e:
          if e.errno != errno.EEXIST:
            raise
          if not overwrite_dir:
            n = 2
            while True:
              ndir = "%s-%d" % (dir, n)
              try:
                os.makedirs(ndir)
              except OSError, e:
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
      except Exception, e:
        exception("exception writing content: %s", e)
        os.remove(savepath)
        savefp.close()
        raise e

  def url_action(self, action, U):
    ''' Accept `action` and URL `U`, yield results of action applied to URL.
    '''
    global actions
    with Pfx("%s(%s)" % (action, U)):
      url_func = self.action_map.get(action)
      if url_func is None:
        raise ValueError("unknown action")
      return url_func(self, U)

  def url_hrefs(self, U, *a, **kw):
    with Pfx("hrefs(%s)" % (U,)):
      if 'absolute' not in kw:
        kw['absolute'] = True
      hrefs = ()
      try:
        # using list() to run now instead of deferred
        hrefs = list(U.hrefs(*a, **kw))
      except HTTPError, e:
        error("%s", e)
      except URLError, e:
        error("%s", e)
      return hrefs

  def url_srcs(self, U, *a, **kw):
    with Pfx("srcs(%s)" % (U,)):
      if 'absolute' not in kw:
        kw['absolute'] = True
      hrefs = ()
      try:
        # using list() to run now instead of deferred
        hrefs = list(U.srcs(*a, **kw))
      except HTTPError, e:
        error("%s", e)
      except URLError, e:
        error("%s", e)
      return hrefs

  def with_exts(self, urls, suffixes, case_sensitive=False):
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

  def url_images(self, U):
    return self.with_exts( self.url_hrefs(U, absolute=True), IMAGE_SUFFIXES )

  def url_inline_images(self, U):
    return self.with_exts( self.url_srcs(U, 'img', absolute=True), IMAGE_SUFFIXES)

  def url_print(self, U):
    print U
    yield U

  def url_print_type(self, U):
    print U, U.content_type
    yield U

  def url_samedomain(self, U):
    if U.domain == U.referer.domain:
      yield U

  def url_samehostname(self, U):
    if U.hostname == U.referer.hostname:
      yield U

  def url_samescheme(self, U):
    if U.scheme == U.referer.scheme:
      yield U

  def url_isimage(self, U):
    return self.with_exts([U], IMAGE_SUFFIXES)

  def url_isvideo(self, U):
    return self.with_exts([U], VIDEO_SUFFIXES)

  def select_by_re(self, U, regexp):
    m = regexp.search(U)
    if m:
      yield U

  def deselect_by_re(self, U, regexp):
    m = regexp.search(U)
    if not m:
      yield U

  action_map = {
        'hrefs':    url_hrefs,
        'images':   url_images,
        'iimages':  url_inline_images,
        'isimage':  url_isimage,
        'isvideo':  url_isvideo,
        'print':    url_print,
        'samedomain': url_samedomain,
        'samehostname': url_samehostname,
        'samescheme': url_samescheme,
        'save':     url_save,
        'type':     url_print_type,
      }

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))
