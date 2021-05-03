#!/usr/bin/python
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

from __future__ import with_statement, print_function
import atexit
from builtins import print as builtin_print
from contextlib import contextmanager
import os
import sys
from threading import RLock
from cs.context import stackattrs, StackableState
from cs.deco import decorator
from cs.gimmicks import warning
from cs.lex import unctrl
from cs.obj import SingletonMixin
from cs.tty import ttysize

try:
  import curses
except ImportError as e:
  warning("cannot import curses: %s", e)
  curses = None

__version__ = '20210428.4-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context', 'cs.deco', 'cs.gimmicks', 'cs.lex', 'cs.obj>=20210122',
        'cs.tty'
    ],
}

def _cleanup():
  ''' Cleanup function called at programme exit to clear the status lines.
  '''
  for U in Upd._singleton_instances():  # pylint: disable=protected-access
    U.shutdown()

atexit.register(_cleanup)

# A couple of convenience functions.

def out(msg, *a, **outkw):
  ''' Update the status line of the default `Upd` instance.
      Parameters are as for `Upd.out()`.
  '''
  return Upd().out(msg, *a, **outkw)

# pylint: disable=redefined-builtin
def print(*a, **kw):
  ''' Wrapper for the builtin print function
      to call it inside `Upd.above()` and enforce a flush.

      The function supports an addition parameter beyond the builtin print:
      * `upd`: the `Upd` instance to use, default `Upd()`

      Programmes integrating `cs.upd` with use of the builtin `print`
      function should use this at import time:

          from cs.upd import print
  '''
  upd = kw.pop('upd', None)
  if upd is None:
    upd = Upd()
  end = kw.get('end', '\n')
  kw['flush'] = True
  with upd.above(need_newline=not end.endswith('\n')):
    builtin_print(*a, **kw)

def nl(msg, *a, **kw):
  ''' Write `msg` to `file` (default `sys.stdout`),
      without interfering with the `Upd` instance.
      This is a thin shim for `Upd.print`.
  '''
  if a:
    msg = msg % a
  if 'file' not in kw:
    kw['file'] = sys.stderr
  print(msg, **kw)

