#!/usr/bin/env python3
#
# Progress counting.
#   - Cameron Simpson <cs@cskk.id.au> 15feb2015
#
# pylint: disable=too-many-lines
#

''' A progress tracker with methods for throughput, ETA and update notification;
    also a compound progress meter composed from other progress meters.

    This contains the follow main items:
    * `progressbar`: a wrapper for an iterable presenting a progress
      bar in the terminal
    * `Progress`: a progress tracking class
    * `OverProgress`: a progress tracking class which tracks the
      aggregate of multiple `Progress` instances

    Example:

        for item in progressbar(items, "task name"):
            ....
'''

from collections import namedtuple
from contextlib import contextmanager
import functools
import sys
from threading import RLock, Thread
import time
from typing import Callable, Optional

from icontract import ensure
from typeguard import typechecked

from cs.deco import decorator, fmtdoc, uses_verbose
from cs.logutils import debug, exception
from cs.py.func import funcname
from cs.queues import IterableQueue, QueueIterator
from cs.resources import RunState, uses_runstate
from cs.seq import seq
from cs.units import (
    human_time,
    transcribe as transcribe_units,
    BINARY_BYTES_SCALE,
    DECIMAL_SCALE,
    TIME_SCALE,
    UNSCALED_SCALE,
)
from cs.upd import Upd, uses_upd, print  # pylint: disable=redefined-builtin

__version__ = '20250530-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.logutils',
        'cs.py.func',
        'cs.queues',
        'cs.resources',
        'cs.seq',
        'cs.units',
        'cs.upd',
        'icontract',
        'typeguard',
    ],
}

# default to 5s of position buffer for computing recent thoroughput
DEFAULT_THROUGHPUT_WINDOW = 5

# default update period
DEFAULT_UPDATE_PERIOD = 0.3

