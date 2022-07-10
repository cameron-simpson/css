#!/usr/bin/env python

''' Basic Finite State Machine (FSM) tools.
'''

from typing import Optional, TypeVar

from typeguard import typechecked
from cs.gvutils import gvprint

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

class FSM:
  ''' Base class for a finite state manchine (FSM).
  '''

  # allow state transitions
  FSM_TRANSITIONS = {}

  def __init__(self, state):
    if state not in self.FSM_TRANSITIONS:
      raise ValueError(
          "invalid initial state %r, expected one of %r" % (
              state,
              sorted(self.FSM_TRANSITIONS.keys()),
          )
      )
    self.fsm_state = state

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
    return super().__getattr__(attr)  # pylint: disable=no-member

  def fsm_event(self, event):
    ''' Transition the FSM from the current state to a new state based on `event`.
        Returns the new state.
    '''
    old_state = self.fsm_state
    try:
      new_state = self.FSM_TRANSITIONS[old_state][event]
    except KeyError as e:
      raise FSMError(
          f'invalid event {event!r} for state {old_state!r}', self
      ) from e
    self.fsm_state = new_state
    return new_state

  @property

  def fsm_transitions_as_dot(self, fsm_transitions, sep='\n'):
    ''' Compute a DOT syntax graph description from a transitions dictionary.
    '''
    dot = [f'digraph {type(self).__name__} {{']
    # NB: we _do not_ sort the transition graph because the "dot" programme
    # layout is affected by the order in which the graph is defined.
    # In this way we execute in the dictionary order, which is
    # insertion order in modern Python, which in turn means that
    # describing the transitions in the natural order in which they
    # occur typically produces a nicer graph diagram.
    for src_state, transitions in fsm_transitions.items():
      for event, dst_state in sorted(transitions.items()):
        dot.append(f'  {src_state}->{dst_state}[label={event}];')
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
    '''
    return gvprint(
        self.fsm_dot, output=output, fmt=fmt, layout=layout, **dot_kw
    )

if __name__ == '__main__':

  from cs.taskqueue import Task
  fsm1 = Task(func=lambda: print("FUNC"))
  print(fsm1.fsm_dot)
  fsm1.fsm_print()
