#!/usr/bin/python
#
# MetaProxy: content rewriting aggressive cache web proxy toolkit.
#   - Cameron Simpson <cs@cskk.id.au> 26dec2014
#
# Design:
#  local squid:
#       optional: redirect or rewrite specific domains to domain.metaproxy,
#         often http: for local caching and speed
#       divert *.metaproxy directly to trusted remote squid
#
#  remote-personal-squid
#       divert *.metaproxy to local remote-personal-metaproxy
#
#  remote-personal-metaproxy
#       rewrite rq URL to target URL, often https:
#       fetch, parse, return (usually cachable) content
#
# TODO:
#   cookie translation: domain<->domain.metaproxy
#

from __future__ import print_function
import sys
import os
import os.path
from os.path import basename, dirname
from contextlib import contextmanager
from getopt import getopt, GetoptError
import re
import socket
from socket import getservbyname
try:
  import socketserver
except ImportError:
  import SocketServer as socketserver
import stat
from tempfile import mkstemp
from threading import Thread, RLock
from types import SimpleNamespace as NS
try:
  from urllib.parse import urlparse
except ImportError:
  from urlparse import urlparse
from cs.result import Asynchron
from cs.env import envsub
from cs.excutils import LogExceptions
from cs.fileutils import copy_data, Tee
from cs.logutils import setup_logging, debug, info, warning, error, exception, D
from cs.x import X
from cs.pfx import Pfx
from cs.later import Later
from cs.lex import get_hexadecimal, get_other_chars
from cs.progress import Progress
from cs.rfc2616 import read_headers, read_http_request_line, message_has_body, \
                        pass_chunked, pass_length, \
                        dec8, enc8, CRLF, CRLFb
from cs.seq import Seq
from cs.threads import locked, locked_property
from cs.timeutils import time_func

USAGE = '''Usage: %s [-L address:port] [-P upstream_proxy]'''

DEFAULT_PARALLEL = 4

def main(argv):
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)

  listen_addrport = None
  default_proxy_addrport = None

  badopts = False

  try:
    opts, argv = getopt(argv, 'L:P:')
  except GetoptError as e:
    error("unrecognised option: %s: %s"% (e.opt, e.msg))
    badopts = True
    opts, argv = [], []

  for opt, val in opts:
    with Pfx(opt):
      if opt == '-L':
        listen_addrport = val
        try:
          listen_addrport = parse_addrport(listen_addrport)
        except ValueError as e:
          warning("%s", e)
          badopts = True
      elif opt == '-P':
        default_proxy_addrport = val
        try:
          default_proxy_addrport = parse_addrport(default_proxy_addrport)
        except ValueError as e:
          warning("%s", e)
          badopts = True
      else:
        error("unrecognised option")
        badopts = True

  if argv:
    warning("extra arguments after options: %r", argv)
    badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  P = MetaProxy(listen_addrport, default_proxy_addrport, parallel=1)
  P.serve_forever()
  return 0

def parse_addrport(addrport):
  ''' Parse addr:port into address string and port number.
  '''
  with Pfx("parse_addrport(%s)", addrport):
    try:
      addr, port = addrport.rsplit(':', 1)
    except ValueError:
      raise ValueError("invalid address:port, no colon")
    else:
      try:
        port = int(port)
      except ValueError:
        raise ValueError("invalid address:port, port must be an integer")
    return addr, port

def parse_http_proxy(envval):
  ''' Parse the value of $http_proxy or similar; return addr, port.
  '''
  with Pfx("parse_http_proxy(%s)", envval):
    if envval.startswith('http://'):
      envval_addrport, offset = get_other_chars(envval, 7, '/')
      if envval.endswith('/', offset):
        return parse_addrport(envval_addrport)
      else:
        raise ValueError("missing trailing slash")
    else:
      raise ValueError("does not start with 'http://'")

