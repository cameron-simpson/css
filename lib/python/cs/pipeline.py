#!/usr/bin/env python3

''' Function pipelines mediated by queues and a Later.
'''

from enum import Enum, auto as auto_enum, unique as unique_enum
from icontract import require
from cs.later import DEFAULT_RETRY_DELAY
from cs.logutils import debug, error
from cs.pfx import pfx
from cs.py.func import funcname
from cs.queues import IterableQueue, PushQueue
from cs.resources import MultiOpenMixin
from cs.seq import TrackingCounter
from cs.threads import bg as bg_thread

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.later',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.queues',
        'cs.resources',
        'cs.seq',
        'cs.threads',
    ],
}

@unique_enum
class StageType(Enum):
  # func(item) returns an iterable
  ONE_TO_MANY = auto_enum()
  # func(item) returns a single item
  ONE_TO_ONE = auto_enum()
  # func(item) returns a Boolean, item is passed on or dropped
  SELECTOR = auto_enum()
  # func(items) returns an iterable, consuming all the incoming items
  MANY_TO_MANY = auto_enum()
  # a pipeline in a pipeline, a streaming many to many
  PIPELINE = auto_enum()

@pfx
@require(lambda later: later.is_submittable())
def pipeline(later, actions, inputs=None, outQ=None, name=None):
  ''' Construct a function pipeline to be mediated by a `Later` queue.

      Return: `input, output`
      where `input`` is a closeable queue on which more data items can be put
      and `output` is an iterable from which results can be collected.

      Parameters:
      * `actions`: an iterable of filter functions accepting
        single items from the iterable `inputs`, returning an
        iterable output.
      * `inputs`: the initial iterable inputs; this may be None.
        If missing or None, it is expected that the caller will
        be supplying input items via `input.put()`.
      * `outQ`: the optional output queue; if None, an IterableQueue() will be
        allocated.
      * `name`: name for the PushQueue implementing this pipeline.

      If `inputs` is None or `open` is true, the returned `input` requires
      a call to `input.close()` when no further inputs are to be supplied.

      Example use with presupplied Later `L`:

          input, output = L.pipeline(
                  [
                    ls,
                    filter_ls,
                    ( StageType.MANY_TO_MANY, lambda items: sorted(list(items)) ),
                  ],
                  ('.', '..', '../..'),
                 )
          for item in output:
            print(item)
  '''
  filter_funcs = list(actions)
  if not filter_funcs:
    raise ValueError("no actions")
  if outQ is None:
    outQ = IterableQueue(name="pipelineIQ")
  if name is None:
    name = "pipelinePQ"
  pipeline = Pipeline(name, later, filter_funcs, outQ)
  inQ = pipeline.inQ
  if inputs is None:
    debug(
        "pipeline: no inputs, NOT setting up %s._defer_iterable(inputs, inQ=%r)",
        later, inQ
    )
  else:
    later.defer_iterable(inputs, inQ)
  return pipeline

class _PipelineStage(PushQueue):
  ''' A `_PipelineStage` subclasses `cs.queues.PushQueue` and mediates
      computation via a `Later`; it also adds some activity tracking.

      This represents a single stage in a Later pipeline of functions.
      We raise the pipeline's _busy counter for every item in play,
      and also raise it while the finalisation function has not run.
      This lets us inspect a pipeline for business, which we use in the
      `cs.app.pilfer` termination process.
  '''

  def __init__(self, name, pipeline, functor, outQ, retry_interval=None):
    ''' Initialise the `_PipelineStage`, wrapping func_iter and
        func_final in code to inc/dec the main pipeline _busy counter.

        Parameters:
        * `name`: name for this pipeline stage as for `PushQueue`
        * `pipeline`: parent pipeline for this pipeline stage
        * `functor`: callable used to process items
        * `outQ`: output queue
        * `retry_interval`: how often to retry (UNUSED? TODO: reimplement)
    '''
    if retry_interval is None:
      retry_interval = DEFAULT_RETRY_DELAY
    PushQueue.__init__(self, name, functor, outQ)
    self.pipeline = pipeline
    self.retry_interval = retry_interval

  def defer(self, functor, *a, **kw):
    ''' Submit a callable `functor` for execution.
    '''
    return self.pipeline.later.defer(functor, *a, **kw)

  @not_cancelled
  def defer_iterable(self, it: Iterable, outQ):
    ''' Submit an iterable `it` for processing to `outQ`.
    '''
    return self.pipeline.later.defer_iterable(it, outQ)

class _PipelineStageOneToOne(_PipelineStage):

  def put(self, item):
    # queue computable then send result to outQ
    self.outQ.open()
    LF = self.defer(self.functor, item)

    def notify(LF):
      # collect result: queue or report exception
      item2, exc_info = LF.join()
      if exc_info:
        # report exception
        error("%s.put(%r): %r", self.name, item, exc_info, stack_info=True)
      else:
        self.outQ.put(item2)
      self.outQ.close()

    LF.notify(notify)

