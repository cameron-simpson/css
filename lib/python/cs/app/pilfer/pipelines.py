#!/usr/bin/env puython3

from asyncio import run
from collections import namedtuple
from dataclasses import dataclass
from functools import partial
import shlex
from typing import Callable, List, Mapping, Union

from cs.deco import Promotable
from cs.lex import get_identifier
from cs.naysync import AsyncPipeLine, AnyIterable
from cs.pfx import Pfx, pfx_method

from .actions import Action

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
            raise ValueError('no actions after')

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

  async def run_pipeline(self, input_items: AnyIterable):
    ''' Asynchronous generator to make an `AsyncPipeLine` and run it with `input_items`.
    '''
    pipeline = self.make_pipeline()
    async for item in pipeline(input_items):
      yield item

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