@functools.total_ordering
class BaseProgress:
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
    ''' `int(Progress)` returns the current position.
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

        If `remaining_time` is `None`, this is also `None`.
    '''
    remaining = self.remaining_time
    if remaining is None:
      return None
    return time.time() + remaining

  @ensure(lambda width, result: len(result) <= width)
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

  def format_counter(self, value, scale=None, max_parts=2, sep=',', **kw):
    ''' Format `value` accoridng to `scale` and `max_parts`
        using `cs.units.transcribe`.
    '''
    if scale is None:
      scale = self.units_scale
    if scale is None:
      return str(value)
    return transcribe_units(value, scale, max_parts=max_parts, sep=sep, **kw)

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
  def status(self, label, width, recent_window=None, stalled=None):
    ''' A progress string of the form:
        *label*`: `*pos*`/`*total*` ==>  ETA '*time*

        Parameters:
        * `label`: the label for the status line;
          if `None` use `self.name`
        * `width`: the available width for the status line;
          if not an `int` use `width.width`
        * `recent_window`: optional timeframe to define "recent" in seconds,
          default : `5`
        * `stalled`: the label to indicate no throughput, default `'stalled'`;
          for a worker this might often b better as `'idle'`
    '''
    if label is None:
      label = self.name
    if stalled is None:
      stalled = 'stalled'
    if not isinstance(width, int):
      width = width.width
    if recent_window is None:
      recent_window = 5
    leftv = []
    rightv = []
    throughput = self.throughput_recent(recent_window)
    if throughput is not None:
      if throughput == 0:
        if self.total is not None and self.position >= self.total:
          return 'idle'
        rightv.append(stalled)
      else:
        if throughput >= 10:
          throughput = int(throughput)
        rightv.append(self.format_counter(throughput, max_parts=1) + '/s')
      remaining = self.remaining_time
      if remaining:
        remaining = int(remaining)
      if remaining is not None:
        rightv.append(f'ETA {human_time(remaining)}')
    if self.total is not None and self.total > 0:
      leftv.append(self.text_pos_of_total())
    else:
      leftv.append(self.format_counter(self.position))
    # the n/m display
    left = ' '.join(leftv)
    # the throughput display
    right = ' '.join(rightv)
    if self.total is None:
      arrow_field = ' '
    else:
      # how much room for an arrow? we would like:
      # "label: left arrow right"
      arrow_width = width - len(left) - len(right) - 2
      if label:  # allow for ': ' separator after label
        arrow_width -= len(label) + 2
      if arrow_width < 3:  # no room for an arrow
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
      elif label_width <= 0:  # label_width<=len(label): need to crop the label
        # no room
        prefix = ''
      elif label_width == 1:  # just indicate the crop
        prefix = '<'
      elif label_width == 2:  # just indicate the crop
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
  @uses_verbose
  @uses_upd
  @fmtdoc
  def bar(
      self,
      label=None,
      *,
      statusfunc=None,
      width=None,
      recent_window=None,
      stalled=None,
      report_print=None,
      insert_pos=1,
      poll: Optional[Callable[["BaseProgress"], None]] = None,
      update_period=DEFAULT_UPDATE_PERIOD,
      verbose: bool,
      upd: Upd,
  ):
    ''' A context manager to create and withdraw a progress bar.
        It returns the `UpdProxy` which displays the progress bar.

        Parameters:
        * `label`: an optional label for the progress bar,
          default from `self.name`.
        * `insert_pos`: where to insert the progress bar within the `cs.Upd`,
          default `1`
        * `poll`: an optional callable which will receive `self`,
          which can be used to update the progress state before
          updating the progress bar display; useful if the progress
          should be updates from some other programme state
        * `recent_window`: optional timeframe to define "recent" in seconds;
          if the default `statusfunc` (`Progress.status`) is used
          this is passed to it
        * `report_print`: optional `print` compatible function
          with which to write a report on completion;
          this may also be a `bool`, which if true will use `Upd.print`
          in order to interoperate with `Upd`.
        * `stalled`: optional string to replace the word `'stalled'`
          in the status line; for a worker this might be better as `'idle'`
        * `statusfunc`: an optional function to compute the progress bar text
          accepting `(self,label,width)`; default `Progress.status`
        * `update_period`: an optional frequency with which to update the display,
          default from `DEFAULT_UPDATE_PERIOD` ({DEFAULT_UPDATE_PERIOD}s);
          if set to `0` then the display is updated whenever `self` is updated
        * `width`: an optional width expressing how wide the progress bar
          text may be.
          The default comes from the `proxy.width` property.

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
    if report_print is None:
      report_print = verbose
    if statusfunc is None:

      def statusfunc(P, label, width):
        ''' Use the `Progress.status` method by default.
        '''
        return P.status(
            label,
            width,
            recent_window=recent_window,
            stalled=stalled,
        )

    def text_auto():
      ''' The current state of the `Progress`, to fit `width` and `proxy.width`.
      '''
      if poll is not None:
        poll(self)
      return statusfunc(self, "", min((width or proxy.width), proxy.width))

    # pylint: disable=unused-argument
    def update(P: Progress, _):
      ''' Update the status bar `UpdProxy` with the current state.
      '''
      proxy.text = None

    cancel_ticker = False

    def _ticker():
      ''' Worker to update the progress bar every `update_period` seconds.
      '''
      time.sleep(update_period)
      while not cancel_ticker:
        update(self, None)
        time.sleep(update_period)

    try:
      start_pos = self.position
      with upd.insert(
          insert_pos,
          prefix=label + ' ',
          text_auto=text_auto,
      ) as proxy:
        update(self, None)
        if update_period == 0:
          # update every time the Progress is updated
          self.notify_update.add(update)
        elif update_period > 0:
          # update every update_period seconds
          Thread(target=_ticker, name=f'{label}-ticker', daemon=True).start()
        yield proxy
    finally:
      cancel_ticker = True
      if update_period == 0:
        self.notify_update.remove(update)
      if report_print:
        if isinstance(report_print, bool):
          report_print = print
        report_print(
            label + ':', self.format_counter(self.position - start_pos), 'in',
            transcribe_units(
                self.elapsed_time, TIME_SCALE, max_parts=2, skip_zero=True
            )
        )

  # pylint: disable=too-many-arguments,too-many-branches,too-many-locals
  @uses_runstate
  def iterbar(
      self,
      it,
      label=None,
      *,
      itemlenfunc=None,
      incfirst=False,
      update_period=DEFAULT_UPDATE_PERIOD,
      cancelled=None,
      runstate: RunState,
      **bar_kw,
  ):
    ''' An iterable progress bar: a generator yielding values
        from the iterable `it` while updating a progress bar.

        Parameters:
        * `it`: the iterable to consume and yield.
        * `label`: a label for the progress bar,
          default from `self.name`.
        * `itemlenfunc`: an optional function returning the "size" of each item
          from `it`, used to advance `self.position`.
          The default is to assume a size of `1`.
          A convenient alternative choice may be the builtin function `len`.
        * `incfirst`: whether to advance `self.position` before we
          `yield` an item from `it` or afterwards.
          This reflects whether it is considered that progress is
          made as items are obtained or only after items are processed
          by whatever is consuming this generator.
          The default is `False`, advancing after processing.
        * `update_period`: default `DEFAULT_UPDATE_PERIOD`; if `0`
          then update on every iteration, otherwise every `update_period`
          seconds
        * `cancelled`: an optional callable to test for iteration cancellation
        Other parameters are passed to `Progress.bar`.

        Example uses:

            from cs.units import DECIMAL_SCALE
            rows = [some list of data]
            P = Progress(total=len(rows), units_scale=DECIMAL_SCALE)
            for row in P.iterbar(rows, incfirst=True):
                ... do something with each row ...

            with open(data_filename, 'rb') as f:
                datalen = os.stat(f).st_size
                def readfrom(f):
                    while True:
                        bs = f.read(65536)
                        if not bs:
                            break
                        yield bs
                P = Progress(total=datalen)
                for bs in P.iterbar(readfrom(f), itemlenfunc=len):
                    ... process the file data in bs ...
    '''
    if cancelled is None:
      cancelled = lambda: runstate.cancelled
    with self.bar(label, update_period=update_period, **bar_kw) as proxy:
      for item in it:
        if cancelled and cancelled():
          break
        length = itemlenfunc(item) if itemlenfunc else 1
        if incfirst:
          self += length
          if update_period == 0:
            proxy.text = None
          yield item
        else:
          yield item
          self += length
          if update_period == 0:
            proxy.text = None

  def qbar(self, label=None, **iterbar_kw) -> QueueIterator:
    ''' Set up a progress bar, return a closeable `Queue`-like object
        for receiving items. This is a shim for `Progress.iterbar`
        which dispatches a worker to iterate items put onto a queue.

        Example:

            Q = Progress.qbar("label")
            try:
                ... do work, calling Q.put(item) ...
            finally:
                Q.close()
    '''
    Q = IterableQueue(name=label)

    def qbar_worker():
      ''' Consume the items from `Q`, updating the progress bar.
      '''
      for _ in self.iterbar(Q, label=label, **iterbar_kw):
        pass

    T = Thread(target=qbar_worker, name=f'{self}.qbar.qbar_worker:{label}')
    T.start()
    return Q

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
  @typechecked
  def __init__(
      self,
      name: Optional[str] = None,
      *,
      position: Optional[float] = None,
      start: Optional[float] = None,
      start_time: Optional[float] = None,
      throughput_window: Optional[int] = None,
      total: Optional[float] = None,
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
    return "%s(name=%r,start=%s,position=%s,start_time=%s,throughput_window=%s,total=%s)" % (
        type(self).__name__,
        self.name,
        self.start,
        self.position,
        self.start_time,
        self.throughput_window,
        self.total,
    )

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

  def advance_total(self, delta):
    ''' Function form of addition to the total.
    '''
    self.total += delta

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
    for ndx in range(len(positions) - 1):
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
    time0 = max(time0, self.start_time)
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

@uses_upd
def progressbar(
    it,
    label=None,
    *,
    position=None,
    total=None,
    units_scale=UNSCALED_SCALE,
    upd: Upd,
    report_print=None,
    **iterbar_kw
):
  ''' Convenience function to construct and run a `Progress.iterbar`
      wrapping the iterable `it`,
      issuing and withdrawing a progress bar during the iteration.
      If there is no current `Upd` instance or it is disabled, this
      returns `it` directly.

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
  if not report_print and (upd is None or upd.disabled):
    return it
  if total is None:
    try:
      total = len(it)
    except TypeError:
      total = None
  return Progress(
      name=label, position=position, total=total, units_scale=units_scale
  ).iterbar(
      it, report_print=report_print, **iterbar_kw
  )

