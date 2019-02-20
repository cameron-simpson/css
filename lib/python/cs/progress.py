#!/usr/bin/python
#
# Progress counting.
#   - Cameron Simpson <cs@cskk.id.au> 15feb2015
#

''' A progress tracker with methods for throughput, ETA and update notification.
'''

from collections import namedtuple
import functools
import time
from cs.logutils import warning, exception
from cs.seq import seq
from cs.units import transcribe_time, transcribe, BINARY_BYTES_SCALE

DISTINFO = {
    'description': "A progress tracker with methods for throughput, ETA and update notification",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.seq', 'cs.units'],
}

CheckPoint = namedtuple('CheckPoint', 'time position')

@functools.total_ordering
class Progress(object):
  ''' A progress counter to track task completion with various utility methods.

      >>> P = Progress(name="example")
      >>> P                         #doctest: +ELLIPSIS
      Progress(name='example',start=0,position=0,start_time=...,thoughput_window=None,total=None):[CheckPoint(time=..., position=0)]
      >>> P.advance(5)
      >>> P                         #doctest: +ELLIPSIS
      Progress(name='example',start=0,position=5,start_time=...,thoughput_window=None,total=None):[CheckPoint(time=..., position=0), CheckPoint(time=..., position=5)]
      >>> P.total = 100
      >>> P                         #doctest: +ELLIPSIS
      Progress(name='example',start=0,position=5,start_time=...,thoughput_window=None,total=100):[CheckPoint(time=..., position=0), CheckPoint(time=..., position=5)]

      A Progress instance has an attribute ``notify_update`` which
      is a set of callables. Whenever the position is updates, each
      of these will be called with the Progress instance and the
      latest CheckPoint.

      Progress objects also make a small pretense of being an integer.
      The expression `int(progress)` returns the current position,
      and += and -= adjust the position.

      This is convenient for coding, but importantly it is also
      important for discretionary use of a Progress with some other
      object.
      If you want to make a lightweight Progress capable class
      you can set a position attribute to an int
      and manipulate it carefully using += and -= entirely.
      If you decide to incur the cost of maintaining a Progress object
      you can slot it in:

          # initial setup with just an int
          my_thing.amount = 0

          # later, or on some option, use a Progress instance
          my_thing.amount = Progress(my_thing.amount)
  '''

  def __init__(
      self,
      position=None,
      name=None,
      start=None,
      start_time=None, throughput_window=None,
      total=None,
  ):
    ''' Initialise the Progesss object.

        Parameters:
        * `position`: initial position, default 0.
        * `name`: optional name for this instance.
        * `start`: starting position of progress range,
          default from `position`.
        * `start_time`: start time of the process, default now.
        * `throughput_window`: length of throughput time window in seconds,
          default None.
        * `total`: expected completion value, default None.
    '''
    if name is None:
      name = '-'.join( ( str(type(self)), str(seq())) )
    now = time.time()
    if position is None:
      position = 0
    if start is None:
      start = position
    if start_time is None:
      start_time = now
    elif start_time > now:
      raise ValueError("start_time(%s) > now(%s)" % (start_time, now))
    if throughput_window is not None and throughput_window <= 0:
      raise ValueError("throughput_window <= 0: %s" % (throughput_window,))
    self.name = name
    self.start = start
    self._total = total
    self.start_time = start_time
    self.throughput_window = throughput_window
    # history of positions, used to compute throughput
    positions = [ CheckPoint(start_time, start) ]
    if position != start:
      positions.append(CheckPoint(now, position))
    self._positions = positions
    self._flushed = True
    self.notify_update = set()

  def __str__(self):
    return "%s[start=%s:pos=%s:total=%s]" \
        % (self.name, self.start, self.position, self.total)

  def __repr__(self):
    return "%s(name=%r,start=%s,position=%s,start_time=%s,thoughput_window=%s,total=%s):%r" \
        % (
            type(self).__name__, self.name,
            self.start, self.position, self.start_time,
            self.throughput_window, self.total,
            self._positions)

  def __int__(self):
    ''' int(Progress) returns the current position.
    '''
    return self.position

  def __eq__(self, other):
    ''' A Progress is equal to another object `other`
        if its position equals `int(other)`.
    '''
    return int(self) == int(other)

  def __lt__(self, other):
    ''' A Progress is less then another object `other`
        if its position is less than `int(other)`.
    '''
    return int(self) < int(other)

  def _updated(self):
    datum = self.latest
    for notify in self.notify_update:
      try:
        notify(self, datum)
      except Exception as e:
        exception("%s: notify_update %s: %s", self, notify, e)

  @property
  def latest(self):
    ''' Latest datum.
    '''
    return self._positions[-1]

  @property
  def position(self):
    ''' Latest position.
    '''
    return self.latest.position

  @position.setter
  def position(self, new_position):
    ''' Update the latest position.
    '''
    self.update(new_position)

  @property
  def total(self):
    ''' Return the current total.
    '''
    return self._total

  @total.setter
  def total(self, new_total):
    ''' Update the total.
    '''
    self._total = new_total
    self._updated()

  @property
  def ratio(self):
    ''' The fraction progress completed: (position-start)/(total-start).
        Returns None if total is None or total <= start.

        >>> P = Progress()
        >>> P.ratio
        >>> P.total = 16
        >>> P.ratio
        0.0
        >>> P.update(4)
        >>> P.ratio
        0.25
    '''
    total = self.total
    if total is None:
      return None
    start = self.start
    if total <= start:
      return None
    return float(self.position - start) / (total - start)

  def update(self, new_position, update_time=None):
    ''' Record more progress.

        >>> P = Progress()
        >>> P.position
        0
        >>> P.update(12)
        >>> P.position
        12
    '''
    if update_time is None:
      update_time = time.time()
    ##if new_position < self.position:
    ##  warning("%s.update: .position going backwards from %s to %s",
    ##          self, self.position, new_position)
    datum = CheckPoint(update_time, new_position)
    self._positions.append(datum)
    self._flushed = False
    self._updated()

  def advance(self, delta, update_time=None):
    ''' Record more progress, return the advanced position.

        >>> P = Progress()
        >>> P.position
        0
        >>> P.advance(4)
        >>> P.position
        4
        >>> P.advance(4)
        >>> P.position
        8
    '''
    self.update(self.position + delta, update_time=update_time)

  def __iadd__(self, delta):
    ''' Operator += form of advance().

        >>> P = Progress()
        >>> P.position
        0
        >>> P += 4
        >>> P.position
        4
        >>> P += 4
        >>> P.position
        8
    '''
    self.advance(delta)
    return self

  def __isub__(self, delta):
    ''' Operator -= form of advance().

        >>> P = Progress()
        >>> P.position
        0
        >>> P += 4
        >>> P.position
        4
        >>> P -= 4
        >>> P.position
        0
    '''
    self.advance(-delta)
    return self

  def _flush(self, oldest=None):
    if oldest is None:
      window = self.throughput_window
      if window is None:
        raise ValueError("oldest may not be None when throughput_window is None")
      oldest = time.time() - window
    positions = self._positions
    # scan for first item still in time window
    for ndx, posn in enumerate(positions):
      if posn.time >= oldest:
        if ndx > 0:
          del positions[0:ndx]
        break
    self._flushed = True

  @property
  def elapsed_time(self):
    ''' Time elapsed since start_time.
    '''
    return time.time() - self.start_time

  @property
  def throughput(self):
    ''' Compute current overall throughput per second.

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
    elapsed = self.elapsed_time
    if elapsed == 0:
      return 0
    if elapsed <= 0:
      warning("%s.throughput: negative elapsed time since start_time=%s: %s",
              self, self.start_time, elapsed)
      return 0
    return float(consumed) / elapsed

  def throughput_recent(self, time_window):
    ''' Recent throughput per second within a time window in seconds.

        The time span overlapping the start of the window is included
        on a flat pro rata basis.
    '''
    if time_window <= 0:
      raise ValueError(
          "%s.throughput_recent: invalid time_window <= 0: %s"
          % (self, time_window))
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
    for t, p in self._positions:
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
    rate = float(self.position - low_pos) / (now - low_time)
    if rate < 0:
      warning('rate < 0 (%s)', rate)
    return rate

  @property
  def remaining_time(self):
    ''' The projected time remaining to end
        based on the current throughput and the total.
    '''
    total = self.total
    if total is None:
      return None
    remaining = total - self.position
    if remaining < 0:
      warning("%s.remaining_time: self.position(%s) > self.total(%s)",
              self, self.position, self.total)
      return None
    throughput = self.throughput
    if throughput is None or throughput == 0:
      return None
    return remaining / throughput

  @property
  def eta(self):
    ''' The projected time of completion.
    '''
    remaining = self.remaining_time
    if remaining is None:
      return None
    return time.time() + remaining

  def status(self, label, width):
    ''' A progress string of the form
        *label*`: `*pos*` / `*total*` ==>  ETA '*time*.
    '''
    ratio = self.ratio
    remaining = self.remaining_time
    if remaining:
      remaining = int(remaining)
    if ratio is None:
      if remaining is None:
        return label + ': ETA unknown'
      return label + ': ETA ' + transcribe_time(remaining)
    # "label: ==>  ETA xs"
    left = (
        label
        + ': '
        + transcribe(self.position, BINARY_BYTES_SCALE, max_parts=1)
        + ' / ' + transcribe(self.total, BINARY_BYTES_SCALE, max_parts=1)
        + ' '
    )
    if remaining is None:
      right = 'ETA unknown'
    else:
      right = ' ETA ' + transcribe_time(remaining)
    arrow_width = width - len(left) - len(right)
    if arrow_width < 1:
      # no roow for an arrow
      return label + ':' + right
    if ratio <= 0:
      arrow = ''
    elif ratio < 1.0:
      arrow_len = arrow_width * ratio
      if arrow_len < 1:
        arrow = '>'
      else:
        arrow = '=' * int(arrow_len - 1) + '>'
    else:
      arrow = '=' * arrow_width
    arrow_field = arrow + ' ' * (arrow_width - len(arrow))
    return left + arrow_field + right

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.progress_tests')