def openpair(fd):
  ''' Open the supplied file descriptor `fd` for read and open a dup() of it for write.
      The file descriptor itself must support read and write.
  '''
  with Pfx("openpair(%d)", fd):
    S = os.fstat(fd)
    if stat.S_ISREG(S.st_mode):
      warning("fd (%d) is a regular file; openpair will not make well behaved file objects", fd)
    fd2 = os.dup(fd)
    fpin = os.fdopen(fd, "rb")
    fpout = os.fdopen(fd2, "wb")
    return fpin, fpout

class MetaProxy(socketserver.TCPServer):

  def __init__(self, listen_addrport, default_proxy_addrport, parallel=DEFAULT_PARALLEL):
    self.listen_addrport = listen_addrport
    self.default_proxy_addrport = default_proxy_addrport
    self.allow_reuse_address = True
    socketserver.TCPServer.__init__(
            self,
            listen_addrport,
            MetaProxyHandler
          )
    self.later = Later(parallel)
    self.later.open()
    self.name = "MetaProxy(%s)" % (listen_addrport,)
    self.tagseq = Seq()
    self.cache = MetaProxyCache()

  def __str__(self):
    return self.name

  def shutdown(self):
    self.later.close()
    socketserver.TCPServer.shutdown(self)

  # TODO: just use a semaphore? or will we want prioritisation later anyway?
  def process_request(self, rqf, client_addr):
    handler = self.RequestHandlerClass(rqf, client_addr, self)
    self.later.defer(self._process_request, handler, rqf, client_addr)

  def _process_request(self, handler, rqf, client_addr):
    with LogExceptions():
      try:
        handler.handle(rqf, client_addr)
        self.shutdown_request(rqf)
      except:
        self.handle_error(rqf, client_addr)
        self.shutdown_request(rqf)

  def newtag(self, prefix):
    ''' Allocate a new tag for labelling requests.
    '''
    n = next(self.tagseq)
    return "%s-%d" % (prefix, n)

  def should_cache(self, RQ):
    ''' Test whether a request should be cached.
    '''
    uri = RQ.req_uri
    if ( uri.endswith('/')
      or uri.endswith('.js')
      or uri.endswith('.jpg')
      or uri.endswith('.gif')
       ):
      return True
    return True
    ##return False

  def probe_cache(self, RQ):
    ''' Probe the cache for this request. Return (node, fpin, fpout).
        If the URI is in the cache, return the node and fpin a file
        object open for read on the cached response.
        If the URI is new to the cache and this is the first request,
        fpin will be None and fpout will be open for write on a
        file which will become the cached response.
        If the URI is new to the cache but a fetch is already in
        process, fpin and fpout will be None and a call to the node
        will return an fpin when the fetch underway is complete.
    '''
    cache = self.cache
    fpin = None
    fpout = None
    with cache.lock:
      N = cache.find_node(RQ)  # may be None if not cached
      if N and N.ready:
        # in cache, access the cached response
        fpin = N.readfrom()
      else:
        # not in cache
        if self.should_cache(RQ):
          # allocate node and start temp file
          N = cache.make_node(RQ)
          fpout = N.writeto()
        else:
          # else should not cache, don't bother
          ##X("... and should not cache")
          pass
    return N, fpin, fpout

