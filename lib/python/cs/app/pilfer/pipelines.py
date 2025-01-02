#!/usr/bin/env puython3

from asyncio import run
from dataclasses import dataclass
from functools import partial
import shlex
from typing import List, Union

from cs.deco import Promotable
from cs.naysync import AsyncPipeLine, AnyIterable, async_iter
from cs.pfx import Pfx, pfx_method

from .actions import Action
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
  def make_pipeline(self) -> AsyncPipeLine:
    ''' Construct an `AsyncPipeLine` from an iterable of pipeline
        stage specification strings and an action mapping.
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
          if not specs:
            raise ValueError('no actions after *')

          async def per(item, pipespec):
            ''' Process a single `item` through its own pipeline.
            '''
            subpipe = pipespec.make_pipeline()
            async for result in subpipe([item]):
              yield result

          subpipelinespec = type(self)(
              name=f'{spec} {" ".join(map(shlex.quote, specs))}',
              argv=specs,
          )
          stage_funcs.append(partial(per, pipespec=subpipelinespec))
          specs = []
          continue
        action = Action.from_str(spec)
        stage_funcs.append(action.stage_spec)
    return AsyncPipeLine.from_stages(*stage_funcs)

  @uses_pilfer
  async def run_pipeline(
      self,
      input_item_Ps: AnyIterable,
      *,
      P: Pilfer = None,
      has_pilfer=False,
      fast=None,
  ):
    ''' An asynchronous generator to make an `AsyncPipeLine` and run it with `input_items`.
        It yields `(result,Pilfer)` 2-tuples.

        Parameters:
        * `input_item_Ps`: an iterable of input items
        * `P`: an optional `Pilfer` context; the default comes from the ambient instance
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
    pipeline = self.make_pipeline()
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
