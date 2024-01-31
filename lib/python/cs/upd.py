#!/usr/bin/env python3
#
# Single line status updates.
#   - Cameron Simpson <cs@cskk.id.au>
#
# pylint: disable=too-many-lines

r'''
Single and multiple line status updates with minimal update sequences.

This is available as an output mode in `cs.logutils`.

Single line example:

    from cs.upd import Upd, nl, print
    .....
    with Upd() as U:
        for filename in filenames:
            U.out(filename)
            ... process filename ...
            U.nl('an informational line to stderr')
            print('a line to stdout')

Multiline multithread example:

    from threading import Thread
    from cs.upd import Upd, print
    .....
    def runner(filename, proxy):
        # initial status message
        proxy.text = "process %r" % filename
        ... at various points:
            # update the status message with current progress
            proxy.text = '%r: progress status here' % filename
        # completed, remove the status message
        proxy.close()
        # print completion message to stdout
        print("completed", filename)
    .....
    with Upd() as U:
        U.out("process files: %r", filenames)
        Ts = []
        for filename in filenames:
            proxy = U.insert(1) # allocate an additional status line
            T = Thread(
                "process "+filename,
                target=runner,
                args=(filename, proxy))
            Ts.append(T)
            T.start()
        for T in Ts:
            T.join()

## A note about Upd and terminals

I routinely use an `Upd()` as a progress reporting tool for commands
running on a terminal. This attaches to `sys.stderr` by default.
However, it is usually not desirable to run an `Upd` display
if the backend is not a tty/terminal.
Therefore, an `Upd` has a "disabled" mode
which performs no output;
the default behaviour is that this mode activates
if the backend is not a tty (as tested by `backend.isatty()`).
The constructor has an optional parameter `disabled` to override
this default behaviour.
'''

import atexit
from builtins import print as builtin_print
from contextlib import contextmanager
from functools import partial
import os
import sys
from threading import RLock, Thread
import time
from typing import Optional, Union

from cs.context import stackattrs
from cs.deco import decorator, default_params, fmtdoc
from cs.gimmicks import open_append, warning
from cs.lex import unctrl
from cs.obj import SingletonMixin
from cs.resources import MultiOpenMixin
from cs.threads import HasThreadState, ThreadState
from cs.tty import ttysize
from cs.units import transcribe, TIME_SCALE

try:
  import curses
except ImportError as curses_e:
  warning("cannot import curses: %s", curses_e)
  curses = None

__version__ = '20240201'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.gimmicks',
        'cs.lex',
        'cs.obj>=20210122',
        'cs.resources',
        'cs.threads',
        'cs.tty',
        'cs.units',
    ],
}

CS_UPD_BACKEND_ENVVAR = 'CS_UPD_BACKEND'

def _cleanup():
  ''' Cleanup function called at programme exit to clear the status lines.
  '''
  for U in Upd._singleton_instances():  # pylint: disable=protected-access
    U.shutdown()

atexit.register(_cleanup)

