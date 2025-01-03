#!/usr/bin/env python3
#
# Idea spawned from a debugging session at python-discord with Draco and JigglyBalls.
# - Cameron Simpson <cs@cskk.id.au> 14dec2024
#

''' An attempt at comingling async-code and nonasync-code in an ergonomic way.

    One of the difficulties in adapting non-async code for use in
    an async world is that anything asynchronous needs to be turtles
    all the way down: a single blocking synchronous call anywhere
    in the call stack blocks the async event loop.

    This module presently provides:
    - `@afunc`: a decorator to make a synchronous function asynchronous
    - `@agen`: a decorator to make a synchronous generator asynchronous
    - `amap(func,iterable)`: asynchronous mapping of `func` over an iterable
    - `aqget(q)`: asynchronous function to get an item from a `queue.Queue` or similar
    - `aqiter(q)`: asynchronous generator to yield items from a `queue.Queue` or similar
    - `async_iter(iterable)`: return an asynchronous iterator of an iterable
    - `IterableAsyncQueue`: an iterable flavour of `asyncio.Queue` with no `get` methods
    - `AsyncPipeLine`: a pipeline of functions connected together with `IterableAsyncQueue`s
'''

from asyncio import create_task, run, to_thread, Queue as AQueue, Task
from dataclasses import dataclass
from enum import auto, StrEnum
from functools import partial
from heapq import heappush, heappop
from inspect import (
    isasyncgenfunction,
    iscoroutinefunction,
    isgeneratorfunction,
)
from queue import Queue, Empty
from typing import (
    Any,
    AsyncIterable,
    Callable,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

from cs.deco import decorator
from cs.semantics import ClosedError, not_closed

__version__ = '20250103'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.semantics',
    ],
}

AnyIterable = Union[Iterable, AsyncIterable]

@decorator
def agen(genfunc):
  ''' A decorator for a synchronous generator which turns it into
      an asynchronous generator.
      If `genfunc` already an asynchronous generator it is returned unchanged.
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

  if isasyncgenfunction(genfunc):
    return genfunc

  def agen(*a, **kw):
    ''' Return an async iterator yielding items from `genfunc`.
    '''
    return async_iter(genfunc(*a, **kw))

  return agen

@decorator
def afunc(func, fast=False):
  ''' A decorator for a synchronous function which turns it into
      an asynchronous function.
      If `func` is already an asynchronous function it is returned unchanged.
      If `fast` is true (default `False`) then `func` is presumed to consume
      negligible time and it is simply wrapped in an asynchronous function.
      Otherwise it is wrapped in `asyncio.to_thread`.

      Example:

          @afunc
          def func(count):
              time.sleep(count)
              return count

          slept = await func(5)

          @afunc(fast=True)
          def asqrt(n):
              return math.sqrt(n)
  '''
  if iscoroutinefunction(func):
    return func
  if fast:

    async def fast_func(*a, **kw):
      return func(*a, **kw)

    return fast_func
  return partial(to_thread, func)

async def async_iter(it: AnyIterable, fast=None):
  ''' Return an asynchronous iterator yielding items from the iterable `it`.
      An asynchronous iterable returns `aiter(it)` directly.

      If `fast` is true then `it` is iterated directly instead of
      via a distinct async generator. If not specified, `fast` is
      set to `True` if `it` is a `list` or `tuple` or `set`. A true
      value for this parameter indicates that fetching the next
      item from `it` is always effectively instant and never blocks.
  '''
  try:
    # is it already an asynchronous iterable?
    dund_aiter = it.__aiter__
  except AttributeError:
    # not already an asynchronous iterable
    if fast is None:
      fast = type(it) in (list, tuple, set)
    it = iter(it)
    if fast:
      # yield directly from the iterator
      for item in it:
        yield item
      return

    # otherwise we use asyncio.to_thread(next(it))
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
  else:
    # an asynchronous iterable
    async for item in dund_aiter():
      yield item

async def aqget(q: Queue):
  ''' An asynchronous function to get an item from a `queue.Queue`like object `q`.
      It must support the `.get()` and `.get_nowait()` methods.
  '''
  try:
    return q.get_nowait()
  except Empty:
    return await to_thread(q.get)

_aqiter_NO_SENTINEL = object()

async def aqiter(q: Queue, sentinel=_aqiter_NO_SENTINEL):
  ''' An asynchronous generator to yield items from a `queue.Queue`like object `q`.
      It must support the `.get()` and `.get_nowait()` methods.

      An optional `sentinel` object may be supplied, which ends iteration
      if encountered. If a sentinel is specified then this must be the only
      consumer of the queue because the sentinel is consumed.
  '''
  aget = afunc(q.get)
  while True:
    try:
      item = q.get_nowait()
    except Empty:
      item = await aget()
    if item is sentinel:
      return
    yield item

async def amap(
    func: Callable[[Any], Any],
    it: AnyIterable,
    concurrent=False,
    unordered=False,
    indexed=False,
):
  ''' An asynchronous generator yielding the results of `func(item)`
      for each `item` in the iterable `it`.

      `it` may be a synchronous or asynchronous iterable.

      `func` may be a synchronous or asynchronous callable.

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
  func = afunc(func)
  # promote it to an asynchronous iterator
  ait = async_iter(it)
  if not concurrent:
    # serial operation
    i = 0
    async for item in ait:
      result = await func(item)
      yield (i, result) if indexed else result
      i += 1
    return

  # concurrent operation
  # dispatch calls to func() as tasks
  # yield results from an asyncio.Queue

  # a queue of (index, result)
  Q = AQueue()

  # run func(item) and yield its sequence number and result
  # this allows us to yield in order from a heap
  async def qfunc(i, item):
    await Q.put((i, await func(item)))

  # queue all the tasks with their sequence numbers
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

