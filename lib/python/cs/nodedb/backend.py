#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

from contextlib import contextmanager
from threading import Condition
from collections import namedtuple
import unittest
from cs.logutils import D, OBSOLETE, debug
from cs.obj import O
from cs.threads import locked_property
from cs.excutils import unimplemented
from cs.timeutils import sleep
from cs.debug import Lock, RLock, Thread
from cs.py3 import Queue, Queue_Full as Full, Queue_Empty as Empty

# delay between update polls
POLL_DELAY = 0.1

CSVRow = namedtuple('CSVRow', 'type name attr value')

class _BackendMappingMixin(O):
  ''' A mapping interface to be presented by all Backends.
  '''

  def len(self):
    return len(self.keys())

  @unimplemented
  def iterkeys(self):
    ''' Yield (type, name) tuples for all nodes in the backend database.
    '''
    pass

  def keys(self):
    return list(self.iterkeys())

  def iteritems(self):
    ''' Yield ( (type, name), node_dict ) tuples for all nodes in
        the backend database.
    '''
    for key in self.iterkeys():
      yield key, self[key]

  def items(self):
    return list(self.iteritems())

  def itervalues(self):
    ''' Yield node_dict for all nodes in the backend database.
    '''
    for key in self.iterkeys():
      yield self[key]

  def values(self):
    return list(self.itervalues())

  @unimplemented
  def __getitem__(self, key):
    ''' Return a dict with a mapping of attr => values for the
        specified node key.
    '''
    pass

  def get(self, key, default):
    try:
      value = self[key]
    except KeyError:
      return default
    return value

  @unimplemented
  def __setitem__(self, key, node_dict):
    pass

  @unimplemented
  def __delitem__(self, key):
    pass

  __hash__ = None

  def __eq__(self, other):
    keys = set(self.keys())
    okeys = set(other.keys())
    if keys != okeys:
      raise Error
      ##print >>sys.stderr, "1: keys[%s] != okeys[%s]" % (keys, okeys)
      ##sys.stderr.flush()
      return False
    for k in keys:
      if self[k] != other[k]:
        raise Error
        ##print >>sys.stderr, "2: %s != %s" % (self[k], other[k])
        ##sys.stderr.flush()
        return False
    return True

  def __ne__(self, other):
    return not self == other

class _BackendUpdateQueue(O):
  ''' Mixin to supply the update queue and associated facilities.
  '''

  def __init__(self):
    self._update_lock = RLock()
    self._queue_updates = not self.readonly
    self._update_count = 0      # updates queued
    self._updated_count = 0     # updates applied
    self._update_cond = Condition(self._update_lock)
    if self.monitor or not self.readonly:
      self._updateQ = Queue(1024)
      self._update_thread = self._start_update_thread()

  def _start_update_thread(self):
    ''' Construct and start the update thread.
    '''
    T = Thread(name="%s._update_thread" % (self,),
               target=self._update_monitor,
               args=(self._updateQ,))
    debug("start monitor thread...: %s", T)
    T.start()
    return T

  @contextmanager
  def _updates_off(self):
    ''' A context manager to turn off updates of the backend.
        This is used when loading updates from other sources.
    '''
    with self._update_lock:
      o_updates = self._queue_updates
      self._queue_updates = False
      yield
      self._queue_updates = o_updates

  def _update(self, row):
    ''' Queue the supplied row (TYPE, NAME, ATTR, VALUE) for the backend update thread.
    '''
    if self._queue_updates:
      if self.readonly:
        warning("readonly: do not queue %r", row)
      elif self.closed:
        raise RuntimeError("%s closed: not queuing %r" % (self, row))
      else:
        debug("queue %r", row)
        self._updateQ.put(row)
        with self._lock:
          self._update_count += 1

  def sync(self):
    ''' Wait for the update queue to complete to the current update_count.
    '''
    if not self._update_thread:
      return
    with self._lock:
      update_count = self._update_count
    while True:
      with self._update_lock:
        debug("polling self._updated_count (%d) - need (%d)", self._updated_count, update_count)
        ready = self._updated_count >= update_count
        if ready:
          break
        debug("sync: not ready, waiting for another notification")
        self._update_cond.wait()

  def _update_monitor(self, updateQ, delay=POLL_DELAY):
    ''' Watch for updates from the NodeDB and from the backend.
    '''
    if not self.monitor:
      # not watching for other updates
      # just read update queue and apply
      while not self.closed:
        row = updateQ.get()
        self._update_push(updateQ, delay, row0=row)
      return

    # poll file regularly for updates
    while True:
      # run until self.closed and updateQ.empty
      # to ensure that all updates get written to backend
      is_empty = updateQ.empty()
      if is_empty:
        if self.closed:
          break
        if not self.monitor:
          return
        # poll for other updates
        self._update_fetch()
      elif not self.readonly:
        # apply our updates
        self._update_push(updateQ, delay)
      sleep(delay)

  def _update_fetch(self):
    ''' Read updates from the backing store
        and update the NodeDB accordingly.
    '''
    with self._update_lock:
      with self._updates_off():
        for row in self.fetch_updates():
          self.apply_row(row)

  def _update_push(self, updateQ, delay, row0=None):
    ''' Copy current updates from updateQ and append to the backing store.
        Process:
          take data lock
            catch up on outside updates
            write our updates
          release data lock
    '''
    if self.readonly:
      raise RuntimeError("_update_push called but we are readonly!")
    if updateQ.empty():
      error("_update_push: updateQ is empty! should not happen!")
      return
    with self._update_lock:
      with self.lockdata():
        D("_update_push: read other updates...")
        self._update_fetch()
        def sendrows():
          if row0:
            yield row0
          while True:
            try:
              row = updateQ.get(True, delay)
            except Empty:
              break
            yield row
        self.push_updates(sendrows())
        # alert sync() users that updates have been committed
        self._update_cond.notify_all()

class Backend(_BackendMappingMixin, _BackendUpdateQueue):
  ''' Base class for NodeDB backends.
  '''

  def __init__(self, readonly, monitor=False):
    ''' Initialise the Backend.
        `readonly`: this backend is readonly; do not write updates
        `monitor`:  (default False) this backend watches the backing store
                    for updates and loads them as found
    '''
    self.nodedb = None
    self.readonly = readonly
    self.monitor = monitor
    self.closed = False
    self._update_thread = None
    _BackendUpdateQueue.__init__(self)
    self._lock = Lock()

  def nodedata(self):
    ''' Yield node data in:
          type, name, attrmap
        form.
    '''
    for k, attrmap in self.iteritems():
      k1, k2 = k
      yield k1, k2, attrmap

  def init_nodedb(self):
    ''' Apply the nodedata from this backend to the NodeDB.
        This can be overridden by subclasses to provide some backend specific
        efficient implementation.
    '''
    nodedb = self.nodedb
    with self._updates_off():
      nodedb.apply_nodedata(self.nodedata())

  def close(self):
    ''' Basic close: sync, detach from NodeDB, mark as closed.
    '''
    self.closed = True
    self.sync()
    if self._update_thread:
      self._update_thread.join()
    self.nodedb = None

  def _reload_nodedb(self):
    ''' Toss all the data in the NodeDB and reload.
    '''
    with self._updates_off():
      self.nodedb._scrub()
      self.init_nodedb()

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

  def apply_csv_row(self, row):
    ''' Apply the values from an individual CSV update row.
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
      N.get(attr).append(nodedb.fromtext(value))

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