# pylint: disable=too-many-public-methods,too-many-instance-attributes
class Upd(SingletonMixin, MultiOpenMixin, HasThreadState):
  ''' A `SingletonMixin` subclass for maintaining multiple status lines.

      The default backend is `sys.stderr`.
  '''

  perthread_state = ThreadState()

  # pylint: disable=unused-argument
  @staticmethod
  def _singleton_key(backend=None, columns=None, disabled=False):
    if backend is None:
      backend = sys.stderr
    return id(backend)

  # pylint: disable=too-many-branches
  @fmtdoc
  def __init__(self, backend=None, columns=None, disabled=None):
    ''' Initialise the `Upd`.

        Parameters:
        * `backend`: the output file, default from the environment
          variable `${CS_UPD_BACKEND_ENVVAR}` otherwise `sys.stderr`
        * `columns`: the width of the output,
          default from the width of the `backend` tty if it is a tty,
          `80` otherwise
        * `disabled`: if true, disable the output - just keep state;
          default true if the output is not a tty;
          this automatically silences the `Upd` if stderr is not a tty
    '''
    if hasattr(self, '_backend'):
      return
    if backend is None:
      try:
        backend_path = os.environ[CS_UPD_BACKEND_ENVVAR]
      except KeyError:
        backend = sys.stderr
      else:
        backend = open_append(backend_path)
    self._backend = backend
    assert self._backend is not None
    # test isatty and the associated file descriptor
    isatty = backend.isatty()
    if isatty:
      try:
        backend_fd = backend.fileno()
      except OSError:
        backend_fd = None
        isatty = False
    else:
      backend_fd = None
    if columns is None:
      columns = 80
      if backend.isatty():
        rc = ttysize(backend)
        if rc.columns is not None:
          columns = rc.columns
    if disabled is None:
      try:
        disabled = not backend.isatty()
      except AttributeError:
        disabled = True
    self._backend_isatty = isatty
    self._backend_fd = backend_fd
    # prepare the terminfo capability mapping, if any
    self._ti_strs = {}
    if isatty:
      if curses is not None:
        try:
          # pylint: disable=no-member
          curses.setupterm(fd=backend_fd)
        except TypeError:
          pass
        else:
          for ti_name in (
              'vi',  # cursor invisible
              'vs',  # cursor visible
              'cuu1',  # cursor up one line
              'dl1',  # delete one line
              'il1',  # insert one line
              'el',  # clear to end of line
          ):
            # pylint: disable=no-member
            s = curses.tigetstr(ti_name)
            if s is not None:
              s = s.decode('ascii')
            self._ti_strs[ti_name] = s
    self._disabled = disabled
    self._disabled_backend = None
    self.columns = columns
    self._cursor_visible = True
    self._current_slot = None
    self._lock = RLock()
    self._reset()

  def _reset(self):
    ''' Set up the initial internal empty state.
        This does *not* do anything with the display.
    '''
    self._current_slot = 0
    self._above = None
    self._slot_text = ['']
    proxy0 = UpdProxy(index=None, upd=self)
    self._proxies = [proxy0]
    proxy0.index = 0

  def __str__(self):
    backend = self._disabled_backend if self._disabled else self._backend
    return "%s(backend=%s)" % (self.__class__.__name__, backend)

  ############################################################
  # Context manager methods.
  #

  def __enter_exit__(self):
    ''' Generator supporting `__enter__` and `__exit__`.

        On shutdown, if we are exiting because of an exception
        which is not a `SystemExit` with a `code` of `None` or `0`
        then we preserve the status lines one screen.
        Otherwise we clean up the status lines.
    '''
    with MultiOpenMixin.as_contextmanager(self):
      with HasThreadState.as_contextmanager(self):
        yield

  @contextmanager
  def startup_shutdown(self):
    if self._current_slot is None:
      self._reset()
    try:
      yield
    except Exception as e:  # pylint: disable=broad-except
      preserve_display = not (
          isinstance(e, SystemExit) and
          (e.code == 0 if isinstance(e.code, int) else e.code is None)
      )
      self.shutdown(preserve_display=preserve_display)
    else:
      self.shutdown(preserve_display=False)

  def shutdown(self, preserve_display=False):
    ''' Clean out this `Upd`, optionally preserving the displayed status lines.
    '''
    try:
      lock = self._lock
    except AttributeError:
      return
    slots = getattr(self, '_slot_text', None)
    if not preserve_display:
      # remove the Upd display
      with lock:
        while len(slots) > 1:
          del self[len(slots) - 1]
        self[0] = ''
    elif not self._disabled and self._backend is not None:
      # preserve the display for debugging purposes
      # move to the bottom and emit a newline
      with lock:
        txts = self._move_to_slot_v(self._current_slot, 0)
        if slots[0]:
          # preserve the last status line if not empty
          txts.append('\n')
        txts.append(self._set_cursor_visible(True))
        self._backend.write(''.join(txts))
        self._backend.flush()

  ############################################################
  # Sequence methods.
  #

  def __len__(self):
    ''' The length of an `Upd` is the number of slots.
    '''
    return len(self._slot_text)

  def __getitem__(self, index):
    ''' The text of the status line at `index`.
    '''
    return self._slot_text[index]

  def __setitem__(self, index, txt):
    self.out(txt, slot=index)

  def __delitem__(self, index):
    self.delete(index)

  def _set_cursor_visible(self, mode):
    ''' Set the cursor visibility mode, return terminal sequence.
    '''
    txt = ''
    if self._cursor_visible:
      if not mode:
        txt = self.ti_str('vi') or ''
        self._cursor_visible = False
    else:
      if mode:
        txt = self.ti_str('vs') or ''
        self._cursor_visible = True
    return txt

  def cursor_visible(self):
    ''' Make the cursor visible.
    '''
    with self._lock:
      if not self._disabled and self._backend is not None:
        self._backend.write(self._set_cursor_visible(True))
        self._backend.flush()

  def cursor_invisible(self):
    ''' Make the cursor vinisible.
    '''
    with self._lock:
      if not self._disabled and self._backend is not None:
        self._backend.write(self._set_cursor_visible(False))
        self._backend.flush()

  @property
  def disabled(self):
    ''' Whether this `Upd` is currently disabled.
    '''
    return self._disabled

  @disabled.setter
  def disabled(self, new_state):
    ''' Update the `.disabled` property.
    '''
    if new_state:
      self.disable()
    else:
      self.enable()

  # Enable/disable. TODO: restore/withdrawn upd display on toggle.
  def enable(self):
    ''' Enable updates.
    '''
    with self._lock:
      if self._disabled:
        self._backend = self._disabled_backend
        self._disabled_backend = None
        self._disabled = False

  def disable(self):
    ''' Disable updates.
    '''
    with self._lock:
      if not self._disabled:
        self._disabled_backend = self._backend
        self._backend = None
        self._disabled = True

  def proxy(self, index):
    ''' Return the `UpdProxy` for `index`.
        Returns `None` if `index` is out of range.
        The index `0` is never out of range;
        it will be autocreated if there are no slots yet.
    '''
    try:
      return self._proxies[index]
    except IndexError:
      # autocreate slot 0
      if index == 0:
        return self.insert(0)
      return None

  def _update_proxies(self):
    ''' Update the `UpdProxy` indices.
    '''
    proxies = self._proxies
    with self._lock:
      for index in range(len(self._slot_text)):
        proxies[index].index = index

  def ti_str(self, ti_name):
    ''' Fetch the terminfo capability string named `ti_name`.
        Return the string or `None` if not available.
    '''
    return self._ti_strs.get(ti_name, None)

  @staticmethod
  def normalise(txt):
    ''' Normalise `txt` for display,
        currently implemented as:
        `unctrl(txt.rstrip())`.
    '''
    return unctrl(txt.rstrip())

  @classmethod
  def diff(cls, oldtxt, newtxt, columns, raw_text=False):
    ''' Compute the text sequences required to update `oldtxt` to `newtxt`
        presuming the cursor is at the right hand end of `oldtxt`.
        The available area is specified by `columns`.

        We normalise `newtxt` as using `self.normalise`.
        `oldtxt` is presumed to be already normalised.

        If `raw_text` is true (default `False`) we do not normalise `newtxt`
        before comparison.
    '''
    # normalise text
    if not raw_text:
      newtxt = cls.normalise(newtxt)
    # crop for terminal width
    newlen = len(newtxt)
    if newlen >= columns:
      newtxt = newtxt[:columns - 1]
      newlen = len(newtxt)
    oldlen = len(oldtxt)
    pfxlen = min(newlen, oldlen)
    # compute length of common prefix
    for i in range(pfxlen):
      if newtxt[i] != oldtxt[i]:
        pfxlen = i
        break
    # Rewrites take one of two forms:
    #   Backspace to end of common prefix, overwrite with the differing tail
    #     of the new string, erase trailing extent if any.
    #   Return to start of line with carriage return, overwrite with new
    #    string, erase trailing extent if any.
    # Therefore compare backspaces against cr+pfxlen.
    #
    if oldlen - pfxlen < 1 + pfxlen:
      # backspace and partial overwrite
      difftxts = ['\b' * (oldlen - pfxlen), newtxt[pfxlen:]]
    else:
      # carriage return and complete overwrite
      difftxts = ['\r', newtxt]
    # trailing text to overwrite with spaces?
    extlen = oldlen - newlen
    if extlen > 0:
      # old line was longer - write spaces over the old tail
      difftxts.append(' ' * extlen)
      difftxts.append('\b' * extlen)
    return difftxts

  def _move_to_slot_v(self, from_slot, to_slot):
    ''' Compute the text sequences required to move our cursor
        to the end of `to_slot` from `from_slot`.
    '''
    if from_slot is None:
      from_slot = self._current_slot
    assert from_slot >= 0
    assert from_slot == 0 or from_slot < len(self)
    assert to_slot >= 0
    assert to_slot == 0 or to_slot < len(self)
    movetxts = []
    if to_slot != from_slot:
      # move cursor to target slot
      if to_slot < from_slot:
        # emit VT
        movetxts.append('\v' * (from_slot - to_slot))
      else:
        # emit cursor_up
        cursor_up = self.ti_str('cuu1')
        movetxts.append(cursor_up * (to_slot - from_slot))
      # adjust horizontal position
      oldtxt = self._slot_text[to_slot]
      vpos_cur = len(self._slot_text[from_slot])
      vpos_slot = len(oldtxt)
      if vpos_cur > vpos_slot:
        # backspace
        movetxts.append('\b' * (vpos_cur - vpos_slot))
      elif vpos_cur < vpos_slot:
        # overwrite to advance cursor
        movetxts.append(oldtxt[vpos_cur:])
    return movetxts

  def _redraw_line_v(self, txt):
    ''' Compute the text sequences to redraw the specified slot,
        being `CR`, slot text, clear to end of line.
    '''
    txts = ['\r', txt]
    clr_eol = self.ti_str('el')
    if clr_eol:
      txts.append(clr_eol)
    else:
      pad_len = self.columns - len(txt) - 1
      if pad_len > 0:
        txts.append(' ' * pad_len)
        txts.append('\b' * pad_len)
    return txts

  def _redraw_slot_v(self, slot):
    ''' Compute the text sequences to redraw the specified slot,
        being `CR`, slot text, clear to end of line.

        This presumes the cursor is on the requisite line.
    '''
    return self._redraw_line_v(self._slot_text[slot])

  def _redraw_trailing_slots_v(self, upper_slot, skip_first_vt=False):
    ''' Compute text sequences to redraw the slots from `upper_slot` downward,
        leaving the cursor at the end of the lowest slot.

        This presumes the cursor is on the line _above_
        the uppermost slot to redraw,
        as the sequences commence with `'\v'` (`VT`).
    '''
    txts = []
    first = True
    for slot in range(upper_slot, -1, -1):
      if not first or not skip_first_vt:
        txts.append('\v')
      txts.extend(self._redraw_slot_v(slot))
      first = False
    return txts

  def flush(self):
    ''' Flush the backend stream.
    '''
    if self._disabled:
      return
    backend = self._backend
    if backend is not None:
      backend.flush()

  def _update_slot_v(self, slot, newtxt, raw_text=False, redraw=False):
    ''' Compute the text sequences to update the status line at `slot` to `newtxt`.
    '''
    # move to target slot and collect reference text
    txts = self._move_to_slot_v(self._current_slot, slot)
    txts.extend(
        (
            self._redraw_slot_v(slot) if redraw else self.diff(
                self._slot_text[slot],
                newtxt,
                self.columns,
                raw_text=raw_text,
            )
        )
    )
    return txts

  def out(self, txt, *a, slot=0, raw_text=False, redraw=False) -> str:
    ''' Update the status line at `slot` to `txt`.
        Return the previous status line content.

        Parameters:
        * `txt`: the status line text.
        * `a`: optional positional parameters;
          if not empty, `txt` is percent formatted against this list.
        * `slot`: which slot to update; default is `0`, the bottom slot
        * `raw_text`: if true (default `False`), do not normalise the text
        * `redraw`: if true (default `False`), redraw the whole line
          instead of doing the minimal and less visually annoying
          incremental change
    '''
    if a:
      txt = txt % a
    if not raw_text:
      txt = self.normalise(txt)
    backend = self._backend
    with self._lock:
      slots = self._slot_text
      if slot == 0 and not slots:
        self.insert(0)
      try:
        oldtxt = slots[slot]
      except IndexError:
        ##debug("%s.out(slot=%d): %s, ignoring %r", self, slot, e, txt)
        return ''
      if self._disabled or self._backend is None:
        slots[slot] = txt
      else:
        if oldtxt != txt:
          txts = self._update_slot_v(slot, txt, raw_text=True, redraw=redraw)
          self._current_slot = slot
          slots[slot] = txt
          backend.write(''.join(txts))
          backend.flush()
    return oldtxt

  def nl(self, txt, *a, redraw=False):
    ''' Write `txt` to the backend followed by a newline.

        Parameters:
        * `txt`: the message to write.
        * `a`: optional positional parameters;
          if not empty, `txt` is percent formatted against this list.
        * `redraw`: if true (default `False`) use the "redraw" method.

        This uses one of two methods:
        * insert above:
          insert a line above the top status line and write the message there.
        * redraw:
          clear the top slot, write txt and a newline,
          redraw all the slots below.

        The latter method is used if `redraw` is true
        or if `txt` is wider than `self.columns`
        or if there is no "insert line" capability.
    '''
    if self._disabled or self._backend is None:
      return
    if a:
      txt = txt % a
    txts = []
    with self._lock:
      slots = self._slot_text
      if not slots:
        self._backend.write(txt)
        self._backend.write('\n')
        return
      if len(txt) >= self.columns:
        # the line will overflow, force a complete redraw approach
        redraw = True
      clr_eol = self.ti_str('el')
      insert_line = self.ti_str('il1')
      cursor_up = self.ti_str('cuu1')
      if not insert_line or not cursor_up:
        redraw = True
      # move to the top slot
      top_slot = len(slots) - 1
      if redraw:
        # go to the top slot, overwrite it and then rewrite the slots below
        txts.extend(self._move_to_slot_v(self._current_slot, top_slot))
        txts.extend(self._redraw_line_v(''))
        txts.append(txt)
        if clr_eol:
          txts.append(clr_eol)
        txts.extend(self._redraw_trailing_slots_v(top_slot))
        self._current_slot = 0
      else:
        # make sure insert line does not push the bottom line off the screen
        # by forcing a scroll
        txts.extend(self._move_to_slot_v(self._current_slot, 0))
        self._current_slot = 0
        txts.append('\v')
        txts.append(cursor_up)
        # insert the output line above the top slot
        txts.extend(self._move_to_slot_v(self._current_slot, top_slot))
        txts.append('\r')
        txts.append(insert_line)
        txts.append(txt)
        if clr_eol:
          txts.append(clr_eol)
        txts.append('\v\r')
        txts.append(slots[top_slot])
        self._current_slot = top_slot
      self._backend.write(''.join(txts))
      self._backend.flush()

  @contextmanager
  def above(self, need_newline=False):
    ''' Context manager to move to the top line of the display, clear it, yield, redraw below.

        This context manager is for use when interleaving _another_
        stream with the `Upd` display;
        if you just want to write lines above the display
        for the same backend use `Upd.nl`.

        The usual situation for `Upd.above`
        is interleaving `sys.stdout` and `sys.stderr`,
        which are often attached to the same terminal.

        Note that the caller's output should be flushed
        before exiting the suite
        so that the output is completed before the `Upd` resumes.

        Example:

            U = Upd()   # default sys.stderr Upd
            ......
            with U.above():
                print('some message for stdout ...', flush=True)
    '''
    if self._disabled or self._backend is None or not self._slot_text:
      yield
      return
    # go to the top slot, overwrite it and then rewrite the slots below
    with self._lock:
      backend = self._backend
      slots = self._slot_text
      txts = []
      top_slot = len(slots) - 1
      txts.extend(self._move_to_slot_v(self._current_slot, top_slot))
      txts.extend(self._redraw_line_v(''))
      backend.write(''.join(txts))
      backend.flush()
      self._current_slot = top_slot
      try:
        self.disable()
        yield
      finally:
        self.enable()
        txts = []
        if need_newline:
          clr_eol = self.ti_str('el')
          if clr_eol:
            txts.append(clr_eol)
            txts.append('\v\r')
        top_slot = len(slots) - 1
        txts.extend(
            self._redraw_trailing_slots_v(top_slot, skip_first_vt=True)
        )
        backend.write(''.join(txts))
        backend.flush()
        self._current_slot = 0

  @contextmanager
  def without(self, temp_state='', slot=0):
    ''' Context manager to clear the status line around a suite.
        Returns the status line text as it was outside the suite.

        The `temp_state` parameter may be used to set the inner status line
        content if a value other than `''` is desired.
    '''
    if self._disabled or self._backend is None:
      yield
    else:
      with self._lock:
        old = self.out(temp_state, slot=slot)
        try:
          yield old
        finally:
          self.out(old, slot=slot)

  def selfcheck(self):
    ''' Sanity check the internal data structures.

        Warning: this uses asserts.
    '''
    with self._lock:
      assert len(self._slot_text) == len(self._proxies)
      assert len(self._slot_text) > 0
      for i, proxy in enumerate(self._proxies):
        assert proxy.upd is self
        assert proxy.index == i
    return True

  # pylint: disable=too-many-branches,too-many-statements
  def insert(self, index, txt='', proxy=None, **proxy_kw) -> "UpdProxy":
    ''' Insert a new status line at `index`.
        Return the `UpdProxy` for the new status line.
    '''
    assert index is not None
    if proxy:
      if proxy.upd is not None:
        raise ValueError(
            "proxy %s already associated with an Upd: %s" % (proxy, proxy.upd)
        )
      if proxy_kw:
        raise ValueError("cannot supply both a proxy and **proxy_kw")
    slots = self._slot_text
    assert slots
    proxies = self._proxies
    assert proxies
    txts = []
    with self._lock:
      if index < 0:
        # convert negative index to len-index, then check range
        index0 = index
        index = len(self) + index
        if index < 0:
          raise ValueError(
              "index should be in the range 0..%d inclusive: got %s" %
              (len(self), index0)
          )
      elif index > len(self):
        raise ValueError(
            "index should be in the range 0..%d inclusive: got %s" %
            (len(self), index)
        )
      if proxy is None:
        # create the proxy, which inserts it
        return UpdProxy(index=index, upd=self, prefix=txt, **proxy_kw)

      # associate the proxy with self
      assert proxy.upd is None
      proxy.index = index
      proxy.upd = self

      # no display? just insert the slot
      if self._disabled or self._backend is None:
        slots.insert(index, txt)
        proxies.insert(index, proxy)
        return proxy

      # not disabled: manage the display
      cursor_up = self.ti_str('cuu1')
      insert_line = self.ti_str('il1')
      if not cursor_up:
        raise IndexError(
            "TERM=%s: no cuu1 (cursor_up) capability, cannot support multiple status lines"
            % (os.environ.get('TERM'),)
        )
      # make sure insert line does not push the bottom line off the screen
      # by forcing a scroll: move to bottom, VT, cursor up
      if insert_line:
        txts.extend(self._move_to_slot_v(self._current_slot, 0))
        self._current_slot = 0
        txts.append('\v')
        txts.append(cursor_up)
        # post: inserting a line will not drive the lowest line off the screen
      if index == 0:
        # move to bottom slot, add line below
        txts.extend(self._move_to_slot_v(self._current_slot, 0))
        txts.append('\v\r')
        if insert_line:
          txts.append(insert_line)
          txts.append(txt)
        else:
          txts.extend(self._redraw_line_v(txt))
        slots.insert(index, txt)
        proxies.insert(index, proxy)
        self._update_proxies()
        self._current_slot = 0
      else:
        # move to the line which is to be below the inserted line,
        # insert line above that
        txts.extend(self._move_to_slot_v(self._current_slot, index - 1))
        slots.insert(index, txt)
        proxies.insert(index, proxy)
        self._update_proxies()
        if insert_line:
          # insert a line with `txt`
          txts.append(insert_line)
          txts.append('\r')
          txts.append(txt)
          self._current_slot = index
        else:
          # no insert, just redraw from here down completely
          txts.extend(self._redraw_trailing_slots_v(index, skip_first_vt=True))
          self._current_slot = 0
      self._backend.write(''.join(txts))
      self._backend.flush()
    return proxy

  def delete(self, index):
    ''' Delete the status line at `index`.

        Return the `UpdProxy` of the deleted status line.
    '''
    slots = self._slot_text
    proxies = self._proxies
    with self._lock:
      if len(slots) == 0 and index == 0:
        return None
      if index < 0 or index >= len(slots):
        ##debug("Upd.delete(index=%d): index out of range, ignored", index)
        return None
      if len(slots) == 1:
        # silently do not delete
        ##raise ValueError("cannot delete the last slot")
        return None
      if self._disabled or self._backend is None:
        # just remote the data entries
        del slots[index]
        proxy = proxies[index]
        proxy.index = None
        del proxies[index]
        self._update_proxies()
      else:
        delete_line = self.ti_str('dl1')
        cursor_up = self.ti_str('cuu1')
        txts = self._move_to_slot_v(self._current_slot, index)
        self._current_slot = index
        del slots[index]
        proxy = proxies[index]
        proxy.index = None
        del proxies[index]
        self._update_proxies()
        if self._current_slot >= len(self):
          assert self._current_slot == len(self)
          self._current_slot -= 1
        if index == 0:
          if delete_line:
            # erase bottom line and move up and then to the end of that slot
            txts.append(delete_line)
          else:
            # clear the bottom lone
            txts.extend(self._redraw_line_v(''))
          # move up and to the end of that slot
          txts.append(cursor_up)
          txts.append('\r')
          txts.append(slots[index])
        else:
          # the effective index has now moved down
          index -= 1
          if delete_line:
            # delete line and advance to the end of the new current line
            txts.extend((delete_line, '\r', slots[index]))
            self._current_slot = index
          else:
            # no delete line: redraw from here on down then clear the line below
            txts.extend(
                self._redraw_trailing_slots_v(index, skip_first_vt=True)
            )
            txts.append('\v')
            txts.extend(self._redraw_line_v(''))
            txts.append(cursor_up)
            txts.append(slots[0])
            self._current_slot = 0
        self._backend.write(''.join(txts))
        self._backend.flush()
      return proxy

  # pylint: disable=too-many-arguments
  @contextmanager
  def run_task(
      self,
      label: str,
      *,
      report_print=False,
      tick_delay: int = 0.3,
      tick_chars='|/-\\',
  ):
    ''' Context manager to display an `UpdProxy` for the duration of some task.
        It yields the proxy.
    '''
    if tick_delay < 0:
      raise ValueError(
          "run_task(%r,...,tick_delay=%s): tick_delay should be >=0" %
          (label, tick_delay)
      )
    with self.insert(1, label + ' ') as proxy:
      ticker_runstate = None
      if tick_delay > 0:
        from cs.resources import RunState  # pylint: disable=import-outside-toplevel
        ticker_runstate = RunState()

        def _ticker():
          i = 0
          while not ticker_runstate.cancelled:
            proxy.suffix = ' ' + tick_chars[i % len(tick_chars)]
            i += 1
            time.sleep(tick_delay)

        Thread(target=_ticker, daemon=True).start()
      proxy.text = '...'
      start_time = time.time()
      try:
        yield proxy
      finally:
        end_time = time.time()
        if ticker_runstate:
          # shut down the ticker
          ticker_runstate.cancel()
    elapsed_time = end_time - start_time
    if report_print:
      if isinstance(report_print, bool):
        report_print = print
      report_print(
          label + ': in',
          transcribe(elapsed_time, TIME_SCALE, max_parts=2, skip_zero=True)
      )

