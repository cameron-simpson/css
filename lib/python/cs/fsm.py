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

  def fsm_event(self, event):
    ''' Transition the FSM from the current state to a new state based on `event`.
        Returns the new state.
    '''
    transitions = self.FSM_TRANSITIONS[self.fsm_state]
    new_state = transitions[event]
    self.fsm_state = new_state
    return new_state

  @property
  def fsm_dot(self):
    ''' A DOT syntax state transition diagram.
    '''
    dot = [f'digraph {type(self).__name__} {{']
    # NB: we _do not_ sort the transition graph because the "dot" programme
    # layout is affected by the order # in which the graph is defined.
    # In this way we execute in the dictionary order, which is
    # insertion order in modern Python, which in turn means that
    # describing the transitions in the natural order in which they
    # occur typically produces a nicer graph diagram.
    for src_state, transitions in type(self).FSM_TRANSITIONS.items():
      for event, dst_state in sorted(transitions.items()):
        dot.append(f'  {src_state}->{dst_state}[label={event}];')
    dot.append('}')
    return '\n'.join(dot)

  def fsm_print(self, output=None, fmt=None, layout=None, **dot_kw):
    ''' Print the state transition diagram to `output`, default `sys.stdout`,
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