@decorator
def auto_progressbar(func, label=None, report_print=False):
  ''' Decorator for a function accepting an optional `progress`
      keyword parameter.
      If `progress` is not `None` and the default `Upd` is not disabled,
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
  with open(__file__, encoding='utf8') as f:
    lines = f.readlines()
  lines += lines
  if True:  # pylint: disable=using-constant-test
    for _ in progressbar(lines, "lines"):
      pass
  if True:  # pylint: disable=using-constant-test
    for _ in progressbar(
        lines,
        "blines",
        units_scale=BINARY_BYTES_SCALE,
        itemlenfunc=len,
        total=sum(len(line) for line in lines),
    ):
      pass
  if True:  # pylint: disable=using-constant-test
    for _ in progressbar(
        lines,
        "lines update 2s",
        update_period=2,
        report_print=True,
    ):
      pass
  if True:  # pylint: disable=using-constant-test
    P = Progress(
        name=__file__,
        ##total=len(lines),
        units_scale=DECIMAL_SCALE,
    )
    with open(__file__, encoding='utf8') as f:
      for _ in P.iterbar(f):
        time.sleep(0.005)
  from cs.debug import selftest as runtests  # pylint: disable=import-outside-toplevel
  runtests('cs.progress_tests')

if __name__ == '__main__':
  sys.exit(selftest(sys.argv))