class _PipelineStageOneToMany(_PipelineStage):

  def put(self, item):
    self.outQ.open()
    # compute the iterable
    LF = self.defer(self.functor, item)

    def notify(LF):
      it, exc_info = LF.join()
      if exc_info:
        # report exception
        error("%s.put(%r): %r", self.name, item, exc_info, stack_info=True)
        self.outQ.close()
      else:
        self.defer_iterable(it, self.outQ)

    LF.notify(notify)

class _PipelineStageManyToMany(_PipelineStage):

  def __init__(self, name, pipeline, functor, outQ, **kw):
    super().__init__(name, pipeline, functor, outQ, **kw)
    self.gathered = []

  def put(self, item):
    self.gathered.append(item)

  def shutdown(self):
    # queue function with all items, get iteratable
    self.outQ.open()
    gathered = self.gathered
    self.gathered = None
    LF = self.defer(self.functor, gathered)

    def notify(LF):
      it, exc_info = LF.join()
      if exc_info:
        # report exception
        error("%s.put(%r): %r", self.name, it, exc_info, stack_info=True)
        self.outQ.close()
      else:
        self.defer_iterable(it, self.outQ)
      _PipelineStage.shutdown(self)

    LF.notify(notify)

class _PipelineStagePipeline(_PipelineStage):
  ''' A _PipelineStage which feeds an asynchronous pipeline.
  '''

  def __init__(self, name, pipeline, subpipeline, outQ, **kw):
    super().__init__(name, pipeline, None, outQ, **kw)
    self.subpipeline = subpipeline
    outQ.open()

    def copy_out(sub_outQ, outQ):
      for item in sub_outQ:
        outQ.put(item)
      outQ.close()

    self.copier = bg_thread(
        copy_out, name="%s.copy_out" % (self,), args=(subpipeline.outQ, outQ)
    )

  def put(self, item):
    self.subpipeline.put(item)

  def shutdown(self):
    self.subpipeline.close()
    self.copier.join()
    super().shutdown()

class Pipeline(MultiOpenMixin):
  ''' A `Pipeline` encapsulates the chain of `PushQueue`s created by
      a call to `Later.pipeline`.
  '''

  @uses_later
  @typechecked
  def __init__(self, name: str, *, later: Later, actions: Iterable, outQ):
    ''' Initialise the Pipeline from `name`, Later instance `later`,
        list of filter functions `actions` and output queue `outQ`.

        Each action is either a 2-tuple of `(sig,functor)` or an
        object with a .sig attribute and a .functor method returning
        a callable.
    '''
    self.name = name
    self.later = later
    self.queues = [outQ]
    # counter tracking items in play
    self._busy = TrackingCounter(name="Pipeline<%s>._items" % (name,))
    RHQ = outQ
    for index, action in reversed(list(enumerate(actions))):
      try:
        func_sig, functor = action
      except TypeError:
        func_sig = action.sig
        functor = action.functor(self.later)
      pq_name = ":".join(
          (
              name,
              str(index),
              str(func_sig),
              funcname(functor),
          )
      )
      if func_sig == StageType.ONE_TO_MANY:
        PQ = _PipelineStageOneToMany(pq_name, self, functor, RHQ)
      elif func_sig == StageType.ONE_TO_ONE:
        PQ = _PipelineStageOneToOne(pq_name, self, functor, RHQ)
      elif func_sig == StageType.SELECTOR:

        select_by = functor

        def selector(item):
          if select_by(item):
            yield item

        PQ = _PipelineStageOneToMany(pq_name, self, selector, RHQ)
      elif func_sig == StageType.MANY_TO_MANY:
        PQ = _PipelineStageManyToMany(pq_name, self, functor, RHQ)
      elif func_sig == StageType.PIPELINE:
        PQ = _PipelineStagePipeline(pq_name, self, functor, RHQ)
      else:
        raise RuntimeError(
            "unimplemented func_sig=%r, functor=%s" % (func_sig, functor)
        )
      PQ.open()
      self.queues.insert(0, PQ)
      RHQ = PQ

  def __str__(self):
    return "cs.later.Pipeline:%s" % (self.name,)

  def __repr__(self):
    return "<%s %d queues, later=%s>" % (self, len(self.queues), self.later)

  def put(self, item):
    ''' Put an `item` onto the leftmost queue in the pipeline.
    '''
    return self.inQ.put(item)

  @property
  def inQ(self):
    ''' Property returning the leftmost queue in the pipeline, the input queue.
    '''
    return self.queues[0]

  @property
  def outQ(self):
    ''' Property returning the rightmost queue in the pipeline, the output queue.
    '''
    return self.queues[-1]

  @contextmanager
  def startup_shutdown(self):
    ''' Startup/shutdown for the Pipeline, required method of MultiOpenMixin.
    '''
    yield self
    self.inQ.close(enforce_final_close=True)

  def join(self):
    ''' Wait for completion of the output queue.
    '''
    self.outQ.join()
