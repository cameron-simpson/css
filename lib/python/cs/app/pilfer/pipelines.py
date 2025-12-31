#!/usr/bin/env puython3

from asyncio import create_task, Queue as AQueue, run
from dataclasses import dataclass
from functools import partial
import shlex
from typing import AsyncIterable, List, Union

from cs.deco import Promotable
from cs.logutils import warning
from cs.naysync import async_iter, AnyIterable, AsyncPipeLine, StageMode
from cs.pfx import Pfx, pfx_method

from .actions import _Action
from .pilfer import Pilfer, uses_pilfer

@dataclass
class PipeLineSpec(Promotable):
  ''' A pipeline specification: a name and list of actions.
  '''

  name: Union[str, None]
  stage_specs: List[str]  # source list

  @classmethod
  def from_str(cls, pipe_spec_s, name=None):
    ''' Return an `PipeLineSpec` from a string description.
    '''
    return cls(name=name, stage_specs=shlex.split(pipe_spec_s))

  @pfx_method
  def make_stage_funcs(self, *, P: Pilfer) -> AsyncPipeLine:
    ''' Construct a list of stage functions for use in an `AsyncPipeLine`
        from an iterable of pipeline stage specification strings
        and an action mapping.

        This scans `self.stage_specs` and handles the spacial
        specifications `"*"` and `"**"`, which spawn subpipelines.
        Other specifications are passed through to `_Action.from_str`.

        The parameter `P` is a `Pilfer` instance used to translate
        action names to stage functions.
    '''
    specs = list(self.stage_specs)
    stage_funcs = []
    while specs:
      spec = specs.pop(0)
      with Pfx("%r", spec):
        # support commenting-out of individual actions
        if spec.startswith('#'):
          continue

        if spec == "*":
          # fork a new pipeline instance per item
          # terminate this pipeline with a function to spawn subpipelines
          # using the tail of the action list from this point
          # this runs each subpipeline in sequence per item
          if not specs:
            raise ValueError(f'no actions after {spec!r}')
          subpipelinespec = type(self)(
              name=f'{spec!r} {" ".join(map(shlex.quote, specs))}',
              stage_specs=specs,
          )
          try:
            testsubpipe = subpipelinespec.make_stage_funcs(P=P)
          except ValueError as e:
            warning("invalid subpipeline %r: %s", subpipelinespec, e)
            raise

          # a stage func to stream items to subpipelines in series
          async def per(item, pipespec):
            ''' Process a single `item` through its own pipeline,
                yield the results from the pipeline.
            '''
            subpipe = pipespec.make_pipeline(P=P)
            async for result in subpipe([item]):
              yield result

          stage_funcs.append(partial(per, pipespec=subpipelinespec))
          spec = []
          continue

        if spec == "**":
          # fork a new pipeline instance per item
          # terminate this pipeline with a function to spawn subpipelines
          # using the tail of the action list from this point
          # this runs each subpipeline concurrently as items arrive
          if not specs:
            raise ValueError(f'no actions after {spec!r}')
          subpipelinespec = type(self)(
              name=f'{spec!r} {shlex.join(specs)}',
              stage_specs=specs,
          )
          # sanity check the subpipeline
          try:
            testsubpipe = subpipelinespec.make_stage_funcs(P=P)
          except ValueError:
            ##warning("invalid subpipeline %r: %s", subpipelinespec, e)
            raise

          # a stage func to stream items to subpipelines concurrently
          async def collect_for(
              subitem,
              ait: AsyncIterable,
              outaq: AQueue,
              sentinel: object,
          ):
            ''' Collect results from feeding `subitem` to `subpipe`.
                Put `(subitem,result)` 2-tuples onto a shared `outq`.
                Put a final `(subitem,sentinel)` 2-tuple onto `outq`.
            '''
            try:
              async for result in ait:
                await outaq.put((subitem, result))
            finally:
              await outaq.put((subitem, sentinel))

          async def per(inq, pipespec):
            ''' Process each item from `inq` through its own pipeline.
                Run all the subpipelines concurrently.
                This means that the output may have pipeline outputs interleaved.
            '''
            aq = AQueue()  # async queue producing `(item,result)` 2-tuples
            nsubpipes = 0  # number of dispatched subpipelines
            sentinel = object()  # marker for end of pipeline output
            async for item in inq:
              # construct a pipeline to process item
              subpipe = pipespec.make_pipeline(P=P)
              # dispatch the pipeline worker
              create_task(collect_for(item, subpipe([item]), aq, sentinel))
              nsubpipes += 1
            while nsubpipes > 0:
              subitem, result = await aq.get()
              if result is sentinel:
                nsubpipes -= 1
              else:
                # TODO: various modes: with-subitem, batched-by-subitem
                yield result

          stage_funcs.append(
              (
                  partial(per, pipespec=subpipelinespec),
                  StageMode.STREAM,
              )
          )
          specs = []
          continue

        # regular _Action
        action = _Action.from_str(spec, P=P)
        stage_funcs.append(action.stage_spec)
    return stage_funcs

  def make_pipeline(self, *, P: Pilfer) -> AsyncPipeLine:
    ''' Construct an `AsyncPipeLine` from an iterable of pipeline
        stage specification strings and an action mapping.
    '''
    return AsyncPipeLine.from_stages(*self.make_stage_funcs(P=P))

  @uses_pilfer
  async def run_pipeline(
      self,
      input_item_Ps: AnyIterable,
      *,
      P: Pilfer = None,
      has_pilfer=False,
      fast=None,
  ):
    r'''An asynchronous generator to make an `AsyncPipeLine` and run it with `input_items`.
        It yields `(result,Pilfer)` 2-tuples.

        Parameters:
        * `input_item_Ps`: an iterable of input items
        * `P`: an optional `Pilfer` context; the default comes from the ambient instance;
          this is used when resolving stage specifications to stage functions
        * `has_pilfer`: if true then `input_item_Ps` consists of `(item,Pilfer)` 2-tuples,
          otherwise it consists of just `item`s, and `run_pipeline`
          will produce 2-tuples for the pipeline using the `P` parameter
        * `fast`: optional `fast` parameter passed to `async_iter` and `AsyncPipeLine.__call__`

        Example: this is the pipeline dispatch from `PilferCommand.cmd_from`:

            # prepare the main pipeline specification from the remaining argv
            pipespec = PipeLineSpec(name="CLI", stage_specs=argv)
            # prepare an input containing URLs
            if url == '-':
              urls = (line.rstrip('\n') for line in sys.stdin)
            else:
              urls = [url]

            async def print_from(item_Ps):
              """ Consume `(result,Pilfer)` 2-tuples from the pipeline and print the results.
              """
              async for result, _ in item_Ps:
                print(result)

            asyncio.run(print_from(pipespec.run_pipeline(urls)))

    '''
    if not has_pilfer:
      # add the Pilfer to each item
      async def add_pilfer(input_items: AnyIterable):
        ''' Transmute the iterable of items to an iterable of `(item,Pilfer)` 2-tuples.
        '''
        async for item in async_iter(input_items, fast=fast):
          yield item, P

      input_item_Ps = add_pilfer(input_item_Ps)
    pipeline = self.make_pipeline(P=P)
    async for result_P in pipeline(input_item_Ps, fast=fast):
      yield result_P

if __name__ == '__main__':

  def double(item):
    yield item
    yield item

  def stringify(item):
    yield f'string {item!r}'

  async def demo_pipeline(it: AnyIterable):
    pipeline = AsyncPipeLine.from_stages(double, stringify)
    async for result in pipeline(it):
      print(result)

  run(demo_pipeline([1, 2, 3]))
