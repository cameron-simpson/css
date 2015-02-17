#!/usr/bin/python
#
# Progress counting.
#   - Cameron Simpson <cs@zip.com.au> 15feb2015
#

import time
from cs.logutils import warning

class Progress(object):
  ''' A progress counter to track task completion with various utility functions.
  '''

  def __init__(self, total=None, start=0, position=None, start_time=None, throughput_window=None):
    ''' Initialise the Progesss object.
        `total`: expected completion value, default None.
        `start`: starting position of progress range, default 0.
        `position`: initial position, default from `start`.
        `start_time`: start time of the process, default now.
        `throughput_window`: length of throughput time window, default None.
    '''
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
    self.start = 0
    self.total = total
    self.start_time = start_time
    self.throughput_window = throughput_window
    # history of positions, used to compute throughput
    posns = [ (start_time, start) ]
    if position != start:
      posns.append( (position, now) )
    self._positions = posns

  @property
  def position(self):
    ''' Latest position.
    '''
    return self._positions[-1][1]

  def update(self, new_position, update_time=None):
    ''' Record more progress.
    '''
    if update_time is None:
      update_time = time.time()
    if new_position < self.position:
      warning("%s.update: .position going backwards from %s to %s",
              self, self.position, position)
    self._positions.append( (update_time, new_position) )

  def advance(self, delta, update_time=None):
    ''' Record more progress.
    '''
    return self.update(self.position + delta, update_time=update_time)

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
      return None
    if elapsed <= 0:
      warning("%s.throughput: negative elapsed time since start_time=%s: %s",
              self, self.start_time, elapsed)
      return None
    return consumed / elapsed

  def throughput_recent(self, time_window):
    ''' Recent throughput within a time window.
        The time span overlapping the start of the window is included
        on a flat pro rata basis.
    '''
    if time_window <= 0:
      raise ValueError("%s.throughput_recent: invalid time_window <= 0: %s",
                       self, time_window)
    now = time.time()
    time0 = now - time_window
    if time0 < self.start_time:
      time0 = self.start_time
    # lowest time and position
    # low_time will be time0
    # low_pos will be the matching position, probably interpolated
    low_time = None
    low_pos = None
    # walk backward through the samples, assuming monotonic
    for t, p in reversed(self._positions):
      if t >= time0:
        low_time = t
        low_pos = p
        if low_time == time0:
          # hit the bottom of the samples, perfectly aligned
          break
        continue
      # post: t < time0
      if low_time is None:
        # no samples within the window; caller might infer stall
        return None
      # post: low_time > time0
      # compute new low_position between p and low_position
      low_pos = p + (low_pos - p) * (time0 - t) / (low_time - t)
      low_time = time0
      break
    t, p = self._positions[-1]
    if t <= low_time:
      # return None if negative or zero elapsed time in the span
      return None
    # return average throughput over the span, extending span to now
    # Note that this will decay until the next update
    return (p - low_pos) / (now - low_time)

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