class IterableAsyncQueue(AQueue):
  ''' An iterable subclass of `asyncio.Queue`.

      This modifies `asyncio.Queue` by:
      - adding a `.close()` async method
      - making the queue iterable, with each iteration consuming an item via `.get()`
  '''

  def __init__(self, maxsize=0):
    super().__init__(maxsize=maxsize)
    self.__sentinel = object()
    self.closed = False

  def __aiter__(self):
    return self

  async def __anext__(self):
    ''' Fetch the next item from the queue.
    '''
    item = await super().get()
    if item is self.__sentinel:
      await self.close()
      raise StopAsyncIteration
    return item

  async def get(self):
    ''' We do not allow `.get()`. '''
    raise NotImplementedError

  def get_nowat(self):
    ''' We do not allow `.get_nowait()`. '''
    raise NotImplementedError

  async def close(self):
    ''' Close the queue.
        It is not an error to close the queue more than once.
    '''
    if not self.closed:
      self.closed = True
      await super().put(self.__sentinel)

  @not_closed
  async def put(self, item):
    ''' Put `item` onto the queue.
    '''
    return await super().put(item)

  def put_nowait(self, item):
    ''' Put an item onto the queue without blocking.
    '''
    if self.closed and item is not self.__sentinel:
      raise ClosedError
    return super().put_nowait(item)

class StageMode(StrEnum):
  ''' Special modes for `AsyncPipeLine` pipeline stages.
  '''
  STREAM = auto()  # stream items to the stage_func

