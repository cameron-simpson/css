#!/usr/bin/env python3

''' Implementations of actions.
'''

from asyncio import to_thread, create_task
from dataclasses import dataclass
import re
import shlex
from subprocess import Popen, PIPE
from typing import Any, Callable, List, Union
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.lex import BaseToken, is_identifier, skipwhite
from cs.logutils import (warning)
from cs.naysync import agen, afunc, async_iter, AnyIterable, StageMode
from cs.pfx import Pfx, pfx_call
from cs.urlutils import URL

from .pilfer import Pilfer, uses_pilfer

class Action(BaseToken):

  batchsize: Union[int, None] = None

  @classmethod
  @typechecked
  def from_str(cls, text) -> "Action":
    with Pfx("%s.from_str(%r)", cls.__name__, text):

      # pipe:shlex(argv)
      if text.startswith('pipe:'):
        argv = shlex.split(text[5:])
        return ActionSubProcess(
            offset=0,
            source_text=text,
            end_offset=len(text),
            batchsize=0,
            argv=argv,
        )
      if is_identifier(text):
        # TODO: options parameters?
        # parse.parse_action_args?
        return ActionByName(
            offset=0,
            source_text=text,
            end_offset=len(text),
            name=text,
        )

      # | shcmd
      if text.startswith('|'):
        # pipe through shell command
        shcmd = text[1:].strip()
        if not shcmd:
          raise SyntaxError("empty shell command")
        return ActionSubProcess(
            offset=0,
            source_text=text,
            end_offset=len(text),
            argv=['/bin/sh', '-c', shcmd],
        )

      # /regexp or -/regexp
      if text.startswith(('/', '-/')):
        if text.startswith('/'):
          offset = 1
          invert = False
        else:
          assert text.startswith('-/', offset)
          offset = 2
          invert = True
        if text.endswith('/'):
          regexp_s = text[offset:-1]
        else:
          regexp_s = text[offset:]
        regexp = pfx_call(re.compile, regexp_s)
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

        return ActionSelect(
            offset=0,
            source_text=text,
            end_offset=len(text),
            select_func=re_match,
            invert=invert,
        )

      if text == '..':
        return ActionModify(
            offset=0,
            source_text=text,
            end_offset=len(text),
            modify_func=lambda item, P: (URL.promote(item).parent, P),
            fast=True,
        )

      raise SyntaxError('no action recognised')

@dataclass
class ActionByName(Action):

  name: str

  @property
  @uses_pilfer
  def stage_spec(self, *, P: Pilfer):
    return P.action_map[self.name]

# TODO: this gathers it all, need to open pipe and stream, how?
@dataclass
class ActionSubProcess(Action):
  ''' A action which passes items through a subprocess
      with `str(item)` on each input line
      and yielding the subprocess output lines
      with their trailing newlines removed.
  '''

  argv: List[str]

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
@dataclass
class ActionSelect(Action):
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

  @property
  def stage_spec(self):
    ''' Our stage function uses the usual process one item style.
    '''
    return self.stage_func

@dataclass
class ActionModify(Action):

  modify_func: Callable[Tuple[Any, Pilfer], Tuple[Any, Pilfer]]
  fast: bool = False

  def __post_init__(self):
    # make an instance stage_func
    af = afunc(self.modify_func, fast=self.fast)

    async def stage_func(item_P):
      item, P = item_P
      yield await af(item, P)

    self.stage_func = stage_func

  @property
  def stage_spec(self):
    ''' Our stage function uses the usual process one item style.
    '''
    return self.stage_func
