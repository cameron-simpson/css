#!/usr/bin/python
#
# Single line status updates.
#   - Cameron Simpson <cs@cskk.id.au>

r'''
Single line status updates with minimal update sequences.

This is available as an output mode in `cs.logutils`.

Example:

    with Upd() as U:
        for filename in filenames:
            U.out(filename)
            ... process filename ...
            upd.nl('an informational line')
'''

from __future__ import with_statement
import atexit
from contextlib import contextmanager
from threading import RLock
from cs.gimmicks import warning
from cs.lex import unctrl
from cs.obj import SingletonMixin
from cs.tty import ttysize

try:
  import curses
except ImportError as e:
  warning("cannot import curses: %s", e)
  curses = None

__version__ = '20200229'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.gimmicks', 'cs.lex', 'cs.obj', 'cs.tty'],
}

instances = []

def cleanupAtExit():
  ''' Cleanup function called at programme exit to clear the status line.
  '''
  global instances
  for i in instances:
    i.close()
  instances = ()

atexit.register(cleanupAtExit)

class Upd(SingletonMixin):
  ''' A `SingletonMixin` subclass for maintaining a regularly updated status line.
  '''

  @classmethod
  def _singleton_key(cls, backend, columns=None):
    return id(backend)

  def _singleton_init(self, backend, columns=None):
    assert backend is not None
    if columns is None:
      columns = 80
      if backend.isatty():
        rc = ttysize(backend)
        if rc.columns is not None:
          columns = rc.columns
    self._backend = backend
    self.columns = columns
    self._ti_ready = False
    self._ti_strs = {}
    self._slot_text = ['']
    self._current_slot = 0
    self._above = None
    self._lock = RLock()
    global instances
    instances.append(self)

  def __enter__(self):
    return self

  def __exit__(self, exc_type, *_):
    ''' Tidy up on exiting the context.

        If we are exiting because of an exception and the status
        line is not empty, output a newline to preserve the status
        line on the screen.  Otherwise just clear the status line.
    '''
    if self._slot_text[0]:
      if exc_type:
        self._backend.write('\n')
        self._backend.flush()
      else:
        self.out('')

  def close(self):
    ''' Close this Upd.
    '''
    if self._backend is not None:
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
    try:
      return self._ti_strs[ti_name]
    except KeyError:
      with self._lock:
        if curses is None:
          s = None
        else:
          if not self._ti_ready:
            curses.setupterm()
            self._ti_ready = True
          s = curses.tigetstr(ti_name)
          if s is not None:
            s = s.decode('ascii')
        self._ti_strs[ti_name] = s
      return s

  @staticmethod
  def adjust_text_v(oldtxt, newtxt, columns, raw_text=False):
    ''' Compute the text sequences required to update `oldtxt` to `newtxt`
        presuming the cursor is at the right hand end of `oldtxt`.
        The available area is specified by `columns`.

        We normalise `newtxt` as `unctrl(newtxt.rstrip())`.
        `oldtxt` is presumed to be already normalised.
    '''
    # normalise text
    if not raw_text:
      newtxt = unctrl(newtxt.rstrip())
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

  def move_to_slot_v(self, from_slot, to_slot):
    ''' Compute the text sequences required to move our cursor
        to the end of `to_slot` from `from_slot`.
    '''
    assert from_slot >= 0
    assert to_slot >= 0
    if from_slot is None:
      from_slot = self._current_slot
    movetxts = []
    oldtxt = self._slot_text[to_slot]
    from_slot = self._current_slot
    if to_slot != from_slot:
      # move cursor to target slot
      if to_slot < from_slot:
        # emit VT
        movetxts.append('\v' * (from_slot - to_slot))
      else:
        # emit cursor_up
        cuu1 = self.ti_str('cuu1')
        movetxts.append(cuu1 * (to_slot - from_slot))
      # adjust horizontal position
      vpos_cur = len(self._slot_text[from_slot])
      vpos_slot = len(oldtxt)
      if vpos_cur > vpos_slot:
        # backspace
        movetxts.append('\b' * (vpos_cur - vpos_slot))
      elif vpos_cur < vpos_slot:
        # overwrite to advance cursor
        movetxts.append(oldtxt[vpos_cur:])
    return movetxts

  def redraw_line_v(self, txt):
    ''' Compute the text sequences to redraw the specified slot,
        being `CR`, slot text, clear to end of line.
    '''
    txts = ['\r', txt]
    clr_eol = self.ti_str('clr_eol')
    if clr_eol:
      txts.append(clr_eol)
    else:
      pad_len = self.columns - len(txt) - 1
      if pad_len > 0:
        txts.append(' ' * pad_len)
        txts.append('\b' * pad_len)
    return txts

  def redraw_slot_v(self, slot):
    ''' Compute the text sequences to redraw the specified slot,
        being `CR`, slot text, clear to end of line.

        This presumes the cursor is on the requisite line.
    '''
    return self.redraw_line_v(self._slot_text[slot])

  def redraw_trailing_slots_v(self, upper_slot, skip_first_vt=False):
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
      txts.extend(self.redraw_slot_v(slot))
      first = False
    return txts

  def flush(self):
    ''' Flush the backend stream.
    '''
    backend = self._backend
    if backend is not None:
      backend.flush()

  def out(self, txt, *a, slot=0, raw_text=False, redraw=False):
    ''' Update the status line to `txt`.
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
      txt = unctrl(txt.rstrip())
    backend = self._backend
    with self._lock:
      oldtxt = self._slot_text[slot]
      if oldtxt != txt:
        # move to target slot and collect reference text
        txts = self.move_to_slot_v(self._current_slot, slot)
        if redraw:
          txts.extend(self.redraw_slot_v(slot))
        else:
          txts.extend(
              self.adjust_text_v(oldtxt, txt, self.columns, raw_text=True)
          )
        backend.write(''.join(txts))
        backend.flush()
        self._current_slot = slot
        self._slot_text[slot] = txt
    return txt

  def nl(self, txt, *a, raw=False):
    ''' Write `txt` to the backend followed by a newline.

        Parameters:
        * `txt`: the message to write.
        * `a`: optional positional parameters;
          if not empty, `txt` is percent formatted against this list.
        * `raw`: if true (default `False`) use the "clear, newline,
          restore" method.

        This uses one of two methods:
        * insert above:
          insert a line above the status line and write the message there.
        * clear, newline, restore:
          clears the status line, writes the text line, restores
          the status line.

        The former method is used if the terminal supports the
        `il1` (insert one line) capability;
        this is probed for on the first use and remembered.
    '''
    if a:
      txt = txt % a
    txts = []
    with self._lock:
      if raw or len(txt) >= self.columns:
        # force a clear-newline-restore method
        above = False
      else:
        # see if we have an "insert line above" capability
        above = self._above
        if above is None:
          il1 = self.ti_str('il1')
          if il1:
            above = ((il1 + b'\r').decode(), '\v\r')
          else:
            above = False
          self._above = above
      slots = self._slot_text
      # move to the top slot
      top_slot = len(slots) - 1
      txts.extend(self.move_to_slot_v(self._current_slot, top_slot))
      if above:
        # insert blank line, write `txt`, move down to start of top slot
        txts.append(above[0])  # insert line above
        txts.append(txt)
        txts.append(above[1])  # move back to top line, at start
        txts.append(slots[top_slot])  # advance cursor to end of top slot
      else:
        # overwrite the top line instead,
        txts.extend(self.redraw_line_v(txt))
        # then rewrite all the slots below that
        txts.extend(self.redraw_trailing_slots_v(top_slot))
      self._backend.write(''.join(txts))
      self._backend.flush()
      self._current_slot = 0

  @contextmanager
  def without(self, temp_state='', slot=0):
    ''' Context manager to clear the status line around a suite.
        Returns the status line text as it was outside the suite.

        The `temp_state` parameter may be used to set the inner status line
        content if a value other than `''` is desired.
    '''
    with self._lock:
      old = self.out(temp_state, slot=slot)
      try:
        yield old
      finally:
        self.out(old, slot=slot)

  def insert(self, index, txt=''):
    ''' Insert a new status line at `index`.
        Return the index of the new status line.
    '''
    index0 = index
    with self._lock:
      slots = self._slot_text
      if index < 0:
        index = len(slots) + index
        if index < 0:
          raise IndexError("index %s too low" % (index0,))
      elif index > len(slots):
        raise IndexError("index %s too high" % (index0,))
      assert 0 <= index <= len(slots)
      il1 = self.ti_str('il1')
      if index == 0:
        # move to bottom slot, add line below
        txts = self.move_to_slot_v(self._current_slot, 0)
        txts.append('\v\r')
        if il1:
          txts.append(il1)
        txts.extend(self.redraw_line_v(txt))
        slots.insert(index, txt)
        self._current_slot = 0
      else:
        # move to line to be below the inserted line
        txts = self.move_to_slot_v(self._current_slot, index - 1)
        slots.insert(index, txt)
        if il1:
          txts.append(il1)
          txts.extend(self.redraw_line_v(txt))
          self._current_slot = index
        else:
          txts.extend(self.redraw_trailing_slots_v(index, skip_first_vt=True))
          self._current_slot = 0
      self._backend.write(''.join(txts))
      self._backend.flush()
    return index
