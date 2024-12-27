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
            modify_func=lambda item: URL.promote(item).parent,
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
    ''' Pipe a list of items through a subprocess.

        Send `str(item).replace('\n',r'\n')` for each item.
        Read lines from the subprocess and yield each lines without its trailing newline.
    '''

    # this will be filled in with the first Pilfer from the first (item,Pilfer) 2-tuple
    result_P = None

    async def send_items(item_Ps: AnyIterable):
      ''' Send items to the subprocess and then close its stdin.
      '''

      def send_item(item):
        popen.stdin.write(str(item).replace('\n', r'\n').encode('utf-8'))
        popen.stdin.write(b'\n')
        popen.stdin.flush()

      async for item, P in async_iter(items):
        if result_P is None:
          result_P = P
        await to_thread(send_item, item)
      await to_thread(popen.stdin.close)

    @agen
    def read_results():
      ''' Yield lines from the subprocess standard output, newline included.
      '''
      yield from popen.stdout

    popen = await to_thread(Popen, self.argv, stdin=PIPE, stdout=PIPE)
    create_task(send_items(items))
    # yield lines from the subprocess with their trailing newline removed
    async for result in read_results():
      yield result.rstrip(b'\n').decode('utf-8', errors='replace'), result_P
    xit = await to_thread(popen.wait)
    if xit != 0:
      warning("exit %d from subprocess %r", xit, self.argv)

# TODO: this gathers it all, need to open pipe and stream, how?
@dataclass
class ActionSelect(Action):

  select_func: Callable[Any, bool]
  invert: bool = False

  @property
  def stage_spec(self):
    ''' Our stage specification streams items through the subprocess.
    '''
    return self.stage_func

  @typechecked
  async def stage_func(self, item_P: Any):
    ''' Select `item` based on `self.select_func` and `self.invert`.

        Send `str(item).replace('\n',r'\n')` for each item.
        Read lines from the subprocess and yield each lines without its trailing newline.
    '''
    item, P = item_P
    P = P.copy()
    with P:
      if await afunc(self.select_func)(item):
        if not self.invert:
          yield item, P
      elif self.invert:
        yield item, P

@dataclass
class ActionModify(Action):

  modify_func: Callable
  fast: bool = None

  def __post_init__(self):
    # make an instance stage_func
    func = lambda item_P: (self.modify_func(item_P[0]), item_P[1])
    self.stage_func = afunc(func, fast=self.fast)

  @property
  def stage_spec(self):
    ''' Our stage specification streams items through the subprocess.
    '''
    return self.stage_func
