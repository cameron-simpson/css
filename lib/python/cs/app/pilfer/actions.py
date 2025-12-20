#!/usr/bin/env python3

''' Implementations of actions.
'''

from asyncio import to_thread, create_task
from dataclasses import dataclass, field
import re
import shlex
from subprocess import Popen, PIPE
from typing import Any, Callable, List, Mapping, Tuple, Union
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.lex import BaseToken, cutprefix
from cs.logutils import (warning)
from cs.naysync import agen, afunc, async_iter, AnyIterable, StageMode
from cs.pfx import pfx_call
from cs.urlutils import URL

from .parse import get_name_and_args, import_name
from .pilfer import Pilfer, uses_pilfer

@dataclass
class ActionSpecification:
  r'''An action specification parsed from the `[actions]` section of a `pilferrc` file.

      Such a section contains fields of the form:

          action_name = action_argv
          action_name.mode = mode_value

      where:
      * `action_name` is a name containing no dots, specifying the
        argument list for an action
      * `action_argv` is a shell compatible argument list parsed by `shlex.split`
      * `action_name.mode` specifies a value for the mode of `action_name` named `mode`;
        there may be several of these
      The `action_name=action_argv` line may be omitted, or have
      an empty `action_argv`; this configuration is expected to be
      for a builtin action such as `dump`.

      Example:

          [actions]
          untrack = s/\?.*//
          dump.ignore_hosts = analytics.example.com beacon.example.com
  '''
  name: str
  argv: list[str]
  modes: Mapping[str, str]

  def from_actions_section(cls, name: str, section: Mapping[str, str]):
    ''' Make a new `_Action` from the action name and an `[actions]` pilferrc section mapping.
    '''
    argv = shlex.split(section.get(name, '').strip())
    mode_prefix = f'{name}.'
    modes = {
        cutprefix(mode_field, mode_prefix): mode_value.strip()
        for mode_field, mode_value in section.items()
        if mode_field.startswith(mode_prefix)
    }
    return cls(name=name, argv=argv, modes=modes)

@dataclass
class _Action(BaseToken):

  pilfer: Pilfer = None
  modes: Mapping[str, Any] = field(default_factory=dict)
  batchsize: Union[int, None] = None

  @classmethod
  @uses_pilfer
  @typechecked
  def from_str(cls, text, *, P: Pilfer) -> "_Action":
    ''' Convert an action specification into an `_Action`.

        The following specifications are recognised:
        - *name*: filter the input via the named stage function
        - "! shcmd": pipe all the input items through a single Bourne shell command
        - "| shlex": pipe all the input items through a command parsed with shlex.split
        - "/regexp": filter items to those matching regexp
        - "-/regexp": filter items to those not matching regexp
        - "..": treat items as URLs and produce their parent URL

        Named stage functions are converted to `_Action`s which keep
        a reference to the supplied `Pilfer` instance, and the
        `Pilfer` is used when converting the actions into pipeline
        stage functions.
    '''
    action = super().from_str(text)
    action.pilfer = P
    return action

@dataclass(kw_only=True)
class ActionByName(_Action):

  name: str
  args: list = field(default_factory=list)
  kwargs: dict = field(default_factory=dict)

  @classmethod
  def parse(cls, text: str, offset=0) -> Tuple[_Action, int]:
    # dotted_name[:param=,...]
    name, args, kwargs, offset1 = get_name_and_args(text)
    if not name:
      raise SyntaxError(f'no name at {offset=} in {text=}')
    return cls(
        source_text=text,
        offset=offset,
        end_offset=offset1,
        name=name,
        args=args,
        kwargs=kwargs,
    ), offset1

  @property
  def stage_spec(self):
    ''' Produce the stage specification for this action.
    '''
    P = self.pilfer
    action_map = P.action_map
    try:
      return action_map[self.name]
    except KeyError:
      return import_name(self.name)

