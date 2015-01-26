#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

from contextlib import contextmanager
from threading import Condition
from collections import namedtuple
import unittest
from cs.logutils import D, OBSOLETE, debug, error, X
from cs.threads import locked, locked_property
from cs.excutils import unimplemented
from cs.timeutils import sleep
from cs.debug import RLock, Thread
from cs.obj import O
from cs.py3 import Queue, Queue_Full as Full, Queue_Empty as Empty

# convenience tuple of raw values, actually used to encode updates
# via Backend.import_csv_row
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
    self._lock = RLock()     # general mutex
    self._update_count = 0

  def __str__(self):
    return "%s(readonly=%s, monitor=%s, raw=%s)" \
           % (self.__class__.__name__, self.readonly, self.monitor, self.raw)

  def init_nodedb(self):
    ''' Apply the nodedata from this backend to the NodeDB.
        This can be overridden by subclasses to provide some backend specific
        efficient implementation.
    '''
    raise NotImplementedError("method to do initial db load from Backend")

  def close(self):
    ''' Basic close: sync, detach from NodeDB, mark as closed.
    '''
    raise NotImplementedError("method to shutdown backend, set .nodedb=None, etc")

  def _update(self, csvrow):
    ''' Update the actual backend with a difference expressed as a CSVRow.
        The values are as follows:
          .type, .name  The Node key.
          .attr         If this commences with a dash ('-') the attribute
			            values are to be discarded. Otherwise, the value is
                        to be appended to the attribute.
          .value        The value to store, already textencoded.
    '''
    raise NotImplementedError("method to update the backend from a CSVRow with difference information")

  @property
  def update_count(self):
    ''' Return the update count, an monotinoic increasing counter for deciding whether a derived data structure is out of date.
    '''
    return self._update_count

  @locked
  def setAttr(self, t, name, attr, values):
    ''' Save the full contents of this attribute list.
    '''
    self.delAttr(t, name, attr)
    if values:
      self.extendAttr(t, name, attr, values)

  @locked
  def delAttr(self, t, name, attr):
    ''' Delete an attribute.
    '''
    self._update(CSVRow(t, name, '-'+attr, ''))

  @locked
  def extendAttr(self, t, name, attr, values):
    ''' Append values to an attribute.
    '''
    for value in values:
      self._update(CSVRow(t, name, attr, value))

  def import_csv_row(self, row):
    ''' Apply the values from an individual CSV update row to the NodeDB without propagating to the backend.
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
      N[attr]._set_values_local( () )
    else:
      # add attribute
      if name.startswith('='):
        # discard node and start anew
        name = name[1:]
        key = t, name
        if key in nodedb:
          nodedb[t, name]._scrub_local()
      N = nodedb.make( (t, name) )
      if attr.startswith('='):
        # reset attribute completely before appending value
        attr = attr[1:]
        N[attr]._set_values_local( (value,) )
      else:
        N.get(attr)._extend_local( (value,) )

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
