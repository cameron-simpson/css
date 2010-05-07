#!/usr/bin/python

import os.path
from cs.nodedb.node import Node, NodeDB, Backend

_NodeDBsByURL = {}

def NodeDBFromURL(url, readonly=False):
  ''' Factory method to return singleton NodeDB instances.
  '''
  if url in _NodeDBsByURL:
    return _NodeDBsByURL[url]

  if url.startswith('/'):
    # filesystem pathname
    # recognise some extensions and recurse
    # otherwise reject
    base = os.path.basename(url)
    _, ext = os.path.splitext(base)
    if ext == '.tch':
      return NodeDBFromURL('file-tch://'+url, readonly=readonly)
    if ext == '.sqlite':
      return NodeDBFromURL('sqlite://'+url, readonly=readonly)
    import sys
    print >>sys.stderr, "ext =", ext
    raise ValueError, "unsupported NodeDB URL: "+url

  if url.startswith('file-tch://'):
    from cs.nodedb.tokcab import Backend_TokyoCabinet
    dbpath = url[11:]
    backend = Backend_TokyoCabinet(dbpath, readonly=readonly)
    db = NodeDB(backend, readonly=readonly)
    _NodeDBsByURL[url] = db
    return db

  if url.startswith('sqlite:') or url.startswith('mysql:'):
    # TODO: direct sqlite support, skipping SQLAlchemy?
    from cs.nodedb.sqla import Backend_SQLAlchemy
    dbpath = url
    backend = Backend_SQLAlchemy(dbpath, readonly=readonly)
    db = NodeDB(backend, readonly=readonly)
    _NodeDBsByURL[url] = db
    return db

  raise ValueError, "unsupported NodeDB URL: "+url
