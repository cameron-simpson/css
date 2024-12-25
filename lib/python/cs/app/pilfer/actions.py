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

from cs.lex import BaseToken, get_identifier, skipwhite
from cs.logutils import (warning)
from cs.naysync import agen, afunc, async_iter, AnyIterable, StageMode
from cs.pfx import Pfx, pfx_call

from .pilfer import Pilfer, uses_pilfer

class Action(BaseToken):

  batchsize: Union[int, None] = None

  @classmethod
  @typechecked
  def parse(cls, text, offset: int = 0, *, skip=True) -> "Action":
    if skip:
      offset = skipwhite(text, offset)
    offset1 = offset
    with Pfx("%s.parse: %d:%r", cls.__name__, offset1, text[offset1:]):

      # pipe:shlex(argv)
      if text.startswith('pipe:', offset1):
        offset += 5
        argv = shlex.split(text[offset:])
        return ActionSubProcess(
            offset=offset1,
            source_text=text,
            end_offset=len(text),
            batchsize=0,
            argv=argv,
        )
      name, offset = get_identifier(text, offset)
      if name:
        # TODO: options parameters?
        # parse.parse_action_args?
        return ActionByName(
            offset=offset1,
            source_text=text,
            end_offset=offset,
            name=name,
        )

      # | shcmd
      if text.startswith('|', offset1):
        # pipe through shell command
        offset = skipwhite(text, offset + 1)
        if offset == len(text):
          raise SyntaxError("empty shell command")
        shcmd = text[offset:]
        return ActionSubProcess(
            offset=offset1,
            source_text=text,
            end_offset=len(text),
            argv=['/bin/sh', '-c', shcmd],
        )

      # /regexp or -/regexp
      if text.startswith(('/', '-/'), offset):
        if text.startswith('/', offset):
          offset += 1
          invert = False
        else:
          assert text.startswith('-/', offset)
          offset += 2
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
            offset=offset1,
            source_text=text,
            end_offset=len(text),
            select_func=re_match,
            invert=invert,
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

  argv: List[str]

  @property
  def stage_spec(self):
    ''' Our stage specification streams items through the subprocess.
    '''
    return self.stage_func, StageMode.STREAM

  @typechecked
  async def stage_func(self, items: AnyIterable):
    ''' Pipe a list of items through a subprocess.

        Send `str(item).replace('\n',r'\n')` for each item.
        Read lines from the subprocess and yield each lines without its trailing newline.
    '''

    async def send_items(items: AnyIterable):
      ''' Send items to the subprocess and then close its stdin.
      '''

      def send_item(item):
        popen.stdin.write(str(item).replace('\n', r'\n').encode('utf-8'))
        popen.stdin.write(b'\n')
        popen.stdin.flush()

      async for item in async_iter(items):
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
      yield result.rstrip(b'\n').decode('utf-8', errors='replace')
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
  async def stage_func(self, item: Any):
    ''' Select `item` based on `self.select_func` and `self.invert`.

        Send `str(item).replace('\n',r'\n')` for each item.
        Read lines from the subprocess and yield each lines without its trailing newline.
    '''
    if await afunc(self.select_func)(item):
      if not self.invert:
        yield item
    elif self.invert:
      yield item
