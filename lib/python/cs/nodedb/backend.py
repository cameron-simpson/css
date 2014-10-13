#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

from contextlib import contextmanager
from threading import Condition
from collections import namedtuple
import unittest
from cs.logutils import D, OBSOLETE, debug, error
from cs.obj import O
from cs.threads import locked_property
from cs.excutils import unimplemented
from cs.timeutils import sleep
from cs.debug import Lock, RLock, Thread
from cs.py3 import Queue, Queue_Full as Full, Queue_Empty as Empty

# delay between update polls
POLL_DELAY = 0.1

# convenience tuple of raw values
CSVRow = namedtuple('CSVRow', 'type name attr value')

class Backend(O):
  ''' Base class for NodeDB backends.
  '''

  def __init__(self, readonly, monitor=False, raw=False):
    ''' Initialise the Backend.
        `readonly`: this backend is readonly; do not write updates
        `monitor`:  (default False) this backend watches the backing store
                    for updates and loads them as found
        `raw`: if true, this backend does not encode/decode values with
		totext/fromtext; it must do its own reversible
		storage of values. This is probably only appropriate
		for in-memory stores.
    '''
    self.nodedb = None
    self.readonly = readonly
    self.monitor = monitor
    self.raw = raw
    self.closed = False
    self._lock = Lock()
    _BackendUpdateQueue.__init__(self)
    self._ready = Lock()
    self._ready.acquire()

  def __str__(self):
    return "%s(readonly=%s, monitor=%s, raw=%s)" \
           % (self.__class__.__name__, self.readonly, self.monitor, self.raw)

  def nodedata(self):
    ''' Yield node data in:
          type, name, attrmap
        form.
    '''
    for k, attrmap in self.iteritems():
      k1, k2 = k
      yield k1, k2, attrmap

  def _import_nodedata(self):
    ''' Method called by the monitor thread at commencement to load the
        backend data into the NodeDB. Why in the monitor thread?
        Because some libraries like SQLite refuse to work in multiple
        threads :-(
    '''
    with self._updates_off():
      self.nodedb.apply_nodedata(self.nodedata(), raw=self.raw)

  def init_nodedb(self):
    ''' Apply the nodedata from this backend to the NodeDB.
        This can be overridden by subclasses to provide some backend specific
        efficient implementation.
    '''
    self._update_start_thread()
    self._update_ready.acquire()

  def close(self):
    ''' Basic close: sync, detach from NodeDB, mark as closed.
    '''
    self.closed = True
    self.sync()
    self._update_close()
    self.nodedb = None

  def setAttr(self, t, name, attr, values):
    ''' Save the full contents of this attribute list.
    '''
    self.delAttr(t, name, attr)
    if values:
      self.extendAttr(t, name, attr, values)

  def delAttr(self, t, name, attr):
    ''' Delete an attribute.
    '''
    self._update(CSVRow(t, name, '-'+attr, ''))

  def extendAttr(self, t, name, attr, values):
    ''' Append values to an attribute.
    '''
    for value in values:
      self._update(CSVRow(t, name, attr, value))

  def import_csv_row(self, row):
    ''' Apply the values from an individual CSV update row to the NodeDB.
        Each row is expected to be post-resolve_csv_row().
        Honour the incremental notation for data:
        - a NAME commencing with '=' discards any existing (TYPE, NAME)
          and begins anew.
        - an ATTR commencing with '=' discards any existing ATTR and
          commences the ATTR anew
        - an ATTR commencing with '-' discards any existing ATTR;
          VALUE must be empty
        Otherwise each VALUE is appended to any existing ATTR VALUEs.
    '''
    nodedb = self.nodedb
    t, name, attr, value = row
    if attr.startswith('-'):
      # remove attribute
      attr = attr[1:]
      if value != "":
        raise ValueError("ATTR = \"%s\" but non-empty VALUE: %r" % (attr, value))
      N = nodedb.make( (t, name) )
      N[attr] = ()
    else:
      # add attribute
      if name.startswith('='):
        # discard node and start anew
        name = name[1:]
        nodedb[t, name] = {}
      N = nodedb.make( (t, name) )
      if attr.startswith('='):
        # reset attribute completely before appending value
        attr = attr[1:]
        N[attr] = ()
      N.get(attr).append(value)

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
