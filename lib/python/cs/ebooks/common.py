#!/usr/bin/env python3

''' Common utilities for cs.ebooks.
'''

from abc import abstractmethod
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Mapping, Optional

from cs.cmdutils import BaseCommand
from cs.fs import FSPathBasedSingleton
from cs.lex import tabulate
from cs.logutils import warning
from cs.resources import MultiOpenMixin

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

  kobo_path: Optional[str] = None

  @cached_property
  def kobo(self):
    from .kobo import KoboTree  # pylint: disable=import-outside-toplevel
    kobo = KoboTree(self.kobo_path)
    self.kobo_path = kobo.fspath

  COMMON_OPT_SPECS = dict(
      C_='calibre_path',
      calibre_='calibre_path',
      D_='dedrm_package_path',
      dedrm_='dedrm_package_path',
      K_='kindle_path',
      kindle_='kindle_path',
      kobo_='kobo_path',
      **BaseCommand.Options.COMMON_OPT_SPECS,
  )

class EBooksCommonBaseCommand(BaseCommand):
  ''' A common `BaseCommand` subclass for the ebooks commands.
  '''

  Options = EBooksCommonOptions

  def cmd_info(self, argv):
    super().cmd_info(argv)
    options = self.options
    rows = []

    def library_row(title, library):
      ''' Return a row to tabulate for a `library`.
      '''
      return (
          f'{title}:',
          '-' if library is None else f'{len(library)} ebooks',
          str(library) if library is None else library.shortpath,
      )

    for line in tabulate(
        library_row('Calibre', options.calibre),
        library_row('Kindle', options.kindle),
        library_row('Kobo', options.kobo),
    ):
      print(line)

class AbstractEbooksTree(FSPathBasedSingleton, MultiOpenMixin, MappingABC):
  ''' A common base class for the `*Tree` classes accessing some
      ebook reader's library.
  '''

  def __str__(self):
    return "%s:%s" % (self.__class__.__name__, self.shortpath)

  def __repr__(self):
    return "%s(%r)" % (self.__class__.__name__, self.fspath)

  @abstractmethod
  def get_library_books_mapping(self) -> Mapping:
    ''' Return a mapping of library primary keys to library book instances.
    '''
    raise NotImplemenetedError

  def __iter__(self) -> Iterable:
    ''' Yield primary keys from the library.
        This might yield ASINs for a Kindle library, VolumeIDs for a Kobo library, etc.
    '''
    return iter(self.get_library_books_mapping())

  def __getitem__(self, key):
    ''' Return the library book instance for the primary `key`.
    '''
    return self.get_library_books_mapping()[key]

  def __len__(self):
    ''' Return the number of library book instances.
    '''
    return len(self.get_library_books_mapping())

  def books(self):
    ''' Return the book instances for this library.
    '''
    return self.get_library_books_mapping().values()