class MetaProxyHandler(socketserver.BaseRequestHandler):
  ''' Request handler class for MetaProxy TCPServers.
  '''

  def __init__(self, sockobj, localaddr, proxy):
    with Pfx("sockobj=%r", sockobj):
      socketserver.BaseRequestHandler(sockobj, localaddr, proxy)
      self.sockobj = sockobj
      self.proxy = proxy
      self.localaddr = localaddr

  def __str__(self):
    return "%s.MetaProxyHandler(%s<==>%s)" % (self.proxy,
                                              self.localaddr,
                                              self.remoteaddr)

  @property
  def remoteaddr(self):
    sockobj = self.sockobj
    try:
      return sockobj.getpeername()
    except OSError as e:
      warning(".remoteaadr: getpeername(sockobj=%r): %s", sockobj, e)
      return None

  def handle(self, rqf, client_addr):
    ''' Handle a connection to the proxy.
    '''
    fpin, fpout = openpair(rqf.fileno())
    self._proxy_requests(_NoCloseFile(fpin), _NoCloseFile(fpout))
    fpin.close()
    fpout.close()

  def _proxy_requests(self, fpin, fpout):
    ''' Process multiple HTTP proxy requests on the same pair of data streams.
    '''
    client_tag = self.proxy.newtag("client")
    with Pfx(client_tag):
      while True:
        try:
          rq_fields = read_http_request_line(fpin)
        except ConnectionResetError as e:
          warning("%s", e)
          break
        rq_method, rq_uri, rq_http_version = rq_fields
        X("GOT NEXT REQUEST: %r", rq_fields)
        if rq_method is None:
          # end of client requests
          if rq_uri is not None or rq_http_version is not None:
            warning("rq_method is None but rq_uri, rq_http_version = %r, %r",
                rq_uri, rq_http_version)
          break
        with Pfx("%s %s %s", rq_method, rq_uri, rq_http_version):
          RQ = URI_Request(self, rq_method, rq_uri, rq_http_version)
          with Pfx(str(RQ)):
            uri_scheme, uri_tail = rq_uri.split(':', 1)
            req_header_data, req_headers = read_headers(fpin)
            RQ.req_header_data = req_header_data
            RQ.req_headers = req_headers
            rsp_fpout = fpout
            N, cache_fpin, cache_fpout = self.proxy.probe_cache(RQ)
            if cache_fpin and cache_fpout:
              warning("both cache_fpin and cache_fpout !!")
            if N:
              if cache_fpin:
                S = os.fstat(cache_fpin.fileno())
                if not stat.S_ISREG(S.st_mode):
                  warning("cache_fpin not attached to a regular file")
                nbytes = S.st_size
                copy_data(cache_fpin, fpout, nbytes)
                cache_fpin.close()
                cache_fpin = None
                fpout.flush()
                info("return CACHED")
                continue
            info("NOT CACHED")
            if cache_fpout:
              # arrange to copy response to the cache
              rsp_fpout = Tee(rsp_fpout, cache_fpout)
            proxy_addrport = self.choose_proxy(RQ)
            with Pfx("proxy_addrport = %r", proxy_addrport):
              try:
                upstream = socket.create_connection(proxy_addrport)
              except Exception as e:
                error("%r: socket.create_connection: %s", proxy_addrport, e)
                RQ.respond(rsp_fpout, '500', 'connect to upstream %r: %s' % (proxy_addrport, e))
                continue
              fpup_in, fpup_out = openpair(upstream.fileno())
              Tdownstream = Thread(name="%s: copy from upstream" % (client_tag,),
                                   target=RQ.pass_response,
                                   args=(client_tag, fpup_in, rsp_fpout))
              Tdownstream.daemon = True
              Tdownstream.start()
              RQ.pass_request(fpin, fpup_out)
              fpup_out.flush()
              Tdownstream.join()
              # disconnect from proxy
              fpup_out.close()
              fpup_in.close()
              upstream.close()
              upstream = None
              debug("upstream closed")
            rsp_fpout.flush()
            if cache_fpout:
              if RQ.rsp_cache_ok:
                cache_fpout.close()
              else:
                cache_fpout.cancel()
              cache_fpout = None
              # forget Tee instance
              rsp_fpout = None

      ##info("end proxy requests")

  def choose_proxy(self, RQ):
    ''' Decide where to connect to deliver the request `RQ`.
    '''
    parts = urlparse(RQ.req_uri)
    host = parts.netloc
    port = parts.port
    if port is None:
      port = socket.getservbyname(parts.scheme, 'tcp')
    proxy_addrport = self.proxy.default_proxy_addrport
    if proxy_addrport is None:
      # look up $http_proxy or other suitable envvar
      envvar = parts.scheme + '_proxy'
      proxyval = os.environ.get(envvar)
      if proxyval is not None:
        with Pfx("$%s", envvar):
          try:
            proxy_addrport = parse_http_proxy(proxyval)
          except ValueError as e:
            warning("invalid value ignored: %s", e)
    if proxy_addrport is None:
      # direct connection
      proxy_addrport = host, port
    info("choose_proxy: %s:%s", *proxy_addrport)
    return proxy_addrport