# pylint: disable=too-many-public-methods,too-many-instance-attributes
class Upd(SingletonMixin):
  ''' A `SingletonMixin` subclass for maintaining a regularly updated status line.

      The default backend is `sys.stderr`.
  '''

  # pylint: disable=unused-argument
  @staticmethod
  def _singleton_key(backend=None, columns=None, disabled=False):
    if backend is None:
      backend = sys.stderr
    return id(backend)

  # pylint: disable=too-many-branches
  def __init__(self, backend=None, columns=None, disabled=None):
    if hasattr(self, '_backend'):
      return
    if backend is None:
      backend = sys.stderr
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
    self._backend = backend
    self._backend_isatty = isatty
    self._backend_fd = backend_fd
    # prepare the terminfo capability mapping, if any
    self._ti_strs = {}
    if isatty:
      if curses is not None:
        try:
          curses.setupterm(fd=backend_fd)
        except TypeError:
          pass
        else:
          for ti_name in (
              'vi',
              'vs',  # cursor invisible/visible
              'cuu1',  # cursor up 1 line
              'dl1',  # delete 1 line
              'il1',  # insert one line
              'el',  # clear to end of line
          ):
            s = curses.tigetstr(ti_name)
            if s is not None:
              s = s.decode('ascii')
            self._ti_strs[ti_name] = s
    self._disabled = disabled
    self._disabled_backend = None
    self.columns = columns
    self._cursor_visible = True
    self._current_slot = None
    self._reset()
    self._lock = RLock()

  def _reset(self):
    ''' Set up the initial internal empty state.
        This does *not* do anything with the display.
    '''
    self._slot_text = []
    self._current_slot = None
    self._above = None
    self._proxies = []

  def __str__(self):
    return "%s(backend=%s)" % (
        type(self).__name__, getattr(self, '_backend', None)
    )

  ############################################################
  # Sequence methods.
  #

  def __len__(self):
    ''' The length of an `Upd` is the number of slots.
    '''
    return len(self._slot_text)

  def __getitem__(self, index):
    return self._slot_text[index]

  def __setitem__(self, index, txt):
    self.out(txt, slot=index)

  def __delitem__(self, index):
    self.delete(index)

  ############################################################
  # Context manager methods.
  #

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, _):
    ''' Tidy up on exiting the context.

        If we are exiting because of an exception
        which is not a `SystemExit` with a `code` of `None` or `0`
        then we preserve the status lines one screen.
        Otherwise we clean up the status lines.
    '''
    preserve_display = not (
        exc_type is None or (
            issubclass(exc_type, SystemExit) and (
                exc_val.code == 0
                if isinstance(exc_val.code, int) else exc_val.code is None
            )
        )
    )
    self.shutdown(preserve_display)

  def shutdown(self, preserve_display=False):
    ''' Clean out this `Upd`, optionally preserving the displayed status lines.
    '''
    slots = getattr(self, '_slot_text', None)
    if slots:
      if not preserve_display:
        # remove the Upd display
        with self._lock:
          while len(slots) > 1:
            del self[len(slots) - 1]
          self[0] = ''
      elif not self._disabled and self._backend is not None:
        # preserve the display for debugging purposes
        # move to the bottom and emit a newline
        with self._lock:
          txts = self._move_to_slot_v(self._current_slot, 0)
          if slots[0]:
            # preserve the last status line if not empty
            txts.append('\n')
          txts.append(self._set_cursor_visible(True))
          self._backend.write(''.join(txts))
          self.cursor_visible()
          self._backend.flush()
    self._reset()

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
        self._set_cursor_visible(True)

  def cursor_invisible(self):
    ''' Make the cursor vinisible.
    '''
    with self._lock:
      if not self._disabled and self._backend is not None:
        self._set_cursor_visible(False)

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
        Returns `None` if `index` if out of range.
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

  def close(self):
    ''' Close this Upd.
    '''
    if self._backend is not None and self._slot_text:
      self.out('')
      self._backend = None

  def closed(self):
    ''' Test whether this Upd is closed.
    '''
    return self._backend is None

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
    assert from_slot >= 0
    assert from_slot < len(self)
    assert to_slot >= 0
    assert to_slot < len(self)
    if from_slot is None:
      from_slot = self._current_slot
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

  def out(self, txt, *a, slot=0, raw_text=False, redraw=False):
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
      oldtxt = slots[slot]
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
    ''' Move to the top line of the display, clear it, yield, redraw below.

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
    else:
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
        yield
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
  def insert(self, index, txt='', proxy=None):
    ''' Insert a new status line at `index`.

        Return the `UpdProxy` for the new status line.
    '''
    if proxy and proxy.upd is not None:
      raise ValueError(
          "proxy %s already associated with an Upd: %s" % (proxy, self)
      )
    slots = self._slot_text
    proxies = self._proxies
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
        if index == 1 and len(self) == 0:
          # crop insert in the initial state
          index = 0
        else:
          raise ValueError(
              "index should be in the range 0..%d inclusive: got %s" %
              (len(self), index)
          )
      if proxy is None:
        # create the proxy, which inserts it
        return UpdProxy(index, self)

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
      first_slot = not slots
      if first_slot:
        # move to the start of the current cursor line, set _current_slot=0
        txts.append('\r')
        txts.extend(self._redraw_line_v(txt))
        self._current_slot = 0
        slots.insert(index, txt)
        proxies.insert(index, proxy)
        self._update_proxies()
      else:
        # we're inserting an additional line to a nonempty display
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
            txts.extend(
                self._redraw_trailing_slots_v(index, skip_first_vt=True)
            )
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
        raise ValueError(
            "index should be in the range 0..%d inclusive: got %s" %
            (len(self), index)
        )
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
  }

  def __init__(self, index=1, upd=None, text=None):
    ''' Initialise a new `UpdProxy` status line.

        Parameters:
        * `index`: optional position for the new proxy as for `Upd.insert`,
          default `1` (directly above the bottom status line)
        * `upd`: the `Upd` instance with which to associate this proxy,
          default the default `Upd` instance (associated with `sys.stderr`)
        * `text`: optional initial text for the new status line
    '''
    self.upd = None
    self.index = None
    if upd is None:
      upd = Upd()
    upd.insert(index, proxy=self)
    self._prefix = ''
    self._text = ''
    if text:
      self(text)

  def __str__(self):
    return (
        "%s(upd=%s,index=%s:%r)" %
        (type(self).__name__, self.upd, self.index, self.text)
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

  def reset(self):
    ''' Clear the proxy: set both the prefix and text to `''`.
    '''
    self._prefix = ''
    self.text = ''

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
      self.text = self._text

  @contextmanager
  def extend_prefix(self, more_prefix):
    ''' Context manager to append text to the prefix.
    '''
    new_prefix = self.prefix + more_prefix
    with stackattrs(self, prefix=new_prefix):
      yield new_prefix

  @property
  def text(self):
    ''' The text of this proxy's slot, without the prefix.
    '''
    return self._text

  @text.setter
  def text(self, txt):
    ''' Set the text of the status line.

        If the length of `self.prefix+txt` exceeds the available display
        width then the leftmost text is cropped to fit.
    '''
    self._text = txt
    upd = self.upd
    if upd is not None:
      with upd._lock:  # pylint: disable=protected-access
        index = self.index
        if index is not None:
          txt = upd.normalise(self.prefix + txt)
          overflow = len(txt) - upd.columns + 1
          if overflow > 0:
            txt = '<' + txt[overflow + 1:]
          self.upd[index] = txt  # pylint: disable=unsupported-assignment-operation

  @property
  def width(self):
    ''' The available space for text after `self.prefix`.

        This is available width for uncropped text,
        intended to support presizing messages such as progress bars.
        Setting the text to something longer will crop the rightmost
        portion of the text which fits.
    '''
    prefix = self.prefix
    upd = self.upd
    return (upd.columns if upd else 80) - 1 - (len(prefix) if prefix else 0)

  def delete(self):
    ''' Delete this proxy from its parent `Upd`.
    '''
    with self.upd._lock:  # pylint: disable=protected-access
      index = self.index
      if index is not None:
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

# pylint: disable=too-few-public-methods
class _UpdState(StackableState):

  def __getattr__(self, attr):
    if attr == 'upd':
      value = Upd()
    else:
      raise AttributeError("%s.%s" % (type(self).__name__, attr))
    setattr(self, attr, value)
    return value

state = _UpdState()

@decorator
def upd_proxy(func, prefix=None, insert_at=1):
  ''' Decorator to create a new `UpdProxy` and record it as `state.proxy`.

      Parameters:
      * `func`: the function to decorate
      * `prefix`: initial proxy prefix, default `func.__name__`
      * `insert_at`: the position for the new proxy, default `1`
  '''
  if prefix is None:
    prefix = func.__name__

  def upd_proxy_wrapper(*a, **kw):
    with state.upd.insert(insert_at) as proxy:
      with stackattrs(state, proxy=proxy):
        return func(*a, **kw)

  return upd_proxy_wrapper

def demo():
  ''' A tiny demo function for visual checking of the basic functionality.
  '''
  from time import sleep  # pylint: disable=import-outside-toplevel
  U = Upd()
  p = U.proxy(0)
  for n in range(20):
    p(str(n))
    sleep(0.1)
  # proxy line above
  p2 = U.insert(1)
  p2.prefix = 'above: '
  for n in range(20):
    p(str(n))
    p2(str(n))
    sleep(0.1)

if __name__ == '__main__':
  demo()
