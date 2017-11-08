#!/usr/bin/python
#
# Progress counting.
#   - Cameron Simpson <cs@cskk.id.au> 15feb2015
#

from collections import namedtuple
import time
from cs.logutils import warning, exception
from cs.seq import seq
from cs.x import X

CheckPoint = namedtuple('CheckPoint', 'time position')

class Progress(object):
  ''' A progress counter to track task completion with various utility methods.
  '''

  def __init__(self, total=None, start=0, position=None, start_time=None, throughput_window=None, name=None):
    ''' Initialise the Progesss object.
        `total`: expected completion value, default None.
        `start`: starting position of progress range, default 0.
        `position`: initial position, default from `start`.
        `start_time`: start time of the process, default now.
        `throughput_window`: length of throughput time window, default None.
    '''
    if name is None:
      name = 'Progress-%d' % (seq(),)
    now = time.time()
    if start is None:
      start = 0
    if position is None:
      position = start
    if start_time is None:
      start_time = now
    elif start_time > now:
      raise ValueError("start_time(%s) > now(%s)", start_time, now)
    if throughput_window is not None and throughput_window <= 0:
      raise ValueError("throughput_window <= 0: %s", throughput_window)
    self.name = name
    self.start = start
    self.total = total
    self.start_time = start_time
    self.throughput_window = throughput_window
    # history of positions, used to compute throughput
    posns = [ CheckPoint(start_time, start) ]
    if position != start:
      posns.append(CheckPoint(now, position))
    self._positions = posns
    self._flushed = True

  @property
  def position(self):
    ''' Latest position.
    '''
    return self._positions[-1][1]

  @position.setter
  def position(self, new_position):
    self.update(new_position)

  def update(self, new_position, update_time=None):
    ''' Record more progress.
    '''
    if update_time is None:
      update_time = time.time()
    ##if new_position < self.position:
    ##  warning("%s.update: .position going backwards from %s to %s",
    ##          self, self.position, new_position)
    self._positions.append( CheckPoint(update_time, new_position) )
    self._flushed = False

  def advance(self, delta, update_time=None):
    ''' Record more progress, return the advanced position.
    '''
    self.update(self.position + delta, update_time=update_time)

  def __iadd__(self, delta):
    self.advance(delta)
    return self

  def _flush(self, oldest=None):
    if oldest is None:
      window = self.throughput_window
      if window is None:
        raise ValueError("oldest may not be None when throughput_window is None")
      oldest = time.time() - window
    posns = self._positions
    # scan for first item still in time window
    for ndx, posn in enumerate(posns):
      if posn.time >= oldest:
        if ndx > 0:
          del posns[0:ndx]
        break
    self._flushed = True

  @property
  def throughput(self):
    ''' Compute current overall throughput.
        If self.throughput_window is not None,
        calls self.self.throughput_recent(throughput_window).
    '''
    throughput_window = self.throughput_window
    if throughput_window is not None:
      return self.throughput_recent(throughput_window)
    consumed = self.position - self.start
    if consumed < 0:
      warning("%s.throughput: self.position(%s) < self.start(%s)",
              self, self.position, self.start)
    if consumed == 0:
      return 0
    now = time.time()
    elapsed = now - self.start_time
    if elapsed == 0:
      return 0
    if elapsed <= 0:
      warning("%s.throughput: negative elapsed time since start_time=%s: %s",
              self, self.start_time, elapsed)
      return 0
    return consumed / elapsed

  def throughput_recent(self, time_window):
    ''' Recent throughput within a time window.
        The time span overlapping the start of the window is included
        on a flat pro rata basis.
    '''
    if time_window <= 0:
      raise ValueError("%s.throughput_recent: invalid time_window <= 0: %s",
                       self, time_window)
    if not self._flushed:
      self._flush()
    now = time.time()
    time0 = now - time_window
    if time0 < self.start_time:
      time0 = self.start_time
    # lowest time and position
    # low_time will be time0
    # low_pos will be the matching position, probably interpolated
    low_time = None
    low_pos = None
    # walk forward through the samples, assumes monotonic
    for t, p  in self._positions:
      if t >= time0:
        low_time = t
        low_pos = p
        break
    if low_time is None:
      # no samples within the window; caller might infer stall
      return 0
    if low_time >= now:
      # in the future? warn and return None
      warning('low_time=%s >= now=%s', low_time, now)
      return 0
    rate = (self.position - low_pos) / (now - low_time)
    if rate < 0:
      warning('rate < 0 (%s)', rate)
    return rate

  @property
  def projected(self):
    ''' Return the projected time to end based on the current throughput and the total.
    '''
    total = self.total
    if total is None:
      return None
    remaining = total - self.position
    if remaining < 0:
      warning("%s.eta: self.position(%s) > self.total(%s)",
              self, self.position, self.total)
      return None
    throughput = self.throughput
    if throughput is None or throughput == 0:
      return None
    return remaining / throughput

  @property
  def eta(self):
    ''' Return the estimated time of completion.
    '''
    runtime = self.projected
    if runtime is None:
      return None
    return time.time() + runtime

class ProgressWriter(object):
  ''' An object with a .write method which passes the write through to a file and then updates a Progress.
  '''

  def __init__(self, progress, fp):
    ''' Initialise the ProgressWriter with a Progress `progress` and a file `fp`.
    '''
    Proxy.__init__(self, fp)
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

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.progress_tests')
