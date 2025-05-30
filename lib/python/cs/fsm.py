#!/usr/bin/env python

''' Basic Finite State Machine (FSM) tools.
'''

from collections import defaultdict, namedtuple
from itertools import chain
from threading import Lock
import time
from typing import Optional, TypeVar

from typeguard import typechecked

from cs.gimmicks import exception
from cs.gvutils import gvprint, gvsvg, quote as gvq, DOTNodeMixin
from cs.lex import cutprefix, r
from cs.pfx import Pfx, pfx_call
from cs.seq import first

__version__ = '20250120-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.gimmicks',
        'cs.gvutils>=20230816',
        'cs.lex',
        'cs.pfx',
        'cs.seq',
        'typeguard',
    ],
}

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

class CancellationError(FSMError):
  ''' Subclass of `FSMError` Raised when trying to make use of an
      `FSM` which is cancelled.

      For example, this is raised by a `cs.result.Result`
      when accessing `.result` or `.exc_info` after cancellation.
  '''

  def __init__(self, message=None, *, fsm=None, **kw):
    ''' Initialise the `CancellationError`.

        The optional `message` parameter (default `"cancelled"`)
        is set as the `message` attribute.
        Other keyword parameters set their matching attributes.
    '''
    if message is None:
      message = "cancelled"
    elif not isinstance(message, str):
      message = 'cancelled: ' + str(message)
    super().__init__(message, fsm=fsm)
    self.message = message
    for k, v in kw.items():
      setattr(self, k, v)

FSMTransitionEvent = namedtuple(
    'FSMTransitionEvent', 'old_state new_state event when extra'
)