@decorator
def uses_upd(func):
  ''' Decorator for functions accepting an optional `upd:Upd` parameter,
      default from `Upd.default() or Upd()`.
      This also makes the `upd` the default `Upd` instance for this thread.
  '''

  def with_func(*a, upd: Upd, **kw):
    with upd:
      return func(*a, upd=upd, **kw)

  return default_params(with_func, upd=lambda: Upd.default() or Upd())

@uses_upd
def out(msg, *a, upd, **outkw):
  ''' Update the status line of the default `Upd` instance.
      Parameters are as for `Upd.out()`.
  '''
  return upd.out(msg, *a, **outkw)

# pylint: disable=redefined-builtin
@uses_upd
def print(*a, upd: Upd, end='\n', **kw):
  ''' Wrapper for the builtin print function
      to call it inside `Upd.above()` and enforce a flush.

      The function supports an addition parameter beyond the builtin print:
      * `upd`: the `Upd` instance to use, default `Upd()`

      Programmes integrating `cs.upd` with use of the builtin `print`
      function should use this at import time:

          from cs.upd import print
  '''
  kw['flush'] = True
  with upd.above(need_newline=not end.endswith('\n')):
    builtin_print(*a, end=end, **kw)

@uses_upd
def pfxprint(*a, upd, **kw):
  ''' Wrapper for `cs.pfx.pfxprint` to pass `print_func=cs.upd.print`.

      Programmes integrating `cs.upd` with use of the `cs.pfx.pfxprint`
      function should use this at import time:

          from cs.upd import pfxprint
  '''
  # pylint: disable=import-outside-toplevel
  from cs.pfx import pfxprint as base_pfxprint
  return base_pfxprint(*a, print_func=partial(print, upd=upd), **kw)

