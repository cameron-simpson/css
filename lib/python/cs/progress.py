#!/usr/bin/env python3
#
# Progress counting.
#   - Cameron Simpson <cs@cskk.id.au> 15feb2015
#

''' A progress tracker with methods for throughput, ETA and update notification;
    also a compound progress meter composed from other progress meters.
'''

from collections import namedtuple
import functools
import time
from cs.logutils import warning, exception
from cs.seq import seq
from cs.units import transcribe_time, transcribe, BINARY_BYTES_SCALE
from cs.upd import Upd

__version__ = '20200626-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.seq', 'cs.units', 'cs.upd'],
}

# default to 5s of position buffer for computing recent thoroughput
DEFAULT_THROUGHPUT_WINDOW = 5

@functools.total_ordering
class BaseProgress(object):
  ''' The base class for `Progress` and `OverProcess`
      with various common methods.

      Note that durations are in seconds
      and that absolute time is in seconds since the UNIX epoch
      (the basis of `time.time()`).
  '''

  def __init__(self, name=None, start_time=None, units_scale=None):
    ''' Initialise a progress instance.

        Parameters:
        * `name`: optional name
        * `start_time`: optional UNIX epoch start time, default from `time.time()`
        * `units_scale`: a scale for use with `cs.units.transcribe`,
          default `BINARY_BYTES_SCALE`
    '''
    now = time.time()
    if name is None:
      name = '-'.join((type(self).__name__, str(seq())))
    if start_time is None:
      start_time = now
    elif start_time > now:
      raise ValueError("start_time(%s) > now(%s)" % (start_time, now))
    if units_scale is None:
      units_scale = BINARY_BYTES_SCALE
    self.name = name
    self.start_time = start_time
    self.units_scale = units_scale

  def __str__(self):
    return "%s[start=%s:pos=%s:total=%s]" \
        % (self.name, self.start, self.position, self.total)

  __repr__ = __str__

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

  def __hash__(self):
    return id(self)

  @property
  def elapsed_time(self):
    ''' Time elapsed since `start_time`.
    '''
    return time.time() - self.start_time

  @property
  def ratio(self):
    ''' The fraction of progress completed: `(position-start)/(total-start)`.
        Returns `None` if `total` is `None` or `total<=start`.

        Example:

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

  def throughput_recent(self, time_window):
    ''' The recent throughput. Implemented by subclasses.
    '''
    raise NotImplementedError(
        "%s.throughtput_recent(time_window=%s): subclass must implement" %
        (type(self).__name__, time_window)
    )

  def throughput_overall(self):
    ''' The overall throughput from `start` to `position`
        during `elapsed_time`.
    '''
    consumed = self.position - self.start
    if consumed < 0:
      warning(
          "%s.throughput: self.position(%s) < self.start(%s)", self,
          self.position, self.start
      )
    if consumed == 0:
      return 0
    elapsed = self.elapsed_time
    if elapsed == 0:
      return 0
    if elapsed <= 0:
      warning(
          "%s.throughput: negative elapsed time since start_time=%s: %s", self,
          self.start_time, elapsed
      )
      return 0
    return float(consumed) / elapsed

  @property
  def throughput(self):
    ''' The overall throughput: `self.throughput_overall()`.

        By comparison,
        the `Progress.throughput` property is `self.throughput_recent`
        if the `throughput_window` is not `None`,
        otherwise it falls back to `throughput_overall`.
    '''
    return self.throughput_overall()

  @property
  def remaining_time(self):
    ''' The projected time remaining to end
        based on the `throughput` and `total`.

        If `total` is `None`, this is `None`.
    '''
    total = self.total
    if total is None:
      return None
    remaining = total - self.position
    if remaining < 0:
      warning(
          "%s.remaining_time: self.position(%s) > self.total(%s)", self,
          self.position, self.total
      )
      return None
    throughput = self.throughput
    if throughput is None or throughput == 0:
      return None
    return remaining / throughput

  @property
  def eta(self):
    ''' The projected time of completion: now + `remaining_time`.

        If `reamining_time` is `None`, this is also `None`.
    '''
    remaining = self.remaining_time
    if remaining is None:
      return None
    return time.time() + remaining

  def count_of_total_bytes_text(self):
    ''' Return "count units / total units" using binary units.
    '''
    return (
        transcribe(self.position, BINARY_BYTES_SCALE, max_parts=1) + '/' +
        transcribe(self.total, BINARY_BYTES_SCALE, max_parts=1)
    )

  def arrow(self, width, no_padding=False):
    ''' Construct a progress arrow representing completion
        to fit in the specified `width`.
    '''
    if width < 1:
      return ''
    ratio = self.ratio
    if ratio is None or ratio <= 0:
      arrow = ''
    elif ratio < 1.0:
      arrow_len = width * ratio
      if arrow_len < 1:
        arrow = '>'
      else:
        arrow = '=' * int(arrow_len - 1) + '>'
    else:
      arrow = '=' * width
    if not no_padding:
      arrow += ' ' * (width - len(arrow))
    return arrow

  def format_counter(self, value, scale=None, max_parts=2, sep=' '):
    ''' Format `value` accoridng to `scale` and `max_parts`
        using `cs.units.transcribe`.
    '''
    if scale is None:
      scale = self.units_scale
    if scale is None:
      return str(value)
    return transcribe(value, scale, max_parts=max_parts, sep=sep)

  def text_pos_of_total(
      self, fmt="{pos_text}/{total_text}", fmt_pos=None, fmt_total=None
  ):
    ''' Return a "position/total" style progress string.

        Parameters:
        * `fmt`: format string interpolating `pos_text` and `total_text`.
          Default: `"{pos_text}/{total_text}"`
        * `fmt_pos`: formatting function for `self.position`,
          default `self.format_counter`
        * `fmt_total`: formatting function for `self.total`,
          default from `fmt_pos`
    '''
    if fmt_pos is None:
      fmt_pos = self.format_counter
    if fmt_total is None:
      fmt_total = fmt_pos
    pos_text = fmt_pos(self.position)
    total_text = fmt_pos(self.total)
    return fmt.format(pos_text=pos_text, total_text=total_text)

  def status(self, label, width):
    ''' A progress string of the form:
        *label*`: `*pos*`/`*total*` ==>  ETA '*time*
    '''
    left = label
    remaining = self.remaining_time
    if remaining:
      remaining = int(remaining)
    throughput = self.throughput_recent(5)
    if throughput is None:
      throughput_s = ''
    elif throughput == 0:
      throughput_s = 'stalled'
    else:
      throughput_s = self.format_counter(throughput, max_parts=1) + '/s'
    if throughput_s:
      left += ': ' + throughput_s
    if self.total is not None:
      left += ' ' + self.text_pos_of_total()
    if remaining is None:
      right = 'ETA unknown'
    else:
      right = 'ETA ' + transcribe_time(remaining)
    if self.total is None:
      arrow_field = ' '
    else:
      arrow_width = width - len(left) - len(right)
      if arrow_width < 1:
        # no room for an arrow
        arrow_field = ':'
      else:
        arrow_field = self.arrow(arrow_width)
    return left + arrow_field + right

  def bar(  # pylint: disable=blacklisted-name,too-many-arguments
      self,
      it,
      label=None,
      upd=None,
      proxy=None,
      itemlenfunc=None,
      statusfunc=None,
      incfirst=False,
      width=None,
  ):
    ''' Generator yielding values from the iterable `it`
        while updating a progress bar.

        Parameters:
        * `it`: the iterable to consume and yield.
        * `itemlenfunc`: an optional function returning the "size" of each item
          from `it`, used to advance `self.position`.
          The default is to assume a size of `1`.
          A convenient alternative choice may be the builtin function `len`.
        * `incfirst`: whether to advance `self.position` before we
          `yield` an item from `it` or afterwards.
          This reflects whether it is considered that progress is
          made as items are obtained or only after items are processed
          by whatever is consuming this generator.
          The default is `False`,
        * `label`: a label for the progress bar,
          default from `self.name`.
        * `width`: an optional width expressioning how wide the progress bar
          text may be.
          The default comes from the `proxy.width` property.
        * `statusfunc`: an optional function to compute the progress bar text
          accepting `(self,label,width)`.
        * `proxy`: an optional proxy for displaying the progress bar,
          a callable accepting the result of `statusfunc`.
          The default is a `cs.upd.UpdProxy` created from `upd`,
          which inserts a progress bar above the main status line.
        * `upd`: an optional `cs.upd.Upd` instance,
          used only to produce the default `proxy` if that is not supplied.
          The default `upd` is `cs.upd.Upd()`
          which uses `sys.stderr` for display.

        Example use:

            from cs.units import DECIMAL_SCALE
            rows = [some list of data]
            P = Progress(total=len(rows), units_scale=DECIMAL_SCALE)
            for row in P.bar(rows, incfirst=True):
                ... do something with each row ...

            f = open(data_filename, 'rb')
            datalen = os.stat(f).st_size
            def readfrom(f):
                while True:
                    bs = f.read(65536)
                    if not bs:
                        break
                    yield bs
            P = Progress(total=datalen)
            for bs in P.bar(readfrom(f, itemlenfunc=len)):
                ... process the file data in bs ...
    '''
    if label is None:
      label = self.name
    delete_proxy = False
    if proxy is None:
      if upd is None:
        upd = Upd()
      proxy = upd.insert(1)
      delete_proxy = True
    if statusfunc is None:
      statusfunc = lambda P, label, width: P.status(label, width)
    proxy(statusfunc(self, label, width or proxy.width))
    for i in it:
      length = itemlenfunc(i) if itemlenfunc else 1
      if incfirst:
        self += length
      proxy(statusfunc(self, label, width or proxy.width))
      yield i
      if not incfirst:
        self += length
      proxy(statusfunc(self, label, width or proxy.width))
    if delete_proxy:
      proxy.delete()

CheckPoint = namedtuple('CheckPoint', 'time position')

class Progress(BaseProgress):
  ''' A progress counter to track task completion with various utility methods.

      Example:

          >>> P = Progress(name="example")
          >>> P                         #doctest: +ELLIPSIS
          Progress(name='example',start=0,position=0,start_time=...,throughput_window=None,total=None):[CheckPoint(time=..., position=0)]
          >>> P.advance(5)
          >>> P                         #doctest: +ELLIPSIS
          Progress(name='example',start=0,position=5,start_time=...,throughput_window=None,total=None):[CheckPoint(time=..., position=0), CheckPoint(time=..., position=5)]
          >>> P.total = 100
          >>> P                         #doctest: +ELLIPSIS
          Progress(name='example',start=0,position=5,start_time=...,throughput_window=None,total=100):[CheckPoint(time=..., position=0), CheckPoint(time=..., position=5)]

      A Progress instance has an attribute ``notify_update`` which
      is a set of callables. Whenever the position is updated, each
      of these will be called with the `Progress` instance and the
      latest `CheckPoint`.

      `Progress` objects also make a small pretense of being an integer.
      The expression `int(progress)` returns the current position,
      and `+=` and `-=` adjust the position.

      This is convenient for coding, but importantly it is also
      useful for discretionary use of a Progress with some other
      object.
      If you want to make a lightweight `Progress` capable class
      you can set a position attribute to an `int`
      and manipulate it carefully using `+=` and `-=` entirely.
      If you decide to incur the cost of maintaining a `Progress` object
      you can slot it in:

          # initial setup with just an int
          my_thing.amount = 0

          # later, or on some option, use a Progress instance
          my_thing.amount = Progress(my_thing.amount)
  '''

  def __init__( # pylint: disable=too-many-arguments
      self,
      position=None,
      name=None,
      start=None,
      start_time=None,
      throughput_window=None,
      total=None,
      units_scale=None,
  ):
    ''' Initialise the Progesss object.

        Parameters:
        * `position`: initial position, default `0`.
        * `name`: optional name for this instance.
        * `start`: starting position of progress range,
          default from `position`.
        * `start_time`: start time of the process, default now.
        * `throughput_window`: length of throughput time window in seconds,
          default None.
        * `total`: expected completion value, default None.
    '''
    BaseProgress.__init__(
        self, name=name, start_time=start_time, units_scale=units_scale
    )
    if position is None:
      position = 0
    if start is None:
      start = position
    if throughput_window is None:
      throughput_window = DEFAULT_THROUGHPUT_WINDOW
    elif throughput_window <= 0:
      raise ValueError("throughput_window <= 0: %s" % (throughput_window,))
    self.start = start
    self._total = total
    self.throughput_window = throughput_window
    # history of positions, used to compute throughput
    positions = [CheckPoint(self.start_time, start)]
    if position != start:
      positions.append(CheckPoint(time.time(), position))
    self._positions = positions
    self._flushed = True
    self.notify_update = set()

  def __repr__(self):
    return "%s(name=%r,start=%s,position=%s,start_time=%s,throughput_window=%s,total=%s):%r" \
        % (
            type(self).__name__, self.name,
            self.start, self.position, self.start_time,
            self.throughput_window, self.total,
            self._positions)

  def _updated(self):
    datum = self.latest
    for notify in self.notify_update:
      try:
        notify(self, datum)
      except Exception as e:  # pylint: disable=broad-except
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
        raise ValueError(
            "oldest may not be None when throughput_window is None"
        )
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
  def throughput(self):
    ''' Current throughput per second.

        If `self.throughput_window` is not `None`,
        calls `self.throughput_recent(throughput_window)`.
        Otherwise call `self.throughput_overall()`.
    '''
    throughput_window = self.throughput_window
    if throughput_window is None:
      return self.throughput_overall()
    return self.throughput_recent(throughput_window)

  def throughput_recent(self, time_window):
    ''' Recent throughput per second within a time window in seconds.

        The time span overlapping the start of the window is included
        on a flat pro rata basis.
    '''
    if time_window <= 0:
      raise ValueError(
          "%s.throughput_recent: invalid time_window <= 0: %s" %
          (self, time_window)
      )
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
      # in the future? warn and return 0
      warning('low_time=%s >= now=%s', low_time, now)
      return 0
    rate = float(self.position - low_pos) / (now - low_time)
    if rate < 0:
      warning('rate < 0 (%s)', rate)
    return rate

class OverProgress(BaseProgress):
  ''' A `Progress`-like class computed from a set of subsidiary `Progress`es.

      Example:

          >>> P = OverProgress(name="over")
          >>> P1 = Progress(name="progress1", position=12)
          >>> P1.total = 100
          >>> P1.advance(7)
          >>> P2 = Progress(name="progress2", position=20)
          >>> P2.total = 50
          >>> P2.advance(9)
          >>> P.add(P1)
          >>> P.add(P2)
          >>> P1.total
          100
          >>> P2.total
          50
          >>> P.total
          150
          >>> P1.start
          12
          >>> P2.start
          20
          >>> P.start
          0
          >>> P1.position
          19
          >>> P2.position
          29
          >>> P.position
          16

  '''

  def __init__(
      self, subprogresses=None, name=None, start_time=None, units_scale=None
  ):
    BaseProgress.__init__(
        self, name=name, start_time=start_time, units_scale=units_scale
    )
    self.subprogresses = set()
    if subprogresses:
      for subP in subprogresses:
        self.add(subP)

  def __repr__(self):
    return "%s(name=%r,start=%s,position=%s,start_time=%s,total=%s)" \
        % (
            type(self).__name__, self.name,
            self.start, self.position, self.start_time,
            self.total)

  def add(self, subprogress):
    ''' Add a subsidairy `Progress` to the contributing set.
    '''
    self.subprogresses.add(subprogress)

  def remove(self, subprogress):
    ''' Remove a subsidairy `Progress` from the contributing set.
    '''
    self.subprogresses.remove(subprogress)

  @property
  def start(self):
    ''' We always return a starting value of 0.
    '''
    return 0

  def _overmax(self, fnP):
    ''' Return the maximum of the non-`None` values
        computed from the subsidiary `Progress`es.
        Return the maximum, or `None` if there are no non-`None` values.
    '''
    maximum = None
    for value in filter(fnP, self.subprogresses):
      if value is not None:
        maximum = value if maximum is None else max(maximum, value)
    return maximum

  def _oversum(self, fnP):
    ''' Sum non-`None` values computed from the subsidiary `Progress`es.
        Return the sum, or `None` if there are no non-`None` values.
    '''
    summed = None
    for value in map(fnP, self.subprogresses):
      if value is not None:
        summed = value if summed is None else summed + value
    return summed

  @property
  def position(self):
    ''' The `position` is the sum off the subsidiary position offsets
        from their respective starts.
    '''
    return self._oversum(lambda P: P.position - P.start)

  @property
  def total(self):
    ''' The `total` is the sum of the subsidiary totals.
    '''
    return self._oversum(lambda P: P.total)

  @property
  def throughput(self):
    ''' The `throughput` is the sum of the subsidiary throughputs.
    '''
    return self._oversum(lambda P: P.throughput)

  def throughput_recent(self, time_window):
    ''' The `throughput_recent` is the sum of the subsidiary throughput_recentss.
    '''
    return self._oversum(lambda P: P.throughput_recent(time_window))

  @property
  def eta(self):
    ''' The `eta` is the maximum of the subsidiary etas.
    '''
    return self._overmax(lambda P: P.eta)

def progressbar(it, label=None, total=None, **kw):
  ''' Convenience function to construct and run a `Progress.bar`.

      Parameters:
      * `it`: the iterable to consume
      * `label`: optional label, doubles as the `Progress.name`
      * `total`: optional value for `Progress.total`,
        default from `len(it)` if supported.

      If `total` is `None` and `it` supports `len()`
      then the `Progress.total` is set from it.

      All arguments are passed through to `Progress.bar`.

      Example use:

          for row in progressbar(rows):
              ... do something with row ...
  '''
  if total is None:
    try:
      total = len(it)
    except TypeError:
      total = None
  P = Progress(name=label, total=total)
  yield from P.bar(it, label=label, **kw)

if __name__ == '__main__':
  from cs.units import DECIMAL_SCALE
  lines = open(__file__).readlines()
  for line in progressbar(lines, "lines"):
    time.sleep(0.005)
  P = Progress(name=__file__, total=len(lines), units_scale=DECIMAL_SCALE)
  for line in P.bar(open(__file__)):
    time.sleep(0.005)
  from cs.debug import selftest
  selftest('cs.progress_tests')
