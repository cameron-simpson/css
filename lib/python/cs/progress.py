#!/usr/bin/env python3
#
# Progress counting.
#   - Cameron Simpson <cs@cskk.id.au> 15feb2015
#
# pylint: disable=too-many-lines
#

''' A progress tracker with methods for throughput, ETA and update notification;
    also a compound progress meter composed from other progress meters.
'''

from collections import namedtuple
from contextlib import contextmanager
import functools
import sys
from threading import RLock
import time
from cs.deco import decorator
from cs.logutils import debug, exception
from cs.py.func import funcname
from cs.seq import seq
from cs.units import (
    transcribe_time,
    transcribe,
    BINARY_BYTES_SCALE,
    DECIMAL_SCALE,
    TIME_SCALE,
    UNSCALED_SCALE,
)
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

__version__ = '20201102.1-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires':
    ['cs.deco', 'cs.logutils', 'cs.py.func', 'cs.seq', 'cs.units', 'cs.upd'],
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
    self.notify_update = set()
    self._warned = set()
    self._lock = RLock()

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
             P.ratio
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
      debug(
          "%s.throughput: self.position(%s) < self.start(%s)", self,
          self.position, self.start
      )
    if consumed == 0:
      return 0
    elapsed = self.elapsed_time
    if elapsed == 0:
      return 0
    if elapsed <= 0:
      debug(
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
      if "position>total" not in self._warned:
        self._warned.add("position>total")
        debug(
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

  def format_counter(self, value, scale=None, max_parts=2, sep=','):
    ''' Format `value` accoridng to `scale` and `max_parts`
        using `cs.units.transcribe`.
    '''
    if scale is None:
      scale = self.units_scale
    if scale is None:
      return str(value)
    return transcribe(value, scale, max_parts=max_parts, sep=sep)

  def text_pos_of_total(
      self, fmt=None, fmt_pos=None, fmt_total=None, pos_first=False
  ):
    ''' Return a "total:position" or "position/total" style progress string.

        Parameters:
        * `fmt`: format string interpolating `pos_text` and `total_text`.
          Default: `"{pos_text}/{total_text}"` if `pos_first`,
          otherwise `"{total_text}:{pos_text}"`
        * `fmt_pos`: formatting function for `self.position`,
          default `self.format_counter`
        * `fmt_total`: formatting function for `self.total`,
          default from `fmt_pos`
        * `pos_first`: put the position first if true (default `False`),
          only consulted if `fmt` is `None`
    '''
    if fmt_pos is None:
      fmt_pos = self.format_counter
    if fmt_total is None:
      fmt_total = fmt_pos
    if fmt is None:
      fmt = "{pos_text}/{total_text}" if pos_first else "{total_text}:{pos_text}"
    pos_text = fmt_pos(self.position)
    total_text = fmt_pos(self.total)
    return fmt.format(pos_text=pos_text, total_text=total_text)

  # pylint: disable=too-many-branches,too-many-statements
  def status(self, label, width, window=None):
    ''' A progress string of the form:
        *label*`: `*pos*`/`*total*` ==>  ETA '*time*

        Parameters:
        * `label`: the label for the status line;
          if `None` use `self.name`
        * `width`: the available width for the status line;
          if not an `int` use `width.width`
        * `window`: optional timeframe to define "recent" in seconds,
          default : `5`
    '''
    if label is None:
      label = self.name
    if not isinstance(width, int):
      width = width.width
    if window is None:
      window = 5
    leftv = []
    rightv = []
    throughput = self.throughput_recent(window)
    if throughput is not None:
      if throughput == 0:
        if self.total is not None and self.position >= self.total:
          return 'idle'
        rightv.append('stalled')
      else:
        if throughput >= 10:
          throughput = int(throughput)
        rightv.append(self.format_counter(throughput, max_parts=1) + '/s')
      remaining = self.remaining_time
      if remaining:
        remaining = int(remaining)
      if remaining is None:
        rightv.append('ETA ??')
      else:
        rightv.append('ETA ' + transcribe_time(remaining))
    if self.total is not None and self.total > 0:
      leftv.append(self.text_pos_of_total())
    left = ' '.join(leftv)
    right = ' '.join(rightv)
    if self.total is None:
      arrow_field = ' '
    else:
      # how much room for an arrow? we would like:
      # "label: left arrow right"
      arrow_width = width - len(left) - len(right) - len(label) - 2
      if label:
        arrow_width -= 2  # allow for ': ' separator after label
      if arrow_width < 1:
        # no room for an arrow
        arrow_field = ':'
      else:
        arrow_field = ' ' + self.arrow(arrow_width) + ' '
    status_line = left + arrow_field + right
    if label:
      label_width = width - len(status_line)
      if label_width >= len(label) + 2:
        prefix = label + ': '
      elif label_width == len(label) + 1:
        prefix = label + ':'
      # label_width<=len(label): need to crop the label
      elif label_width <= 0:
        # no room
        prefix = ''
      elif label_width == 1:
        # just indicate the crop
        prefix = '<'
      elif label_width == 2:
        # just indicate the crop
        prefix = '<:'
      else:
        # crop as "<tail-of-label:"
        prefix = '<' + label[-label_width + 2:] + ':'
    else:
      prefix = ''
    status_line = prefix + status_line
    return status_line

  # pylint: disable=blacklisted-name,too-many-arguments
  @contextmanager
  def bar(
      self,
      label=None,
      upd=None,
      proxy=None,
      statusfunc=None,
      width=None,
      window=None,
      report_print=None,
      insert_pos=1,
      deferred=False,
  ):
    ''' A context manager to create and withdraw a progress bar.
        It returns the `UpdProxy` which displays the progress bar.

        Parameters:
        * `label`: a label for the progress bar,
          default from `self.name`.
        * `proxy`: an optional `UpdProxy` to display the progress bar
        * `upd`: an optional `cs.upd.Upd` instance,
          used to produce the progress bar status line if not supplied.
          The default `upd` is `cs.upd.Upd()`
          which uses `sys.stderr` for display.
        * `statusfunc`: an optional function to compute the progress bar text
          accepting `(self,label,width)`.
        * `width`: an optional width expressioning how wide the progress bar
          text may be.
          The default comes from the `proxy.width` property.
        * `window`: optional timeframe to define "recent" in seconds;
          if the default `statusfunc` (`Progress.status`) is used
          this is passed to it
        * `report_print`: optional `print` compatible function
          with which to write a report on completion;
          this may also be a `bool`, which if true will use `Upd.print`
          in order to interoperate with `Upd`.
        * `insert_pos`: where to insert the progress bar, default `1`
        * `deferred`: optional flag; if true do not create the
          progress bar until the first update occurs.

        Example use:

            # display progress reporting during upload_filename()
            # which updates the supplied Progress instance
            # during its operation
            P = Progress(name=label)
            with P.bar(report_print=True):
                upload_filename(src, progress=P)

    '''
    if label is None:
      label = self.name
    if upd is None:
      upd = Upd()
    if statusfunc is None:
      statusfunc = lambda P, label, width: P.status(
          label, width, window=window
      )
    pproxy = [proxy]
    proxy_delete = proxy is None

    def update(P, _):
      proxy = pproxy[0]
      if proxy is None:
        proxy = pproxy[0] = upd.insert(insert_pos, 'LABEL=' + label)
      proxy(statusfunc(P, label, width or proxy.width))

    try:
      if not deferred:
        if proxy is None:
          proxy = pproxy[0] = upd.insert(insert_pos)
        status = statusfunc(self, label, width or proxy.width)
        proxy(status)
      self.notify_update.add(update)
      start_pos = self.position
      yield pproxy[0]
    finally:
      self.notify_update.remove(update)
      if proxy and proxy_delete:
        proxy.delete()
    if report_print:
      if isinstance(report_print, bool):
        report_print = print
      report_print(
          label + ':', self.format_counter(self.position - start_pos), 'in',
          transcribe(
              self.elapsed_time, TIME_SCALE, max_parts=2, skip_zero=True
          )
      )

  # pylint: disable=too-many-arguments,too-many-branches,too-many-locals
  def iterbar(
      self,
      it,
      label=None,
      upd=None,
      proxy=None,
      itemlenfunc=None,
      statusfunc=None,
      incfirst=False,
      width=None,
      window=None,
      update_frequency=1,
      update_min_size=0,
      report_print=None,
  ):
    ''' An iterable progress bar: a generator yielding values
        from the iterable `it` while updating a progress bar.

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
        * `window`: optional timeframe to define "recent" in seconds;
          if the default `statusfunc` (`Progress.status`) is used
          this is passed to it
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
        * `update_frequency`: optional update frequency, default `1`;
          only update the progress bar after this many iterations,
          useful if the iteration rate is quite high
        * `update_min_size`: optional update step size, default `0`;
          only update the progress bar after an advance of this many units,
          useful if the iteration size increment is quite small
        * `report_print`: optional `print` compatible function
          with which to write a report on completion;
          this may also be a `bool`, which if true will use `Upd.print`
          in order to interoperate with `Upd`.

        Example use:

            from cs.units import DECIMAL_SCALE
            rows = [some list of data]
            P = Progress(total=len(rows), units_scale=DECIMAL_SCALE)
            for row in P.iterbar(rows, incfirst=True):
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
            for bs in P.iterbar(readfrom(f, itemlenfunc=len)):
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
      statusfunc = lambda P, label, width: P.status(
          label, width, window=window
      )
    iteration = 0
    last_update_iteration = 0
    last_update_pos = start_pos = self.position

    def update_status(force=False):
      nonlocal self, proxy, statusfunc, label, width
      nonlocal iteration, last_update_iteration, last_update_pos
      if (force or iteration - last_update_iteration >= update_frequency
          or self.position - last_update_pos >= update_min_size):
        last_update_iteration = iteration
        last_update_pos = self.position
        proxy(statusfunc(self, label, width or proxy.width))

    update_status(True)
    for iteration, item in enumerate(it):
      length = itemlenfunc(item) if itemlenfunc else 1
      if incfirst:
        self += length
        update_status()
      yield item
      if not incfirst:
        self += length
        update_status()
    if delete_proxy:
      proxy.delete()
    else:
      update_status(True)
    if report_print:
      if isinstance(report_print, bool):
        report_print = print
      report_print(
          label + ':', self.format_counter(self.position - start_pos), 'in',
          transcribe(
              self.elapsed_time, TIME_SCALE, max_parts=2, skip_zero=True
          )
      )

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

  # pylint: disable=too-many-arguments
  def __init__(
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

  def __repr__(self):
    return "%s(name=%r,start=%s,position=%s,start_time=%s,throughput_window=%s,total=%s):%r" \
        % (
            type(self).__name__, self.name,
            self.start, self.position, self.start_time,
            self.throughput_window, self.total,
            self._positions)

  def _updated(self):
    datum = self.latest
    for notify in list(self.notify_update):
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
    if new_position < self.latest.position:
      debug(
          "%s.update: new position %s < latest position %s", self,
          new_position, self.latest.position
      )
    if update_time is None:
      update_time = time.time()
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
    # scan for first item still in time window,
    # never discard the last 2 positions
    for ndx in range(0, len(positions) - 1):
      posn = positions[ndx]
      if posn.time >= oldest:
        # this is the first element to keep, discard preceeding (if any)
        # note we can't just start at ndx=1 because ndx=0 might be in range
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
    positions = self._positions
    if len(positions) == 1 and positions[0].time == self.start_time:
      # no throughput if we only have the starting position
      return None
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
      debug('low_time=%s >= now=%s', low_time, now)
      return 0
    rate = float(self.position - low_pos) / (now - low_time)
    if rate < 0:
      debug('rate < 0 (%s)', rate)
    return rate

class OverProgress(BaseProgress):
  ''' A `Progress`-like class computed from a set of subsidiary `Progress`es.

      AN OverProgress instance has an attribute ``notify_update`` which
      is a set of callables.
      Whenever the position of a subsidiary `Progress` is updated,
      each of these will be called with the `Progress` instance and `None`.

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
    # we use these to to accrue removed subprogresses (optional)
    self._base_total = 0
    self._base_position = 0
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

  def _updated(self):
    with self._lock:
      notifiers = list(self.notify_update)
    for notify in notifiers:
      try:
        notify(self, None)
      except Exception as e:  # pylint: disable=broad-except
        exception("%s: notify_update %s: %s", self, notify, e)

  # pylint: disable=unused-argument
  def _child_updated(self, child, _):
    ''' Notify watchers if a child updates.
    '''
    self._updated()

  def add(self, subprogress):
    ''' Add a subsidairy `Progress` to the contributing set.
    '''
    with self._lock:
      subprogress.notify_update.add(self._child_updated)
      self.subprogresses.add(subprogress)
      self._updated()

  def remove(self, subprogress, accrue=False):
    ''' Remove a subsidairy `Progress` from the contributing set.
    '''
    with self._lock:
      subprogress.notify_update.remove(self._child_updated)
      self.subprogresses.remove(subprogress)
      if accrue:
        self._base_position += subprogress.position - subprogress.start
        self._base_total += subprogress.total
      self._updated()

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
    with self._lock:
      children = list(self.subprogresses)
    maximum = None
    for value in filter(fnP, children):
      if value is not None:
        maximum = value if maximum is None else max(maximum, value)
    return maximum

  def _oversum(self, fnP):
    ''' Sum non-`None` values computed from the subsidiary `Progress`es.
        Return the sum, or `None` if there are no non-`None` values.
    '''
    with self._lock:
      children = list(self.subprogresses)
    summed = None
    for value in map(fnP, children):
      if value is not None:
        summed = value if summed is None else summed + value
    return summed

  @property
  def position(self):
    ''' The `position` is the sum off the subsidiary position offsets
        from their respective starts.
    '''
    pos = self._oversum(lambda P: P.position - P.start)
    if pos is None:
      pos = 0
    return self._base_position + pos

  @property
  def total(self):
    ''' The `total` is the sum of the subsidiary totals.
    '''
    total = self._oversum(lambda P: P.total)
    if total is None:
      total = 0
    return self._base_total + total

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

def progressbar(
    it,
    label=None,
    position=None,
    total=None,
    units_scale=UNSCALED_SCALE,
    **kw
):
  ''' Convenience function to construct and run a `Progress.iterbar`
      wrapping the iterable `it`,
      issuing and withdrawning a progress bar during the iteration.

      Parameters:
      * `it`: the iterable to consume
      * `label`: optional label, doubles as the `Progress.name`
      * `position`: optional starting position
      * `total`: optional value for `Progress.total`,
        default from `len(it)` if supported.
      * `units_scale`: optional units scale for `Progress`,
        default `UNSCALED_SCALE`

      If `total` is `None` and `it` supports `len()`
      then the `Progress.total` is set from it.

      All arguments are passed through to `Progress.iterbar`.

      Example use:

          for row in progressbar(rows):
              ... do something with row ...
  '''
  if total is None:
    try:
      total = len(it)
    except TypeError:
      total = None
  yield from Progress(
      name=label, position=position, total=total, units_scale=units_scale
  ).iterbar(
      it, label=label, **kw
  )
  pass

@decorator
def auto_progressbar(func, label=None, report_print=False):
  ''' Decorator for function which accept an optional `progress` parameter.
      If `progress` is `None` and the default `Upd` is not disabled,
      run the function with a progress bar.
  '''

  def wrapper(
      *a,
      progress=None,
      progress_name=None,
      progress_total=None,
      progress_report_print=None,
      **kw
  ):
    if progress_name is None:
      progress_name = label or funcname(func)
    if progress_report_print is None:
      progress_report_print = report_print
    if progress is None:
      upd = Upd()
      if not upd.disabled:
        progress = Progress(name=progress_name, total=progress_total)
        with progress.bar(upd=upd, report_print=progress_report_print):
          return func(*a, progress=progress, **kw)
    return func(*a, progress=progress, **kw)

  return wrapper

# pylint: disable=unused-argument
def selftest(argv):
  ''' Exercise some of the functionality.
  '''
  lines = open(__file__).readlines()
  lines += lines
  for _ in progressbar(lines, "lines"):
    time.sleep(0.005)
  for _ in progressbar(lines, "lines step 100", update_frequency=100,
                       report_print=True):
    time.sleep(0.005)
  P = Progress(name=__file__, total=len(lines), units_scale=DECIMAL_SCALE)
  for _ in P.iterbar(open(__file__)):
    time.sleep(0.005)
  from cs.debug import selftest as runtests  # pylint: disable=import-outside-toplevel
  runtests('cs.progress_tests')

if __name__ == '__main__':
  sys.exit(selftest(sys.argv))