@contextmanager
@uses_upd
def run_task(*a, upd, **kw):
  ''' Top level `run_task` function to call `Upd.run_task`.
  '''
  with upd.run_task(*a, **kw) as proxy:
    yield proxy

@uses_upd
def nl(msg, *a, upd, **kw):
  ''' Write `msg` to `file` (default `sys.stdout`),
      without interfering with the `Upd` instance.
      This is a thin shim for `Upd.print`.
  '''
  if a:
    msg = msg % a
  if 'file' not in kw:
    kw['file'] = sys.stderr
  print(msg, upd=upd, **kw)

class UpdProxy(object):
  ''' A proxy for a status line of a multiline `Upd`.

      This provides a stable reference to a status line after it has been
      instantiated by `Upd.insert`.

      The status line can be accessed and set via the `.text` property.

      An `UpdProxy` is also a context manager which self deletes on exit:

          U = Upd()
          ....
          with U.insert(1, 'hello!') as proxy:
              .... set proxy.text as needed ...
          # proxy now removed
  '''

  __slots__ = {
      'upd': 'The parent Upd instance.',
      'index': 'The index of this slot within the parent Upd.',
      '_prefix': 'The fixed leading prefix for this slot, default "".',
      '_text': 'The text following the prefix for this slot, default "".',
      '_text_auto':
      'An optional callable to generate the text if _text is empty.',
      '_suffix': 'The fixed trailing suffix or this slot, default "".',
      'update_period': 'Update time interval.',
      'last_update': 'Time of last update.',
  }

  @uses_upd
  def __init__(
      self,
      text: Optional[str] = None,
      *,
      upd: Upd,
      index: Union[int, None] = 1,
      prefix: Optional[str] = None,
      suffix: Optional[str] = None,
      text_auto=None,
      update_period: Optional[float] = None,
  ):
    ''' Initialise a new `UpdProxy` status line.

        Parameters:
        * `index`: optional position for the new proxy as for `Upd.insert`,
          default `1` (directly above the bottom status line)
        * `upd`: the `Upd` instance with which to associate this proxy,
          default the default `Upd` instance (associated with `sys.stderr`)
        * `text`: optional initial text for the new status line
    '''
    self.upd = None
    self.index = index
    self._prefix = prefix or ''
    self._text = ''
    self._text_auto = text_auto
    self._suffix = suffix or ''
    self.update_period = update_period
    self.last_update = None
    if index is None:
      self.upd = upd
    else:
      self.upd = None
      upd.insert(index, proxy=self)
      assert self.index is not None
    if text:
      self(text)

  def __str__(self):
    return (
        "%s(upd=%s,index=%s:%r)" %
        (self.__class__.__name__, self.upd, self.index, self.text)
    )

  def __call__(self, msg, *a):
    ''' Calling the proxy sets its `.text` property
        in the form used by other messages: `(msg,*a)`
    '''
    if a:
      msg = msg % a
    self.text = msg

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.delete()

  def _update(self):
    upd = self.upd
    if upd is None:
      return
    index = self.index
    if index is None:
      return
    update_period = self.update_period
    if update_period:
      now = time.time()
      if (self.last_update is not None
          and now - self.last_update < update_period):
        return
    txt = upd.normalise(self._prefix + self._text + self._suffix)
    with upd._lock:  # pylint: disable=protected-access
      overflow = len(txt) - upd.columns + 1
      if overflow > 0:
        txt = '<' + txt[overflow + 1:]
      self.upd[index] = txt  # pylint: disable=unsupported-assignment-operation
    if update_period:
      self.last_update = now

  def reset(self):
    ''' Clear the proxy: set both the prefix and text to `''`.
    '''
    self._prefix = ''
    self._text = ''
    self._suffix = ''
    self._update()

  @property
  def prefix(self):
    ''' The current prefix string.
    '''
    return self._prefix

  @prefix.setter
  def prefix(self, new_prefix):
    ''' Change the prefix, redraw the status line.
    '''
    old_prefix, self._prefix = self._prefix, new_prefix
    if new_prefix != old_prefix:
      self._update()

  @contextmanager
  def extend_prefix(self, more_prefix, print_elapsed=False):
    ''' Context manager to append text to the prefix.
    '''
    new_prefix = self.prefix + more_prefix
    with stackattrs(self, prefix=new_prefix):
      start_time = time.time()
      yield new_prefix
    if print_elapsed:
      end_time = time.time()
      print("%s: %ss" % (new_prefix, end_time - start_time))

  @property
  def text(self):
    ''' The text of this proxy's slot, without the prefix.
    '''
    return self._text or ('' if self._text_auto is None else self._text_auto())

  @text.setter
  def text(self, txt):
    ''' Set the text of the status line.

        If the length of `self.prefix+txt` exceeds the available display
        width then the leftmost text is cropped to fit.
    '''
    self._text = txt
    self._update()

  @property
  def suffix(self):
    ''' The current suffix string.
    '''
    return self._suffix

  @suffix.setter
  def suffix(self, new_suffix):
    ''' Change the suffix, redraw the status line.
    '''
    old_suffix, self._suffix = self._suffix, new_suffix
    if new_suffix != old_suffix:
      self._update()

  @property
  def width(self):
    ''' The available space for text after `self.prefix` and before `self.suffix`.

        This is available width for uncropped text,
        intended to support presizing messages such as progress bars.
        Setting the text to something longer will crop the rightmost
        portion of the text which fits.
    '''
    prefix = self.prefix
    suffix = self.suffix
    upd = self.upd
    return (
        (upd.columns if upd else 80) - 1 - (len(prefix) if prefix else 0) -
        (len(suffix) if suffix else 0)
    )

  def delete(self):
    ''' Delete this proxy from its parent `Upd`.
    '''
    if self.upd is not None:
      with self.upd._lock:  # pylint: disable=protected-access
        index = self.index
        if index is None:
          self.text = ''
        else:
          self.upd.delete(index)

  __del__ = delete

  def insert(self, index, txt=''):
    ''' Insert a new `UpdProxy` at a position relative to this `UpdProxy`.
        Return the new proxy.

        This supports the positioning of related status lines.
    '''
    upd = self.upd
    if not upd:
      raise ValueError("no .upd, cannot create a new proxy")
    with upd._lock:  # pylint: disable=protected-access
      if self.index is not None:
        index += self.index
      return upd.insert(index, txt)

