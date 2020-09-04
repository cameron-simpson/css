#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@cskk.id.au>
#

from contextlib import contextmanager
from threading import Condition
from collections import namedtuple
from types import SimpleNamespace as NS
import unittest
from cs.debug import RLock, Thread
from cs.excutils import unimplemented
from cs.logutils import D, debug, error
from cs.pfx import pfx_method
from cs.py3 import Queue, Queue_Full as Full, Queue_Empty as Empty
from cs.threads import locked, locked_property
from cs.timeutils import sleep
from cs.x import X

# a db update
_Update = namedtuple('_Update', 'do_append type name attr values')

class Update(_Update):

  @classmethod
  def from_csv_row(cls, row):
    ''' Decode a CSV row into a Update instances (one row may be represented by multiple Updates); yields Updates.
        Honour the incremental notation for data:
        - a NAME commencing with '=' discards any existing (TYPE, NAME)
          and begins anew.
        - an ATTR commencing with '=' discards any existing ATTR and
          commences the ATTR anew
        - an ATTR commencing with '-' discards any existing ATTR;
          VALUE must be empty
        Otherwise each VALUE is appended to any existing ATTR VALUEs.
    '''
    t, name, attr, value = row
    if name.startswith('='):
      # reset Node, optionally commence attribute
      yield ResetUpdate(t, name[1:])
      if attr != "":
        yield ExtendUpdate(t, name[1:], attr, (value,))
    elif attr.startswith('='):
      yield ExtendUpdate(t, name, attr[1:], (value,))
    elif attr.startswith('-'):
      if value != "":
        raise ValueError("reset CVS row: value != '': %r" % (row,))
      yield ResetUpdate(t, name, attr[1:])
    else:
      yield ExtendUpdate(t, name, attr, (value,))

  def to_csv(self):
    ''' Transform an Update into row data suitable for CSV.
    '''
    do_append, t, name, attr, values = self
    if do_append:
      # straight value appends
      for value in values:
        yield t, name, attr, value
    else:
      if attr is None:
        # reset whole Node
        if values:
          raise ValueError("values supplied when attr is None: %r" % (values,))
        yield t, '=' + name, "", ""
      else:
        # reset attr values
        if values:
          # reset values
          first = True
          for value in values:
            if first:
              yield t, name, '=' + attr, value
              first = False
            else:
              yield t, name, attr, value
        else:
          # no values - discard whole attr
          yield t, name, '-' + attr, ""

def ResetUpdate(t, name, attr=None, values=None):
  ''' Return an update to reset a whole Node (t, name) or attribute (t, name).attr if `attr` is not None.
  '''
  if attr is None:
    if values is not None:
      raise ValueError(
          "ResetUpdate: attr is None, but values is %r" % (values,)
      )
    return Update(False, t, name, None, None)
  if values is None:
    values = ()
  else:
    values = tuple(values)
  return Update(False, t, name, attr, values)

def ExtendUpdate(t, name, attr, values):
  ''' Return an update to extend (t, name).attr with the iterable `values`.
  '''
  return Update(True, t, name, attr, tuple(values))

class Backend(NS):
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
    self._lock = RLock()  # general mutex
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
    raise NotImplementedError(
        "method to shutdown backend, set .nodedb=None, etc"
    )

  @pfx_method
  def _update(self, update):
    ''' Update the actual backend with an _Update object expressing a difference.
        The values are as follows:
          .type, .name  The Node key.
          .attr         If this commences with a dash ('-') the attribute
			            values are to be discarded. Otherwise, the value is
                        to be appended to the attribute.
          .value        The value to store, already textencoded.
    '''
    raise NotImplementedError(
        "missing method to update the backend from an _Update with difference information"
    )

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
    self._update(ResetUpdate(t, name, attr))

  @locked
  def extendAttr(self, t, name, attr, values):
    ''' Append values to an attribute.
    '''
    self._update(ExtendUpdate(t, name, attr, values))

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
