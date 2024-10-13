#!/usr/bin/env python3

''' Common utilities for cs.ebooks.
'''

from dataclasses import dataclass
from functools import cached_property
from typing import Optional

from cs.cmdutils import BaseCommand
from cs.logutils import warning

@dataclass
class EBooksCommonOptions(BaseCommand.Options):
  ''' Set up the default values in `options`.
    '''

  kindle_path: Optional[str] = None

  @cached_property
  def kindle(self):
    from .kindle import KindleTree  # pylint: disable=import-outside-toplevel
    kindle = KindleTree(self.kindle_path)
    self.kindle_path = kindle.fspath
    return kindle

  calibre_path: Optional[str] = None

  @cached_property
  def calibre(self):
    from .calibre import CalibreTree  # pylint: disable=import-outside-toplevel
    calibre = CalibreTree(self.calibre_path)
    self.calibre_path = calibre.fspath
    return calibre

  dedrm_package_path: Optional[str] = None

  @cached_property
  def dedrm(self):
    from .dedrm import DeDRMWrapper  # pylint: disable=import-outside-toplevel
    try:
      dedrm_package_path = DeDRMWrapper.get_package_path(
          self.dedrm_package_path,
          calibre=self.calibre,
      )
    except ValueError as e:
      warning("could not obtain the DeDRM package: %s", e)
      return None
    dedrm = DeDRMWrapper(dedrm_package_path)
    self.dedrm_package_path = dedrm.fspath
    return dedrm

  COMMON_OPT_SPECS = dict(
      C_='calibre_path',
      D_='dedrm_package_path',
      K_='kindle_path',
      **BaseCommand.Options.COMMON_OPT_SPECS,
  )

class EBooksCommonBaseCommand(BaseCommand):
  ''' A common `BaseCommand` subclass for the ebooks commands.
  '''

  Options = EBooksCommonOptions
