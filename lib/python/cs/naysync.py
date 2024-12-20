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

from asyncio import create_task, run, to_thread, Queue as AQueue
from heapq import heappush, heappop
from inspect import iscoroutinefunction
from typing import Any, Callable, Iterable

from cs.deco import decorator

__version__ = '20241220'

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

async def async_iter(it: Iterable):
  ''' Return an asynchronous iterator yielding items from the iterable `it`.
  '''
  it = iter(it)
  sentinel = object()

  def gen():
    while True:
      try:
        yield next(it)
      except StopIteration:
        break
    yield sentinel

  next_it = lambda: next(gen())
  while True:
    item = await to_thread(next_it)
    if item is sentinel:
      break
    yield item

async def amap(
    func: Callable[[Any], Any],
    it: Iterable,
    concurrent=False,
    unordered=False,
    indexed=False,
):
  ''' An asynchronous generator yielding the results of `func(item)`
      for each `item` in the asynchronous iterable `it`.

      If `concurrent` is `False` (the default), run each `func(item)`
      call in series.

      If `concurrent` is true run the function calls as `asyncio`
      tasks concurrently.
      If `unordered` is true (default `False`) yield results as
      they arrive, otherwise yield results in the order of the items
      in `it`, but as they arrive - tasks still evaluate concurrently
      if `concurrent` is true.

      If `indexed` is true (default `False`) yield 2-tuples of
      `(i,result)` instead of just `result`, where `i` is the index
      if each item from `it` counting from `0`.

      Example of an async function to fetch URLs in parallel.

          async def get_urls(urls : List[str]):
              """ Fetch `urls` in parallel.
                  Yield `(url,response)` 2-tuples.
              """
              async for i, response in amap(
                  requests.get, urls,
                  concurrent=True, unordered=True, indexed=True,
              ):
                  yield urls[i], response
  '''
  # promote a synchronous function to an asynchronous function
  if not iscoroutinefunction(func):
    func = afunc(func)
  # promote it to an asynchronous iterator
  ait = async_iter(it)
  if not concurrent:
    # call func serially
    i = 0
    async for item in ait:
      result = await func(item)
      yield (i, result) if indexed else result
      i += 1
    return
  # concurrent operation
  # dispatch calls to func() as tasks
  # yield results from an asyncio.Queue

  # run func(item) and yield its sequence number and result
  # this allows us to yield in order from a heap
  async def qfunc(i, item):
    await Q.put((i, await func(item)))

  # queue all the tasks with their sequence numbers
  Q = AQueue()
  queued = 0
  # Does this also need to be an async function in case there's
  # some capacity limitation on the event loop? I hope not.
  async for item in ait:
    create_task(qfunc(queued, item))
    queued += 1
  if unordered:
    # yield results as they come in
    for _ in range(queued):
      i, result = await Q.get()
      yield (i, result) if indexed else result
  else:
    # gather results in a heap
    # yield results as their number arrives
    results = []
    unqueued = 0
    for _ in range(queued):
      heappush(results, await Q.get())
      while results and results[0][0] == unqueued:
        i, result = heappop(results)
        yield (i, result) if indexed else result
        unqueued += 1
    # this should have cleared the heap
    assert len(results) == 0

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

  ##run(async_function_demo(4.0, 9))

  print("amap...")
  import random

  def func(sleep_time):
    ##print('func sleep_time', sleep_time, 'start')
    time.sleep(sleep_time)
    ##print('func sleep_time', sleep_time, 'done')
    return f'slept {sleep_time}'

  async def test_amap():
    for concurrent in False, True:
      for unordered in False, True:
        for indexed in False, True:
          print(
              "concurrent",
              concurrent,
              "unordered",
              unordered,
              "indexed",
              indexed,
          )
          async for result in amap(
              func,
              [random.randint(1, 10) / 10 for _ in range(5)],
              concurrent=concurrent,
              unordered=unordered,
              indexed=indexed,
          ):
            print(" ", result)

  run(test_amap())