class FSM(DOTNodeMixin):
  ''' Base class for a finite state machine (FSM).

      The allowed states and transitions are defined by the class
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

  # allowed state transitions
  FSM_TRANSITIONS = {}

  def __init__(self, state=None, *, history=None, lock=None, transitions=None):
    ''' Initialise the `FSM` from:
        * `state`: optional _positional_ parameter for the initial state,
          default `self.FSM_DEFAULT_STATE` or the first key from `self.FSM_TRANSITIONS`
        * `history`: an optional object to record state transition
          history, default `None`; if not `None` this should be an
          iterable object with a `.append(entry)` method such as a
          `list`.
        * `lock`: an optional mutex to control access;
          if presupplied and shared with the caller
          it should probably be an `RLock`;
          the default is a `Lock`, which is enough for `FSM` private use
        * `transitions`: optional *state*->*event*->*state* mapping;
          if provided, this will override the class `FSM_TRANSITIONS` mapping

        Note that the `FSM` base class does not provide a
        `FSM_DEFAULT_STATE` attribute; a default `state` value of
        `None` will leave `.fsm_state` _unset_.

        This behaviour is is chosen mostly to support subclasses
        with unusual behaviour, particularly Django's `Model` class
        whose `refresh_from_db` method seems to not refresh fields
        which already exist, and setting `.fsm_state` from a
        `FSM_DEFAULT_STATE` class attribute thus breaks this method.
        Subclasses of this class and `Model` should _not_ provide a
        `FSM_DEFAULT_STATE` attribute, instead relying on the field
        definition to provide this default in the usual way.
    '''
    if state is None:
      try:
        state = self.FSM_DEFAULT_STATE
      except AttributeError:
        state = first(self.FSM_TRANSITIONS.keys())
      else:
        if state is None:  # who _does_ this? I do :-(
          state = first(self.FSM_TRANSITIONS.keys())
    if lock is None:
      lock = Lock()
    if transitions is not None:
      self.FSM_TRANSITIONS = transitions
    if state is not None:
      if state not in self.FSM_TRANSITIONS:
        raise ValueError(
            "invalid initial state %r, expected one of %r" % (
                state,
                sorted(self.FSM_TRANSITIONS.keys()),
            )
        )
      self.fsm_state = state
    self._fsm_history = history
    self.__lock = lock
    self.__callbacks = defaultdict(list)

  def __str__(self):
    return f'{type(self).__name__}:{self.fsm_state}'

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
    if not attr.startswith('_'):
      if attr in ('fsm_state',):
        return None
      if attr in self.FSM_TRANSITIONS:
        return attr
      try:
        state = self.fsm_state
      except AttributeError:
        pass
      else:
        in_state = cutprefix(attr, 'is_')
        if in_state is not attr:
          # relies on upper case state names
          return state == in_state.upper()
        FSM_TRANSITIONS = self.FSM_TRANSITIONS
        all_transitions = set(
            chain(
                *(
                    per_state_transition.keys()
                    for per_state_transition in FSM_TRANSITIONS.values()
                )
            )
        )
        if attr in all_transitions:
          # only look up known transitions
          try:
            statedef = FSM_TRANSITIONS[state]
          except KeyError as ke:
            raise AttributeError(
                f'FSM.{attr}: no FSM_TRANSITIONS for state {state!r}'
            ) from ke
          if attr in statedef:
            return lambda **kw: self.fsm_event(attr, **kw)
          raise AttributeError(
              f'FSM.{attr}: no such transition for state {state!r}: {statedef!r}'
          )
    sup = super()
    try:
      sga = sup.__getattr__
    except AttributeError as e:
      raise AttributeError(
          "no %s.%s attribute (no %s.__getattr__: %s)" %
          (self.__class__.__name__, attr, sup, e)
      ) from e
    return sga(attr)

  @property
  def fsm_history(self):
    ''' History property wrapping private attribute.
        This aids subclassing where the history is not a local attribute.
    '''
    return self._fsm_history

  def fsm_event_is_allowed(self, event):
    ''' Test whether `event` is permitted in the current state.
        This can be handy as a pretest.
    '''
    return event in self.FSM_TRANSITIONS[self.fsm_state]

  def fsm_event(self, event, **extra):
    ''' Transition the FSM from the current state to a new state based on `event`.
        Call any callbacks associated with the new state.
        Returns the new state.

        Optional information may be passed as keyword arguments.

        A `transition` instance of `FSMTransitionEvent` is created
        with the following attributes:
        * `old_state`: the state when `fsm_event` was called
        * `new_state`: the new state
        * `event`: the `event`
        * `when`: a UNIX timestamp from `time.time()`
        * `extra`: a `dict` with the `extra` information

        If `self.fsm_history` is not `None`,
        `transition` is appended to it.

        If there are callbacks for `new_state` or `FSM.FSM_ANY_STATE`,
        call each callback as `callback(self,transition)`.

        *Important note*: the callbacks are run in series in the
        current `Thread`.  If you need to dispatch a long running
        activity from a state transtion, the callback should still
        return promptly.
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
    with Pfx(
        "fsm_event: run callbacks %s->%s->%s",
        old_state,
        event,
        new_state,
    ):
      for callback in (self.__callbacks[FSM.FSM_ANY_STATE] +
                       self.__callbacks[new_state]):
        try:
          pfx_call(callback, self, transition)
        except CancellationError:
          # ignore cancelled callbacks, eg an FSM instance in cancelled state
          pass
        except Exception as e:  # pylint: disable=broad-except
          exception("exception from callback %s: %s", callback, e)
    return new_state

  @property
  def fsm_events(self):
    ''' Return a list of the events valid for the current state.
    '''
    return list(self.FSM_TRANSITIONS[self.fsm_state])

  def fsm_callback(self, state, callback):
    ''' Register a callback to be called immediately on transition
        to `state` as `callback(self,FSMEventTransition)`.
        The special `state` value `FSM.FSM_ANY_STATE` may be supplied
        to register a callback which fires for every state transition.

            >>> fsm = FSM('state1',transitions={
            ...   'state1':{'ev_a':'state2'},
            ...   'state2':{'ev_b':'state1'},
            ... })
            >>> fsm.fsm_callback('state2',lambda task, transition: print(task, transition))
            >>> fsm.fsm_callback(FSM.FSM_ANY_STATE,lambda task, transition: print("ANY", task, transition))
            >>> fsm.ev_a(foo=3) # doctest: +ELLIPSIS
            ANY FSM:state2 FSMTransitionEvent(old_state='state1', new_state='state2', event='ev_a', when=..., extra={'foo': 3})
            FSM:state2 FSMTransitionEvent(old_state='state1', new_state='state2', event='ev_a', when=..., extra={'foo': 3})
            'state2'
            >>> fsm.ev_b(foo=4) # doctest: +ELLIPSIS
            ANY FSM:state1 FSMTransitionEvent(old_state='state2', new_state='state1', event='ev_b', when=..., extra={'foo': 4})
            'state1'
    '''
    if state is not self.FSM_ANY_STATE and state not in self.FSM_TRANSITIONS:
      raise ValueError(
          "%s.fsm_callback: state %s not in self.FSM_TRANSITIONS %r" %
          (self, r(state), sorted(self.FSM_TRANSITIONS))
      )
    with self.__lock:
      self.__callbacks[state].append(callback)

  def fsm_callback_discard(self, state, callback):
    ''' Deregister a callback for `state`.
    '''
    with self.__lock:
      self.__callbacks[state] = [
          cb for cb in self.__callbacks[state] if cb != callback
      ]

  def fsm_transitions_as_dot(self, transitions=None, **diagram_kw) -> str:
    r'''Compute a DOT syntax graph description of the state diagram.

        Parameters:
        * `transitions`: optional mapping of *state*->*event*->*state*,
          default `self.FSM_TRANSITIONS`
        * `sep`: optional separator between "lines", default `'\n'`
        * `graph_name`: optional name for the graph, default the class name
        * `history_style`: optional style mapping for event transition history,
          used to style edges which have been traversed
    '''
    return self.fsm_state_diagram_as_dot(
        transitions,
        state=self.fsm_state,
        history=self.fsm_history,
        **diagram_kw,
    )

  @classmethod
  def fsm_state_diagram_as_dot(
      cls,
      transitions=None,
      *,
      sep='\n',
      state=None,
      graph_name=None,
      history=None,
      history_style=None,
  ) -> str:
    r'''Compute a DOT syntax graph description of the state diagram.

        Parameters:
        * `transitions`: optional mapping of *state*->*event*->*state*,
          default `cls.FSM_TRANSITIONS`
        * `state`: optional current state name, a key of the transitions
        * `sep`: optional separator between "lines", default `'\n'`
        * `graph_name`: optional name for the graph, default the class name
        * `history`: optional event transition history
        * `history_style`: optional style mapping for event transition history,
          used to style edges which have been traversed
    '''
    if transitions is None:
      transitions = cls.FSM_TRANSITIONS
    if graph_name is None:
      graph_name = cls.__name__
    traversed_edges = defaultdict(list)
    if history_style and history:
      # fill in the mapping of (old,event,new) -> count
      for transition in history:
        # particular types of transitions
        traversed_edges[transition.old_state, transition.event,
                        transition.new_state].append(transition)
        # any type of transition
        traversed_edges[transition.old_state,
                        transition.new_state].append(transition)
    dot = [f'digraph {gvq(graph_name)} {{']
    # NB: we _do not_ sort the transition graph because the "dot" programme
    # layout is affected by the order in which the graph is defined.
    # In this way we execute in the dictionary order, which is
    # insertion order in modern Python, which in turn means that
    # describing the transitions in the natural order in which they
    # occur typically produces a nicer graph diagram.
    for src_state, state_transitions in transitions.items():
      if state is not None and src_state == state:
        # colour the current state
        fillcolor = cls.DOT_NODE_FILLCOLOR_PALETTE.get(src_state)
        if fillcolor:
          attrs_s = cls.dot_node_attrs_str(
              dict(style='filled', fillcolor=fillcolor)
          )
          dot.append(f'  {gvq(src_state)}[{attrs_s}];')
      for event, dst_state in sorted(state_transitions.items()):
        edge_style = dict(label=event)
        if history_style and (src_state, dst_state) in traversed_edges:
          edge_style.update(history_style)
        edge_style_dot = ",".join(
            f'{gvq(k)}={gvq(str(v))}' for k, v in edge_style.items()
        )
        dot.append(f'  {gvq(src_state)}->{gvq(dst_state)}[{edge_style_dot}];')
    dot.append('}')
    return sep.join(dot)

  @classmethod
  def fsm_print_state_diagram(
      cls,
      file=None,
      *,
      fmt=None,
      graph_name=None,
      history=None,
      history_style=None,
      state=None,
      transitions=None,
      **gvprint_kw,
  ):
    ''' Print the state diagram via `cs.gvutils.gvprint`.

        The DOT syntax graph description is computed with
        `FSM.fsm_state_diagram_as_dot` and the `graph_name`,
        `history`, `history_style`, `state` and `transitions`
        parameters are passed through to this.

        If `fmt` is specified as `dot` then the DOT and any remaining
        keyword arguments are passed to `print()`.

        Otherwise any remaining keyword paramaeters are passed to `gvprint`.
    '''
    dot_s = cls.fsm_state_diagram_as_dot(
        transitions,
        state=state,
        graph_name=graph_name,
        history=history,
        history_style=history_style,
    )
    if fmt == 'dot':
      return print(dot_s, **gvprint_kw)
    return gvprint(dot_s, file=file, fmt=fmt, **gvprint_kw)

  @property
  def fsm_dot(self) -> str:
    ''' A DOT syntax description of the state diagram in the current state.
    '''
    return self.fsm_transitions_as_dot()

  @property
  def dot_node_palette_key(self):
    ''' Default palette index is `self.fsm_state`,
        overriding `DOTNodeMixin.dot_node_palette_key`.
    '''
    return self.fsm_state

  def fsm_print(self, file=None, fmt=None, layout=None, **dot_kw):
    ''' Print the state transition diagram to `file`, default `sys.stdout`,
        in format `fmt` using the engine specified by `layout`, default `'dot'`.
        This is a wrapper for `cs.gvutils.gvprint`.
    '''
    return gvprint(self.fsm_dot, file=file, fmt=fmt, layout=layout, **dot_kw)

  def fsm_as_svg(self, layout=None, history_style=None, **dot_kw) -> str:
    ''' Render the state transition diagram as SVG. '''
    return gvsvg(
        self.fsm_transitions_as_dot(history_style=history_style),
        layout=layout,
        **dot_kw,
    )

  @property
  def fsm_svg(self) -> str:
    ''' The state transition diagram as SVG. '''
    return self.fsm_as_svg()

if __name__ == '__main__':
  import sys
  from cs.taskqueue import Task
  fsm1 = Task('fsm1', lambda: print("FUNC"))
  print(fsm1.fsm_dot, file=sys.stderr)
  fsm1.fsm_print()