@dataclass
class AsyncPipeLine:
  ''' An `AsyncPipeLine` is an asynchronous iterable with a `put` method
      to provide input for processing.

      A new pipeline is usually constructed via the factory method
      `AsyncPipeLine.from_stages(stage_func,...)`.

      It has the same methods as an `IterableAsyncQueue`:
      - `async put(item)` to queue an item for processing
      - `async close()` to close the input, indicating end of the input items
      - iteration to consume the processed results

      It also has the following methods:
      - `async submit(AnyIterable)` to submit multiple items for processing
      - `async __call__(AnyIterable)` to submit the iterable for
        processing and consume the results by iteration


      Example:

          def double(item):
              yield item
              yield item
          pipeline = AsyncPipeLine.from_stages(
              double,
              double,
          )
          async for result in pipeline([1,2,3,4]):
              print(result)
 '''

  inq: IterableAsyncQueue
  tasks: List[Task]
  outq: IterableAsyncQueue

  def __aiter__(self):
    return self

  async def __anext__(self):
    return await next(self.outq)

  async def __call__(self, it: AnyIterable, fast=None):
    ''' Call the pipeline with an iterable.
    '''

    # submit the items from `it` to the pipeline and close `it`
    async def submitter():
      await self.submit(it, fast=fast)
      await self.close()

    create_task(submitter())
    async for result in self.outq:
      yield result

  async def put(self, item):
    ''' Put `item` onto the input queue.
    '''
    return await self.inq.put(item)

  async def close(self):
    ''' Close the input queue.
    '''
    return await self.inq.close()

  async def submit(self, it: AnyIterable, fast=None):
    ''' Submit the items from `it` to the pipeline.
    '''
    async for item in async_iter(it, fast=fast):
      await self.inq.put(item)

  @classmethod
  def from_stages(
      cls,
      *stage_specs,
      maxsize=0,
  ) -> Tuple[IterableAsyncQueue, IterableAsyncQueue]:
    ''' Prepare an `AsyncPipeLine` from stage specifications.
        Return `(inq,tasks,outq)` 3-tuple being an input `IterableAsyncQueue`
        to receive items to process, a list of `asyncio.Task`s per
        stage specification, and an output `IterableAsyncQueue` to
        produce results. If there are no stage_specs the 2 queues
        are the same queue.

        Each stage specification is either:
        - an stage function suitable for `run_stage`
        - a 2-tuple of `(stage_func,batchsize)`
        In the latter case:
        - `stage_func` is an stage function suitable for `run_stage`
        - `batchsize` is an `int`, where `0` means to gather all the
          items from `inq` and supply them as a single batch to
          `stage_func` and where a value `>0` collects items up to a limit
          of `batchsize` and supplies each batch to `stage_func`
        If the `batchsize` is `0` the `stage_func` is called exactly
        once with all the input items, even if there are no input
        items.
    '''
    inq = IterableAsyncQueue(maxsize)
    inq0, outq = inq, inq
    tasks = []
    for stage_spec in stage_specs:
      new_outq = IterableAsyncQueue(maxsize)
      try:
        stage_func, batchsize = stage_spec
      except (
          TypeError,  # not an iterable
          ValueError,  # wrong number of parts
      ):
        # we expect just a stage_func
        stage_func = stage_spec
        batchsize = None
      else:
        assert isinstance(batchsize, StageMode) or batchsize >= 0
      assert callable(stage_func)
      tasks.append(
          create_task(
              cls.run_stage(inq, stage_func, new_outq, batchsize=batchsize)
          )
      )
      inq, outq = new_outq, new_outq
    return cls(inq=inq0, tasks=tasks, outq=outq)

  @staticmethod
  async def run_stage(
      inq: IterableAsyncQueue,
      stage_func,
      outq: IterableAsyncQueue,
      batchsize: Optional[Union[int, None, StageMode]] = None,
  ):
    ''' Run a pipeline stage, copying items from `inq` to the `stage_func`
        and putting results onto `outq`. After processing, `outq` is
        closed.

        `stage_func` is a callable which may be:
        - a sync or async generator which yields results to place onto `outq`
        - a sync or async function which returns a single result

        If `batchsize` is `None`, the default, each input item is
        passed to `stage_func(item)`, which yields the results from the
        single item.

        If `batchsize` is an `int`, items from `inq` are collected
        into batches up to a limit of `batchsize` (no limit if
        `batchsize` is `0`) and passed to `stage_func(batch)`, which
        yields the results from the batch of items.
        If the `batchsize` is `0` the `stage_func` is called exactly
        once with all the input items, even if there are no input
        items.
    '''
    if isasyncgenfunction(stage_func):
      pass
    elif isgeneratorfunction(stage_func):
      stage_func = agen(stage_func)
    else:
      if batchsize is StageMode.STREAM:
        raise ValueError(
            f'cannot use StageMode.STREAM with a nongenerator function {stage_func}'
        )
      # a callable, turn it into a single result generator
      if not iscoroutinefunction(stage_func):
        stage_func = afunc(stage_func)
      stage_func0 = stage_func

      async def stage_func(item):
        yield await stage_func0(item)

    try:
      if batchsize is StageMode.STREAM:
        # stream inq through the stage_func
        async for result in stage_func(inq):
          await outq.put(result)
      else:
        # not streaming
        if batchsize is None:
          batch = None
        elif batchsize < 0:
          raise ValueError(
              f'batchsize must be None or a nonnegative integer, got {type(batchsize)}:{batchsize!r}'
          )
        else:
          batch = []
          first_batch = True

        async for item in inq:
          if batchsize is None:
            # process each ite in turn
            async for result in stage_func(item):
              await outq.put(result)
          else:
            # gather items into a batch and process the batch
            batch.append(item)
            if batchsize != 0 and len(batch) >= batchsize:
              async for result in stage_func(batch):
                await outq.put(result)
              batch = []
              first_batch = False

        if batch is not None and (first_batch or batch):
          async for result in stage_func(batch):
            await outq.put(result)

    finally:
      await outq.close()