@decorator
def with_upd_proxy(func, prefix=None, insert_at=1):
  ''' Decorator to run `func` with an additional parameter `upd_proxy`
      being an `UpdProxy` for progress reporting.

      Example:

          @with_upd_proxy
          def func(*a, upd_proxy:UpdProxy, **kw):
            ... perform task, updating upd_proxy ...
  '''

  if prefix is None:
    prefix = func.__name__

  @uses_upd
  def upd_with_proxy_wrapper(*a, upd, **kw):
    with upd.insert(insert_at) as proxy:
      with stackattrs(proxy, prefix=prefix):
        return func(*a, upd_proxy=proxy, **kw)

  return upd_with_proxy_wrapper

# Always create a default Upd() in open state.
# Keep a module level name, which avoids the singleton weakref array
# losing track of it.
# TODO: this indicates a bug in this, the singleton stuff and/or
# the multiopenmixin stuff.
_main_upd = Upd().open()

def demo():
  ''' A tiny demo function for visual checking of the basic functionality.
  '''
  from time import sleep  # pylint: disable=import-outside-toplevel
  U = Upd()
  p = U.proxy(0)
  for n in range(10):
    p(str(n))
    sleep(0.1)
  # proxy line above
  p2 = U.insert(1)
  p2.prefix = 'above: '
  for n in range(10):
    p(str(n))
    p2("2:" + str(n))
    sleep(0.1)

if __name__ == '__main__':
  demo()
