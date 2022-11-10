#!/usr/bin/envpython3

from typing import Callable, Optional, Tuple, TypeVar, Union

from cs.pfx import Pfx, pfx_call, pfx_method
from cs.resources import MultiOpenMixin

from cs.lex import r, s
from cs.x import X

from direct.showbase.ShowBase import ShowBase
from direct.task import Task
from typeguard import typechecked

from .egg import Model

class P3DApp(MultiOpenMixin, ShowBase):

  DEFAULT_SCALE = 1.0, 1.0, 1.0
  DEFAULT_POS = -10, 10, 0

  @typechecked
  def __init__(
      self,
      scene: Optional[Union[str, Model]] = None,  # TODO: add scene type
      *,
      scale: Optional[Tuple[float, float, float]] = None,
      pos: Optional[Tuple[float, float, float]] = None
  ):
    if scale is None:
      scale = self.DEFAULT_SCALE
    if pos is None:
      pos = self.DEFAULT_POS
    super().__init__()
    print("self.render", s(self.render))
    # Load the environment model.
    if scene is None:
      self.scene = None
    else:
      self.scene = (
          self.add_model(scene) if isinstance(scene, (str, Model)) else scene
      )
      # Reparent the model to render.
      self.scene.reparentTo(self.render)
      # Apply scale and position transforms on the model.
      self.scene.setScale(*scale)
      self.scene.setPos(pos)

  @pfx_method
  def add_model(self, modelref, **lm_kwargs):
    if isinstance(modelref, str):
      # filename
      return self.loader.loadModel(modelref, **lm_kwargs)
    if isinstance(modelref, Model):
      with modelref.saved() as eggpath:
        return self.loader.loadModel(eggpath, **lm_kwargs)
    raise TypeError(
        f'do not know how to loadModel from type {modelref.__class__.__name__}'
    )

  @typechecked
  def add_task(
      self,
      task_func: Callable[[Task.PythonTask], int],
      name: Optional[str] = None,
  ):
    ''' Convenience method to add a task.
    '''
    if name is None:
      name = task_func.__qualname__
    return self.taskMgr.add(task_func, name)
