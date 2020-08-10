#!/usr/bin/env python3

''' HTTP access to a Store.
'''

import os
import sys
from flask import (
    Flask, render_template, request, session as flask_session, jsonify, abort
)
from cs.logutils import warning
from cs.resources import RunStateMixin
from . import defaults
from .block import IndirectBlock
from .hash import HashCode, MissingHashcodeError

def main(argv=None):
  if argv is None:
    argv = sys.argv
  argv = list(argv)
  name = argv.pop(0)
  if argv:
    host, port = argv
  else:
    host, port = '127.0.0.1', 5000
  app = StoreApp(name, defaults.S)
  app.run(host=host, port=port)

class _StoreApp(Flask, RunStateMixin):
  ''' A Flask application with a `.store` attribute.
  '''

  def __init__(self, name, S):
    Flask.__init__(self, name)
    RunStateMixin.__init__(self)
    self.store = S
    self.secret_key = os.urandom(16)

  def run(self, *a, **kw):
    ''' Call the main Flask.run inside the RunState.
    '''
    with self.runstate:
      with self.store:
        super().run(*a, **kw)

def StoreApp(name, S):
  ''' Factory method to create the app and attach routes.
  '''
  app = _StoreApp(name, S)

  @app.route('/h/<hashname>/<hashcode_s>')
  def h(hashname, hashcode_s):
    ''' Return a direct hashcode block.
    '''
    try:
      h = HashCode.from_named_hashbytes_hex(hashname, hashcode_s)
    except ValueError as e:
      warning(
          "HashCode.from_hashbytes_hex(%r,%r): %s", hashname, hashcode_s, e
      )
      abort(404, 'invalid hashcode')
      raise RuntimeError("NOTREACHED")
    try:
      data = app.store[h]
    except MissingHashcodeError:
      abort(404)
      raise RuntimeError("NOTREACHED")
    rsp = app.make_response(data)
    rsp.headers.set('Content-Type', 'application/octet-stream')
    rsp.headers.set('ETag', h.etag)
    return rsp

  @app.route('/vt/i/<hashname>/<hashcode_s>')
  def i(hashname, hashcode_s):
    ''' Return an indirect hashcode block.
    '''
    try:
      h = HashCode.from_named_hashbytes_hex(hashname, hashcode_s)
    except ValueError as e:
      warning(
          "HashCode.from_hashbytes_hex(%r,%r): %s", hashname, hashcode_s, e
      )
      abort(404, 'invalid hashcode')
      raise RuntimeError("NOTREACHED")
    IB = IndirectBlock.from_hashcode(h, span=None)
    start = 0
    length = max(0, len(IB) - start)
    rsp = Response(IB.datafrom(start=start))
    rsp.headers.set('Content-Type', 'application/octet-stream')
    rsp.headers.set('Content-Length', str(length))
    rsp.headers.set('ETag', '"VTI:' + h.bare_etag) + '"'
    return rsp

  return app

if __name__ == '__main__':
  sys.exit(main(sys.argv))