class URI_Request(NS):

  def __init__(self, handler, method, uri, version):
    ''' An object for tracking state of a request.
    '''
    self.handler = handler
    self.name = handler.proxy.newtag("rq")
    self.req_method = method
    self.req_uri = uri
    self.req_http_version = version
    self.rsp_cache_ok = False

  def __str__(self):
    return "%s[%s:%s]" % (self.name, self.req_method, self.req_uri)

  @property
  def req_has_body(self):
    return message_has_body(self.req_headers)

  @property
  def rsp_has_body(self):
    ''' Does this response have a message body to forward?
        See RFC2616, part 4.3.
    '''
    if self.req_method == 'HEAD':
      return False
    code = self.rsp_code
    if code.startswith('1') or code == '204' or code == '304':
      return False
    return True

  def pass_request(self, fpin, fpout):
    for s in self.req_method, ' ', self.req_uri, ' ', self.req_http_version:
      fpout.write(enc8(s))
    fpout.write(CRLFb)
    fpout.write(self.req_header_data)
    fpout.write(CRLFb)
    with Pfx("request body"):
      transfer_encoding = self.req_headers.get('Transfer-Encoding')
      content_length = self.req_headers.get('Content-Length')
      if transfer_encoding is not None:
        transfer_encoding = transfer_encoding.strip().lower()
        if transfer_encoding == 'identity':
          debug("pass_identity")
          self.pass_identity(fpin, fpout)
        else:
          hdr_trailer = self.req_headers.get('Trailer')
          debug("pass_chunked")
          pass_chunked(fpin, fpout, hdr_trailer)
      elif content_length is not None:
        length = int(content_length.strip())
        debug("pass_length")
        pass_length(fpin, fpout, length)
      else:
        debug("no body expected")

  def pass_response(self, client_tag,  fpin, fpout):
    with Pfx("%s: %s: pass_response", client_tag, self):
      elapsed, bline = time_func(fpin.readline)
      line = dec8(bline)
      if not line.endswith(CRLF):
        raise ValueError("truncated response (no CRLF): %r" % (line,))
      http_version, status_code, reason = line.split(' ', 2)
      reason = reason.rstrip()
      info("server response: %s %s %s", http_version, status_code, reason)
      self.rsp_http_version = http_version
      self.rsp_status_code = status_code
      self.rsp_reason = reason
      fpout.write(bline)
      rsp_header_data, rsp_headers = read_headers(fpin)
      ##info("response headers:\n%s\n", rsp_headers.as_string())
      self.rsp_header_data = rsp_header_data
      self.rsp_headers = rsp_headers
      fpout.write(rsp_header_data)
      fpout.write(CRLFb)
      with Pfx("response body"):
        if ( self.req_method == 'HEAD'
          or status_code.startswith('1')
          or status_code == '204'
          or status_code == '304'
           ):
          debug("none expected")
        else:
          transfer_encoding = rsp_headers.get('Transfer-Encoding')
          content_length = rsp_headers.get('Content-Length')
          if content_length is None:
            length = None
          else:
            length = int(content_length.strip())
          P = Progress(total=length)
          profpout = ProgressWriter(P, fpout)
          if transfer_encoding is not None:
            transfer_encoding = transfer_encoding.strip().lower()
            if transfer_encoding == 'identity':
              debug("transfer_encoding = %r, using pass_identity", transfer_encoding)
              self.pass_identity(fpin, profpout)
            else:
              debug("transfer_encoding = %r, using pass_chunked", transfer_encoding)
              hdr_trailer = rsp_headers.get('Trailer')
              debug("trailer = %r", hdr_trailer)
              pass_chunked(fpin, profpout, hdr_trailer)
          elif content_length is not None:
            debug("content_length = %r, using pass_length", content_length)
            pass_length(fpin, profpout, length)
          else:
            debug("no body expected")
      # set self.rsp_cache_ok based on completion of response and status code
      info("PASS_RESPONSE: body complete, status_code=%r, set self.rsp_cache_ok", status_code)
      self.rsp_cache_ok = ( self.req_method == 'GET'
                        and ( status_code == '200' or status_code == '404' )
                          )

  def respond(self, fpout, code, infotext, headers=None, body=None):
    ''' Send an HTTP response to fpout.
    '''
    fpout.write(code.enc8())
    fpout.write(' '.enc8())
    fpout.write(infotext.enc8())
    fpout.write(CRLFb)
    if headers is not None:
      fpout.write(headers.as_string().enc8())
    fpout.write(CRLFb)