# TODO: this gathers it all, need to open pipe and stream, how?
@dataclass(kw_only=True)
class ActionSubProcess(_Action):
  ''' A action which passes items through a subprocess
      with `str(item)` on each input line
      and yielding the subprocess output lines
      with their trailing newlines removed.
  '''

  argv: List[str]

  @classmethod
  def parse(cls, text, offset=0):
    if not text.startswith(('!', '|'), offset):
      raise SyntaxError(
          f'expected ! or | at {offset=}, found {text[offset:offset+1]!r}'
      )
    # pipe through shell command or shlex-argv
    cmdtext = text[offset + 1:].strip()
    if not cmdtext:
      raise SyntaxError("empty command")
    if text.startswith('!'):
      # shell command
      argv = ['/bin/sh', '-c', cmdtext]
    elif text.startswith('!'):
      # shlex.split()
      argv = shlex.split(cmdtext)
    else:
      raise RuntimeError('unhandled shcmd/shlex subprocess')
    return cls(
        source_text=text,
        offset=offset,
        end_offset=len(text),
        argv=argv,
    ), len(text)

  @property
  def stage_spec(self):
    ''' Our stage specification streams items through the subprocess.
    '''
    return self.stage_func, StageMode.STREAM

  @typechecked
  async def stage_func(self, item_Ps: AnyIterable):
    r'''Pipe a list of items through a subprocess.

        Send `str(item)` for each item, with sloshes doubled and
        newlines eplaced by `\\n`.  Read lines from the subprocess
        and yield each line without its trailing newline.
    '''

    # this will be filled in with the first Pilfer from the first (item,Pilfer) 2-tuple
    result_P = None

    async def send_items(item_Ps: AnyIterable):
      ''' Send items to the subprocess and then close its stdin.
      '''
      nonlocal result_P

      def send_item(item):
        popen.stdin.write(str(item).replace('\\', r'\\').replace('\n', r'\n'))
        popen.stdin.write('\n')
        popen.stdin.flush()

      async for item, P in async_iter(item_Ps):
        if result_P is None:
          result_P = P
        await to_thread(send_item, item)
      await to_thread(popen.stdin.close)

    @agen
    def read_results():
      ''' Yield lines from the subprocess standard output, newline included.
      '''
      yield from popen.stdout

    # UTF-8 encoded text mode pipe to/from a subprocess
    popen = await to_thread(
        Popen,
        self.argv,
        stdin=PIPE,
        stdout=PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    create_task(send_items(item_Ps))
    # yield lines from the subprocess with their trailing newline removed
    async for result in read_results():
      yield result.rstrip('\n'), result_P
    xit = await to_thread(popen.wait)
    if xit != 0:
      warning("exit %d from subprocess %r", xit, self.argv)

# TODO: this gathers it all, need to open pipe and stream, how?
@dataclass(kw_only=True)
class ActionSelect(_Action):
  ''' This action's `stage_func` yields the input item or not
      depending on the truthiness of `select_func`.

      While the pipelines passes `(item,Pilfer)` 2-tuples,
      the signature of `select_func` depends on `affects_context`.
      If false, the stage function calls `select_func(item)`.
      If true, the stage function calls `select_func(item,Pilfer)`
      and expects back a `(bool,Pilfer)` 2-tuple, where the `Pilfer`
      potentially a new `Pilfer` with modifications applied.
  '''

  select_func: (
      Callable[[Any], bool] | Callable[[Any, Pilfer], Tuple[bool, Pilfer]]
  )
  invert: bool = False
  affects_context: bool = False
  fast: bool = False

  def __post_init__(self):
    af = afunc(self.select_func, fast=self.fast)
    if self.affects_context:

      async def stage_func(item_P):
        ''' Receive the `item_P` 2-tuple, pass as `self.select_func(item,P)`.
        '''
        item, P = item_P
        selected, new_P = await af(item, P)
        if self.invert:
          selected = not selected
        if selected:
          yield item, new_P
    else:

      async def stage_func(item_P):
        ''' Receive the `item_P` 2-tuple, pass as `self.select_func(item)`.
        '''
        item, P = item_P
        selected = await af(item)
        if self.invert:
          selected = not selected
        if selected:
          yield item, P

    self.stage_func = stage_func

  @classmethod
  def parse(cls, text, offset=0):
    text0 = text
    text = text[offset:]
    regexp = cutprefix(text, ('/', '-/'))
    if regexp is text:
      raise SyntaxError(f'expected leading / or -/ at {offset=} of {text0!r}')
    if text.startswith('/'):
      offset = 1
      invert = False
    else:
      assert text.startswith('-/')
      offset = 2
      invert = True
    # optional trailing /
    if text.endswith('/'):
      regexp_s = text[:-1]
    try:
      regexp = pfx_call(re.compile, regexp_s)
    except re.PatternError as re_e:
      raise SyntaxError(f'invalid regexp {regexp_s}: {re_e}') from re_e
    if regexp.groupindex:
      # a regexp with named groups
      @uses_pilfer
      def re_match(item, *, P: Pilfer):
        ''' Match `item` against `regexp`, update the `Pilfer` vars if matched.
        '''
        m = regexp.search(str(item))
        if not m:
          return False
        varmap = m.groupdict()
        if varmap:
          P = P.copy_with_vars(**varmap)
        yield m
    else:

      def re_match(item):
        ''' Just test `item` against `regexp`.
        '''
        return regexp.search(str(item))

    return cls(
        source_text=text0,
        offset=offset,
        end_offset=len(text0),
        select_func=re_match,
        invert=invert,
    ), len(text0)

  @property
  def stage_spec(self):
    ''' Our stage function uses the usual process one item style.
    '''
    return self.stage_func

@dataclass(kw_only=True)
class ActionModify(_Action):

  modify_func: Callable[Tuple[Any, Pilfer], Tuple[Any, Pilfer]]
  fast: bool = False

  def __post_init__(self):
    # make an instance stage_func
    af = afunc(self.modify_func, fast=self.fast)

    async def stage_func(item_P):
      item, P = item_P
      yield await af(item, P)

    self.stage_func = stage_func

  @classmethod
  def parse(cls, text: str, offset: int = 0):
    if text[offset:] == '..':
      return cls(
          source_text=text,
          offset=offset,
          end_offset=len(text),
          modify_func=lambda item, P: (URL.promote(item).parent, P),
          fast=True,
      ), len(text)
    raise SyntaxError('unrecognised {cls.__name__} at {offset=} of {text!r}')

  @property
  def stage_spec(self):
    ''' Our stage function uses the usual process one item style.
    '''
    return self.stage_func
