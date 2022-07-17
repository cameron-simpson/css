#!/usr/bin/env python

''' Basic Finite State Machine (FSM) tools.
'''

from collections import defaultdict, namedtuple
from threading import Lock
import time
from typing import Optional, TypeVar

from typeguard import typechecked

from cs.gimmicks import warning
from cs.gvutils import gvprint, quote as gvq
from cs.lex import cutprefix
from cs.pfx import Pfx, pfx_call

FSMSubType = TypeVar('FSMSubType', bound='FSM')

class FSMError(Exception):
  ''' An exception associated with an `FSM`.

      These have a `.fsm` attribute storing an (optional) `FSM`
      reference supplied at initialisation.
  '''

  @typechecked
  def __init__(self, msg: str, fsm: Optional[FSMSubType] = None):
    super().__init__(msg)
    self.fsm = fsm

FSMTransitionEvent = namedtuple(
    'FSMTransitionEvent', 'old_state new_state event when extra'
)

class FSM:
  ''' Base class for a finite state machine (FSM).

      The allowed states and transitions are definted by the class
      attribute `FSM_TRANSITIONS`, a mapping of
      *state*->*event*->*new_state*.

      Each instance has the following attributes:
      * `fsm_state`: the current state value.
      * `fsm_history`: an optional iterable of `FSMTransitionEvent`
        state transitions recorded by the `fsm_event` method.
        Usually this would be `None` (the default) or a `list`.
  '''

  # token representing "any state" in the callbacks
  FSM_ANY_STATE = object()

  # allow state transitions
  FSM_TRANSITIONS = {}

  def __init__(self, state, history=None, lock=None):
    ''' Initialise the `FSM` from:
        * `state`: the initial state
        * `history`: an optional object to record state transition
          history, default `None`; if not `None` this should be an
          iterable object with a `.append(entry)` method such as a
          `list`.
        * `lock`: an optional mutex to control access;
          if presupplied and shared with the caller
          it should probably be an `RLock`;
          the default is a `Lock`, which is enough for `FSM` private use
    '''
    if lock is None:
      lock = Lock()
    if state not in self.FSM_TRANSITIONS:
      raise ValueError(
          "invalid initial state %r, expected one of %r" % (
              state,
              sorted(self.FSM_TRANSITIONS.keys()),
          )
      )
    self.fsm_state = state
    self.fsm_history = history
    self.__lock = lock
    self.__callbacks = defaultdict(list)

  def __getattr__(self, attr):
    ''' Provide the following attributes:
        - present the state names as attributes, for example:
          `self.PENDING=='PENDING'` if there is a `'PENDING'` state
        - present `is_`*statename* as a Boolean testing whether
          `self.fsm_state==`*statename*`.upper()`
        - a callable calling `self.fsm_event(attr)` if `attr`
          is an event name for the current state
        Fall back to the superclass `__getattr__`.
    '''
    if attr in self.FSM_TRANSITIONS:
      return attr
    in_state = cutprefix(attr, 'is_')
    if in_state is not attr:
      # relies on upper case state names
      return self.fsm_state == in_state.upper()
    try:
      statedef = self.FSM_TRANSITIONS[self.fsm_state]
    except KeyError:
      pass
    else:
      if attr in statedef:
        return lambda: self.fsm_event(attr)
    try:
      sga = super().__getattr__
    except AttributeError as e:
      raise AttributeError(
          "no %s.%s attribute" % (type(self).__name__, attr)
      ) from e
    return sga(attr)

  def fsm_event(self, event, **extra):
    ''' Transition the FSM from the current state to a new state based on `event`.
        Call any callbacks associated with the new state.
        Returns the new state.

        Optional information may be passed as keyword arguments.
        If `self.fsm_history` is not `None`
        a new `FSMTransitionEvent` event is appended to `self.fsm_history`
        with the following attributes:
        * `old_state`: the state when `fsm_event` was called
        * `new_state`: the new state
        * `event`: the `event`
        * `when`: a UNIX timestamp from `time.time()`
        * `extra`: a `dict` with the `extra` information
    '''
    with self.__lock:
      old_state = self.fsm_state
      try:
        new_state = self.FSM_TRANSITIONS[old_state][event]
      except KeyError as e:
        raise FSMError(
            f'invalid event {event!r} for state {old_state!r}', self
        ) from e
      self.fsm_state = new_state
      transition = FSMTransitionEvent(
          old_state=old_state,
          new_state=new_state,
          event=event,
          when=time.time(),
          extra=extra,
      )
      if self.fsm_history is not None:
        self.fsm_history.append(transition)
    with Pfx("%s->%s", old_state, new_state):
      for callback in self.__callbacks[self.FSM_ANY_STATE
                                       ] + self.__callbacks[new_state]:
        try:
          pfx_call(callback, self, transition)
        except Exception as e:  # pylint: disable=broad-except
          warning("exception from callback %s: %s", callback, e)
    return new_state

  @property
  def fsm_events(self):
    ''' Return a list of the events valid for the current state.
    '''
    return list(self.FSM_TRANSITIONS[self.fsm_state])

  def fsm_callback(self, state, callback):
    ''' Register a callback for to be called immediately on transition
        to `state` as `callback(self,FSMEventTransition)`.
    '''
    with self.__lock:
      self.__callbacks[state].append(callback)

  def fsm_transitions_as_dot(self, fsm_transitions, sep='\n'):
    ''' Compute a DOT syntax graph description from a transitions dictionary.
    '''
    dot = [f'digraph {gvq(type(self).__name__)} {{']
    # NB: we _do not_ sort the transition graph because the "dot" programme
    # layout is affected by the order in which the graph is defined.
    # In this way we execute in the dictionary order, which is
    # insertion order in modern Python, which in turn means that
    # describing the transitions in the natural order in which they
    # occur typically produces a nicer graph diagram.
    for src_state, transitions in fsm_transitions.items():
      for event, dst_state in sorted(transitions.items()):
        dot.append(
            f'  {gvq(src_state)}->{gvq(dst_state)}[label={gvq(event)}];'
        )
    dot.append('}')
    return sep.join(dot)

  @property
  def fsm_dot(self):
    ''' A DOT syntax description of `self.FSM_TRANSITIONS`.
    '''
    return self.fsm_transitions_as_dot(self.FSM_TRANSITIONS)

  def fsm_print(self, file=None, fmt=None, layout=None, **dot_kw):
    ''' Print the state transition diagram to `file`, default `sys.stdout`,
        in format `fmt` using the engine specified by `layout`, default `'dot'`.
        This is a wrapper for `cs.gvutils.gvprint`.
    '''
    return gvprint(self.fsm_dot, file=file, fmt=fmt, layout=layout, **dot_kw)

if __name__ == '__main__':
  import sys
  from cs.taskqueue import Task
  fsm1 = Task('fsm1', lambda: print("FUNC"))
  print(fsm1.fsm_dot, file=sys.stderr)
  fsm1.fsm_print()