class MetaProxyCache(NS):
  ''' Access to a cache directory.
  '''

  def __init__(self, cache_dir=None):
    if cache_dir is None:
      cache_dir = envsub('$HOME/var/metaproxy')
    self.cache_dir = cache_dir
    X("cache_dir = %r", self.cache_dir)
    # create cache dir if missing, but not an arbitrarily deep tree
    if not os.path.isdir(cache_dir):
      os.mkdir(cache_dir)
    self._nodes = {}
    self._lock = RLock()
    self.lock = self._lock

  def nodekey(self, RQ):
    return RQ.req_method, RQ.req_uri

  @locked
  def find_node(self, RQ):
    ''' Find and return the CacheNode associated with the URI_Request `RQ`.
        Return None if there is no existing node for this request.
    '''
    key = self.nodekey(RQ)
    N = self._nodes.get(key)
    if N is None:
      N = CacheNode(self, *key)
      if N.ready:
        # keep the Node and return it
        self._nodes[key] = N
      else:
        N = None
    elif not N.exists():
      N = None
    return N

  @locked
  def make_node(self, RQ):
    ''' Find and return the CacheNode associated with the URI_Request `RQ`.
        Create the node if missing.
        The CacheNode may or may not be .ready.
    '''
    key = self.nodekey(RQ)
    N = self._nodes.get(key)
    if N is None:
      N = self._nodes[key] = CacheNode(self, *key)
      if N.key != key:
        warning("%s.key=%r but MetaProxyCache key for (%r,%r)=%r",
                N, N.key, RQ.req_method, RQ.req_uri, key)
    return N

class CacheNode(NS):
  ''' A node within a MetaProxyCache.
  '''

  def __init__(self, cache, method, uri):
    self.cache = cache
    self.method = method
    self.uri = uri
    self._cache_async = None
    self._cache_path = None
    path = self.cachepath
    if os.path.exists(path):
      self._setpath(path)

  def __str__(self):
    return "CacheNode<%s:%s>" % (self.method, self.uri)

  def _setpath(self, path):
    self._cache_path = path

  def __call__(self):
    ''' Return a file open for read at the start of the response cache.
    '''
    async = self._cache_async
    if async is not None:
      # caching in progress; block awaiting completion, return result
      return async()
    return self.readfrom()

  @property
  def key(self):
    return self.method, self.uri

  @property
  def ready(self):
    if self._cache_path:
      return True
    if not self._cache_async:
      self._cache_async = Asynchron()
    return self._cache_async.ready

  def exists(self):
    ''' Test whether the backing file exists.
    '''
    return os.path.exists(self.cachepath)

  @property
  def cachepath(self):
    ''' Compute the cache subpath for a URI.
        Scratch files for foo/bah are made as foo/.tmp*
        A dir part with a dot in it gets '.dir' appended.
        A file with no dot or ending in '.file' or '.dir' gets '.file' appended.
    '''
    up = urlparse(self.uri)
    scheme = up.scheme
    if scheme == 'http':
      port = up.port
      if port is None:
        port = 80
    else:
      raise ValueError("only http:// URIs supported")
    path = up.path
    isdir = not path or path.endswith('/')
    subpaths = [ subpath for subpath in path.split('/') if subpath ]
    if not subpaths:
      path = '.dir'
      subpaths2 = [ '.dir' ]
    else:
      lastpath = subpaths.pop(-1)
      if lastpath.endswith('.file') or '.' not in lastpath:
        lastpath += '.file'
      subpaths2 = []
      for subpath in subpaths:
        if '.' in subpath:
          subpath += '.dir'
        subpaths2.append(subpath)
      subpaths2.append(lastpath)
    path = os.path.join(self.cache.cache_dir,
                        "%s:%s" % (self.method, scheme),
                        "%s:%d" % (up.hostname, port),
                        *subpaths2)
    if up.params:
      path += ';' + up.params
    if up.query:
      path += '&' + up.query
    return path

  def start_cache_file(self):
    return self._cache_fp

  def readfrom(self):
    ''' Return a file open for read on the cached response.
    '''
    return open(self._cache_path, 'rb')

  def writeto(self):
    ''' Write new content to this cache node, return file.
    '''
    wfp = _NewCacheFile(self)
    if self._cache_async is not None:
      raise RuntimeError("%s._cache_async already underway" % (self,))
    self._cache_async = Asynchron()
    return wfp

