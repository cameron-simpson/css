#!/usr/bin/env python3
#
# Idea spawned from a debugging session at python-discord with Draco and JigglyBalls.
# - Cameron Simpson <cs@cskk.id.au> 14dec2024
#

''' An attempt at comingling async-code and nonasync-code-in-a-thread in an argonomic way.

    One of the difficulties in adapting non-async code for use in
    an async world is that anything asynchronous needs to be turtles
    all the way down: a single blocking synchronous call anywhere
    in the call stack blocks the async event loop.

    This module presently provides a pair of decorators for
    asynchronous generators and functions which dispatches them in
    a `Thread` and presents an async wrapper.
'''

from asyncio import run, to_thread

from cs.deco import decorator

__version__ = '20241215-post'

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
def agen(genfunc):
  ''' A decorator for a synchronous generator which turns it into
      an asynchronous generator.
      Exceptions in the synchronous generator are reraised in the asynchronous
      generator.

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
    sentinel = object()

    def rungen():
      for item in genfunc(*a, **kw):
        yield item
      yield sentinel

    g = rungen()
    next_g = lambda: next(g)
    while True:
      item = await to_thread(next_g)
      if item is sentinel:
        break
      yield item

  return agen

@decorator
def afunc(func):
  ''' A decorator for a synchronous function which turns it into
      an asynchronous function.

      Example:

          @afunc
          def func(count):
              time.sleep(count)
              return count

          slept = await func(5)
  '''

  async def afunc(*a, **kw):
    ''' Asynchronous call to `func` via `@agen(fgenfunc)`.
    '''
    return await to_thread(func, *a, **kw)

  return afunc

if __name__ == '__main__':

  @agen
  def gen():
    yield from range(5)

  async def async_generator_demo():
    async for item in gen():
      print("async_demo", repr(item))

  run(async_generator_demo())

  import time

  @afunc
  def async_function_demo(sleep_time, result):
    print("func demo: sleep", sleep_time)
    time.sleep(sleep_time)
    print("func demo: return result", result)
    return result

  run(async_function_demo(4.0, 9))
