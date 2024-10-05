#!/usr/bin/env python3

''' Common utilities for cs.ebooks.
'''

from dataclasses import dataclass, field
from typing import Optional

from cs.cmdutils import BaseCommand

@dataclass
class EBooksCommonOptions(BaseCommand.Options):
  ''' Set up the default values in `options`.
    '''

  # pylint: disable=no-method-argument
  def _kindle_path():
    from .kindle import KindleTree  # pylint: disable=import-outside-toplevel
    try:
      # pylint: disable=protected-access
      kindle_path = KindleTree._resolve_fspath(None)
    except ValueError:
      kindle_path = None
    return kindle_path

  kindle_path: Optional[str] = field(default_factory=_kindle_path)

  # pylint: disable=no-method-argument
  def _calibre_path():
    from .calibre import CalibreTree  # pylint: disable=import-outside-toplevel
    try:
      # pylint: disable=protected-access
      calibre_path = CalibreTree._resolve_fspath(None)
    except ValueError:
      calibre_path = None
    return calibre_path

  calibre_path: Optional[str] = field(default_factory=_calibre_path)

  # pylint: disable=no-method-argument
  def _dedrm_path():
    from .dedrm import DeDRMWrapper  # pylint: disable=import-outside-toplevel
    return DeDRMWrapper.get_package_path()

  dedrm_package_path: Optional[str] = field(default_factory=_dedrm_path)

  COMMON_OPT_SPECS = dict(
      C_='calibre_path',
      D_='dedrm_package_path',
      K_='kindle_path',
      **BaseCommand.Options.COMMON_OPT_SPECS,
  )