class _NewCacheFile(object):
  ''' A simple file-like object with .write and .close methods used to accrue a cache file.
  '''

  def __init__(self, node):
    ''' Instantiaite a new cache file for the supplied CacheNode.
        Create a temp file in the target directory and rename the final path on .close.
    '''
    self.node = node
    self.finalpath = finalpath = node.cachepath
    cachesubdir = os.path.dirname(finalpath)
    if not os.path.isdir(cachesubdir):
      try:
        os.makedirs(cachesubdir)
      except FileExistsError as e:
        # catch racy directory creation
        if not os.path.isdir(cachesubdir):
          raise
    fd, self.tmppath = mkstemp(prefix='.tmp', dir=cachesubdir, text=False)
    self.fp = os.fdopen(fd, 'wb')

  def write(self, data):
    ''' Transcribe data to the temp file.
    '''
    return self.fp.write(data)

  def flush(self):
    ''' Flush buffered data to the temp file.
    '''
    return self.fp.flush()

  def cancel(self):
    ''' Cancel the transcription to the temp file and discard.
    '''
    info("_NewCacheFile: CANCEL, discard cached response...")
    self.node._cache_async.cancel()
    self.fp.close()
    self.fp = None
    os.remove(self.tmppath)

  def close(self):
    ''' Close the output and move the file into place as the cached response.
    '''
    info("_NewCacheFile: CLOSE, install cached response...")
    self.fp.close()
    self.fp = None
    finalpath = self.finalpath
    if os.path.exists(finalpath):
      warning("replacing existing %r", finalpath)
    os.rename(self.tmppath, finalpath)
    X("set %s._cache_path=%r", self.node, finalpath)
    self.node._setpath(finalpath)
    self.node._cache_async.result = finalpath
    self.node._cache_async = None

class ProgressWriter(object):
  ''' An object with a .write method which passes the write through to a file and then updates a Progress.
  '''

  def __init__(self, progress, fp):
    ''' Initialise the ProgressWriter with a Progress `progress` and a file `fp`.
    '''
    self.progress = progress
    self.fp = fp

  def write(self, data):
    ''' Write `data` to the file and update the Progress. Return as from `fp.write`.
        The Progress is updated by the amount written; if fp.write
        returns None then this presumed to be len(data), otherwise
        the return value from fp.write is used.
    '''
    retval = self.fp.write(data)
    if retval is None:
      written = len(data)
    else:
      written = retval
    self.progress.advance(written)
    return retval

class _NoCloseFile(object):

  def __init__(self, fp):
    self._fp = fp

  def __getattr__(self, attr):
    return getattr(self._fp, attr)

  def __iter__(self):
    return iter(self._fp)

  def close(self):
    raise RuntimeError("forbidden .close, ._fp=%r" % (self._fp,))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
