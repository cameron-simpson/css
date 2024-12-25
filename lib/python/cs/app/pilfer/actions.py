#!/usr/bin/env python3

''' Implementations of actions.
'''

from asyncio import to_thread, create_task
from dataclasses import dataclass
import errno
import os
import os.path
import shlex
from subprocess import Popen, PIPE
from threading import Thread
from time import sleep
from typing import Any, Iterable, List, Tuple, Union
from urllib.parse import unquote
from urllib.error import HTTPError, URLError
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.deco import promote
from cs.fileutils import mkdirn
from cs.later import RetryError
from cs.lex import BaseToken, get_identifier, skipwhite
from cs.logutils import (debug, error, warning, exception)
from cs.naysync import agen, afunc, async_iter, AnyIterable, StageMode
from cs.pfx import Pfx
from cs.pipeline import StageType
from cs.py.func import funcname
from cs.resources import MultiOpenMixin
from cs.urlutils import URL

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
        return ActionByName(
            offset=offset1,
            source_text=text,
            end_offset=offset,
            name=name,
        )
      raise SyntaxError(f'no action recognised')

@dataclass
class ActionByName(Action):

  name: str

  @property
  @uses_pilfer
  @trace
  def stage_spec(self, P: Pilfer):
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

    popen = await trace(to_thread)(Popen, self.argv, stdin=PIPE, stdout=PIPE)
    create_task(send_items(items))
    # yield lines from the subprocess with their trailing newline removed
    async for result in read_results():
      yield result.rstrip(b'\n').decode('utf-8', errors='replace')
    xit = await to_thread(popen.wait)
    if xit != 0:
      warning("exit %d from subprocess %r", xit, self.argv)

class ShellProcCommand(MultiOpenMixin):
  ''' An iterable queue-like interface to a shell command subprocess.
  '''

  def __init__(self, shcmd, outQ):
    ''' Set up a subprocess running `shcmd`.
        `discard`: discard .put items, close subprocess stdin immediately after startup.
    '''
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
    self.shproc.stdin.close()

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new Pilfers onto the outQ.
      '''
      for line in fp:
        if not line.endswith('\n'):
          raise ValueError('premature EOF (missing newline): %r' % (line,))
        outQ.put(P.copy_with_vars(_=line[:-1]))
      outQ.close()

    self.copier = Thread(
        name="%s.copy_out" % (self,),
        target=copy_out,
        args=(self.shproc.stdout, self.outQ)
    ).start()

  def put(self, P):
    with self._lock:
      if self.shproc is None:
        self._startproc()
    self.shproc.stdin.write(P._)
    self.shproc.stdin.write('\n')
    if not self.no_flush:
      self.shproc.stdin.flush()

  def shutdown(self):
    if self.shproc is None:
      self.outQ.close()
    else:
      self.shproc.wait()
      xit = self.shproc.returncode
      if xit != 0:
        error("exit %d from: %r", xit, self.shcmd)

def action_shcmd(shcmd):
  ''' Return (function, func_sig) for a shell command.
  '''
  shcmd = shcmd.strip()

  @typechecked
  def function(P) -> Iterable[str]:
    U = P._
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, U)
    except KeyError as e:
      warning("shcmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        with open('/dev/null') as fp0:
          fd0 = fp0.fileno()
          try:
            # TODO: use cs.psutils.run
            subp = Popen(
                ['/bin/sh', '-c', 'sh -uex; ' + v],
                stdin=fd0,
                stdout=PIPE,
                close_fds=True
            )
          except Exception as e:
            exception("Popen: %r", e)
            return
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, StageType.ONE_TO_MANY

def action_pipecmd(shcmd):
  ''' Return (function, func_sig) for pipeline through a shell command.
  '''
  shcmd = shcmd.strip()

  @typechecked
  def function(items) -> Iterable[str]:
    if not isinstance(items, list):
      items = list(items)
    if not items:
      return
    P = items[0]
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, P._)
    except KeyError as e:
      warning("pipecmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        # spawn the shell command
        try:
          subp = Popen(
              ['/bin/sh', '-c', 'sh -uex; ' + v],
              stdin=PIPE,
              stdout=PIPE,
              close_fds=True
          )
        except Exception as e:
          exception("Popen: %r", e)
          return

        # spawn a daemon thread to feed items to the pipe
        def feedin():
          for P in items:
            print(P._, file=subp.stdin)
          subp.stdin.close()

        T = Thread(target=feedin, name='feedin to %r' % (v,))
        T.daemon = True
        T.start()
        # read lines from the pipe, trim trailing newlines and yield
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, StageType.MANY_TO_MANY

def new_dir(dirpath):
  ''' Create the directory `dirpath` or `dirpath-n` if `dirpath` exists.
      Return the path of the directory created.
  '''
  try:
    os.makedirs(dirpath)
  except OSError as e:
    if e.errno != errno.EEXIST:
      exception("os.makedirs(%r): %s", dirpath, e)
      raise
    dirpath = mkdirn(dirpath, '-')
  return dirpath

def url_delay(U, delay, *a):
  sleep(float(delay))
  return U

@promote
def url_query(U: URL, *a):
  if not a:
    return U.query
  qsmap = dict(
      [
          (qsp.split('=', 1) if '=' in qsp else (qsp, ''))
          for qsp in U.query.split('&')
      ]
  )
  return ','.join([unquote(qsmap.get(qparam, '')) for qparam in a])

def url_io(func, onerror, *a, **kw):
  ''' Call `func` and return its result.
      If it raises URLError or HTTPError, report the error and return `onerror`.
  '''
  debug("url_io(%s, %s, %s, %s)...", func, onerror, a, kw)
  try:
    return func(*a, **kw)
  except (URLError, HTTPError) as e:
    warning("%s", e)
    return onerror

def retriable(func):
  ''' A decorator for a function to probe the `Pilfer` flags
      and raise `RetryError` if unsatisfied.
  '''

  def retry_func(P, *a, **kw):
    ''' Call `func` after testing `P.test_flags()`.
    '''
    if not P.test_flags():
      raise RetryError('flag conjunction fails: %s' % (' '.join(P.flagnames)))
    return func(P, *a, **kw)

  retry_func.__name__ = 'retriable(%s)' % (funcname(func),)
  return retry_func
