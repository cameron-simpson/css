#!/usr/bin/env python3
#
# Idea spawned from a debugging session at python-discord with Draco and JigglyBalls.
# - Cameron Simpson <cs@cskk.id.au> 14dec2024
#

''' An attempt at comingling async-code and nonasync-code-in-a-thread in an argonomic way.

    One of the difficulties in adapting non-async code for use in
    an async world is that anything asynchronous needs to be turtles
    all the way down: a single blocking sychornous call anywhere
    in the call stack blocks the async event loop.

    This module presently provides a pair of decorators for
    asynchronous generators andfunctions which dispatches them in
    a `Thread` and presents an async wrapper.
'''

import asyncio
from queue import Queue, Empty as QEmpty
from threading import Thread

from cs.deco import decorator

__version__ = '20241214.1'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
    ],
}

@decorator
def agen(genfunc, maxsize=1, poll_delay=0.25, fast_poll_delay=0.001):
  ''' A decorator for a synchronous generator which turns it into
      an asynchronous generator.

      Parameters:
      * `maxsize`: the size of the `Queue` used for communication,
        default `1`; this governs how greedy the generator may be
      * `poll_delay`: the async delay between polls of the `Queue`
        after it was found to be empty twice in succession, default `0.25`s
      * `fast_poll_delay`: the async delay between polls of the
        `Queue` after it was found to be empty the first time after the
        start or after an item was obtained

      Exceptions in the generator are reraised in the synchronous generator.

      Example:

          @agen
          def gen(count):
              for i in range(count):
                  yield i
                  time.sleep(1.0)

          async for item in gen(5):
              print(item)
  '''

  async def agen(*a, **kw):
    ''' An async generator yielding items from `genfunc`.
    '''
    q = Queue(maxsize=maxsize)
    sentinel = object()
    g = genfunc(*a, **kw)

    def rungen():
      ''' Run the generator and put its items onto the queue.
      '''
      try:
        for item in g:
          q.put((item, None))
      except Exception as e:
        q.put((sentinel, e))
      else:
        q.put((sentinel, None))

    T = Thread(target=rungen)
    T.start()
    delay = fast_poll_delay
    while True:
      try:
        item, e = q.get(block=False)
      except QEmpty:
        await asyncio.sleep(delay)
        delay = poll_delay
        continue
      delay = fast_poll_delay
      if item is sentinel:
        if e is not None:
          raise e
        break
      assert e is None
      yield item

  return agen

@decorator
def afunc(func, poll_delay=0.25, fast_poll_delay=0.001):
  ''' A decorator for a synchronous function which turns it into
      an asynchronous function.

      The parameters are the same as for `@agen` excluding `maxsize`,
      as this wraps the function in an asynchronous generator which
      just yields the function result.

      Example:

          @afunc
          def func(count):
              time.sleep(count)
              return count

          slept = await func(5)
  '''

  @agen(poll_delay=poll_delay, fast_poll_delay=fast_poll_delay)
  def genfunc(*a, **kw):
    ''' An asynchronous generator to yield the return result of `func`.
    '''
    yield func(*a, **kw)

  async def afunc(*a, **kw):
    ''' Asynchronous call to `func` via `@agen(fgenfunc)`.
    '''
    async for item in genfunc(*a, **kw):
      return item
    # we should never get here
    raise RuntimeError

  return afunc

if __name__ == '__main__':

  @agen
  def gen():
    yield from range(5)

  async def async_generator_demo():
    async for item in gen():
      print("async_demo", repr(item))

  asyncio.run(async_generator_demo())

  import time

  @afunc
  def async_function_demo(sleep_time, result):
    print("func demo: sleep", sleep_time)
    time.sleep(sleep_time)
    print("func demo: return result", result)
    return result

  asyncio.run(async_function_demo(4.0, 9))