if __name__ == '__main__':

  import time
  import random

  print_ = partial(print, end='', flush=True)

  if False:  # debugging

    async def demo_pipeline2(it: AnyIterable):
      print("pipeline(hrefs)www.smh.com.au...")
      try:
        from cs.app.pilfer.urls import hrefs
      except ImportError as e:
        print("no cs.app.pilfer.urls, skipping pipeline demo:", e)
      else:
        pipeline = AsyncPipeLine.from_stages(hrefs)
        async for result in pipeline(it):
          print(result)

    run(demo_pipeline2(['https://www.smh.com.au/']))

    @agen
    def gen():
      yield from range(5)

    async def async_generator_demo():
      print_("async_generator_demo @gen(gen):")
      async for item in gen():
        print_("", repr(item))
      print()

    run(async_generator_demo())

    @afunc
    def async_function_demo(sleep_time, result):
      print_("@afunc(func): sleep", sleep_time)
      time.sleep(sleep_time)
      print(", return result", result)
      return result

    run(async_function_demo(2.0, 9))

    def async_iter_gen():
      yield 'gen1'
      yield 'gen2'

    async def async_iter_agen():
      yield 'agen1'
      yield 'agen2'

    async def async_iter_demo(it: AnyIterable, fast=None):
      print_("async_iter", type(it), "fast", fast)
      async for item in async_iter(it):
        print_("", item)
      print()

    for fast in None, False, True:
      for it in (
          [1, 2, 3],
          (4, 5, 6),
          {7, 8, 9},
          async_iter_gen(),
          async_iter_agen(),
      ):
        run(async_iter_demo(it, fast=fast))

    def sync_sleep(sleep_time):
      ##print('func sleep_time', sleep_time, 'start')
      time.sleep(sleep_time)
      ##print('func sleep_time', sleep_time, 'done')
      return f'slept {sleep_time}'

    async def test_amap():
      for concurrent in False, True:
        for unordered in False, True:
          for indexed in False, True:
            print_(
                'amap',
                f'{concurrent=}',
                f'{unordered=}',
                f'{indexed=}',
            )
            start_time = time.time()
            async for result in amap(
                sync_sleep,
                [random.randint(1, 10) / 10 for _ in range(5)],
                concurrent=concurrent,
                unordered=unordered,
                indexed=indexed,
            ):
              print_("", result)
            print(f': elapsed {round(time.time()-start_time, 2)}')

    run(test_amap())

    async def putrange(n, q):
      for i in range(n):
        await q.put(i)
      await q.close()

    async def readq(q):
      print("putrange(5,q):")
      create_task(putrange(5, q))
      async for item in q:
        print_("", item)
      print()

    Q = IterableAsyncQueue()
    run(readq(Q))

    def double(item):
      yield item
      yield item

    def stringify(item):
      yield f'string {item!r}'

    async def demo_pipeline(it: AnyIterable):
      print("pipeline(double,stringify)[1,2,3]...")
      pipeline = AsyncPipeLine.from_stages(double, stringify)
      async for result in pipeline(it):
        print(result)

    run(demo_pipeline([1, 2, 3]))

  else:

    async def aqget3(q):
      for _ in range(3):
        print_(" ->", await aqget(q))

    print_("aqget [1,2,3]")
    myq = Queue()
    for item in [1, 2, 3]:
      myq.put(item)
    run(aqget3(myq))
    print()

    async def aqiter3(q):
      n = 0
      async for item in aqiter(q):
        print_(" ->", item)
        n += 1
        if n == 3:
          break

    print_("aqiter3 [1,2,3]")
    myq = Queue()
    for item in [1, 2, 3]:
      myq.put(item)
    run(aqiter3(myq))
    print()

    async def aqiter_sent(q, sent):
      async for item in aqiter(q, sent):
        print_(" ->", item)

    print_("aqiter_sent")
    qsent = "SENT"
    myq = Queue()
    for item in [1, 2, 3]:
      myq.put(item)
    myq.put(qsent)
    run(aqiter_sent(myq, qsent))
    print()
