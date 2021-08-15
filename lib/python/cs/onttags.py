#!/usr/bin/env python3

''' An ontology command line tool for cs.fstags or cs.sqltags stores.
'''

from contextlib import contextmanager
import os
from os.path import (
    expanduser, isdir as isdirpath, isfile as isfilepath, join as joinpath
)
import sys
from typeguard import typechecked
from cs.context import stackattrs
from cs.fstags import TagFile
from cs.lex import cutsuffix
from cs.logutils import warning
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.tagset import TagsOntology, TagsOntologyCommand

from cs.x import X

ONTTAGS_PATH_DEFAULT = '~/var/ontology'
ONTTAGS_PATH_ENVVAR = 'ONTTAGS'

def main(argv=None):
  ''' Command line mode.
  '''
  return OntCommand(argv).run()

class OntCommand(TagsOntologyCommand):
  ''' Command line tool to manipulate ontologies.
  '''

  GETOPT_SPEC = 'f:'

  USAGE_FORMAT = '''Usage: {cmd} [-f ontology] subcommand [...]
  -f ontology   The filename of an SQLite or flat text TagFile.
                Default from ${ONTTAGS_PATH_ENVVAR} or {ONTTAGS_PATH_DEFAULT}.'''

  USAGE_KEYWORDS = {
      'ONTTAGS_PATH_DEFAULT': ONTTAGS_PATH_DEFAULT,
      'ONTTAGS_PATH_ENVVAR': ONTTAGS_PATH_ENVVAR,
  }

  SUBCOMMAND_ARGV_DEFAULT = 'type'

  def apply_defaults(self):
    ''' Provide a default `self.options.ont_path`.
    '''
    options = self.options
    options.ont_path = os.environ.get(ONTTAGS_PATH_ENVVAR)

  def apply_opt(self, opt, val):
    options = self.options
    if opt == '-f':
      options.ont_path = val
    else:
      super().apply_opt(opt, val)

  @contextmanager
  def run_context(self):
    ''' Set up the ontology around a run.
    '''
    options = self.options
    ont_path = options.ont_path
    if ont_path is None:
      ont_path = options.ont_path = expanduser(ONTTAGS_PATH_DEFAULT)
    ont = Ont(ont_path)
    with ont:
      with stackattrs(options, ontology=ont):
        with super().run_context():
          yield

class Ont(TagsOntology):
  ''' A `TagsOntology` based on a persistent store.
  '''

  @typechecked
  def __init__(self, ont_path: str):
    self.ont_path = ont_path
    tagsets, ont_pfx_map = self.tagsetses_from_path(ont_path)
    super().__init__(tagsets)
    # apply any prefix TagSetses
    for prefix, subtagsets in sorted(ont_pfx_map.items(), key=lambda item:
                                     (len(item[0]), item[0])):
      prefix_ = prefix + '.'
      X("add %r => %s", prefix_, subtagsets)
      self.add_tagsets(subtagsets, prefix_)

  @classmethod
  @typechecked
  def tagsetses_from_path(cls, ont_path: str):
    ''' Return `(tagsets,ont_pfx_map)` instance from `ont_path`,
        being the default `TagSets` and a mapping of name->`TagSets`
        for various subontologies.

        If `ont_path` resolves to a file the mapping wil be empty;
        return an `SQLTags` if `ont_path` ends with `'.sqlite'`
        otherwise a `TagFile`.

        If `ont_path` resolves to a directory, scan the entries.
        An entry named *prefix*`.sqlite` adds a *prefix*->`SQLTags`
        entry to the mapping.
        An entry named *prefix*`.tags` adds a *prefix*->`TagFile`
        entry to the mapping.
        After the scan, `tagsets` is set from the entry
        whose prefix was `'_'`, or `None`.
    '''
    ont_pfx_map = {}
    if isfilepath(ont_path):
      if ont_path.endswith('.sqlite'):
        tagsets = SQLTags(ont_path)
      else:
        tagsets = TagFile(ont_path)
    elif isdirpath(ont_path):
      with Pfx("listdir(%r)", ont_path):
        for subont_name in os.listdir(ont_path):
          if not subont_name or subont_name.startswith('.'):
            continue
          subont_path = joinpath(ont_path, subont_name)
          with Pfx(subont_path):
            if not isfilepath(subont_path):
              warning("not a file")
            prefix = cutsuffix(subont_name, '.sqlite')
            if prefix is not subont_name:
              ont_pfx_map[prefix] = SQLTags(subont_path)
              continue
            prefix = cutsuffix(subont_name, '.tags')
            if prefix is not subont_name:
              ont_pfx_map[prefix] = TagFile(subont_path)
              continue
            warning("unsupported name, does not end in .sqlite or .tags")
            continue
      tagsets = ont_pfx_map.pop('_', None)
    else:
      if not ont_path.endswith('.sqlite'):
        ont_path_sqlite = ont_path + '.sqlite'
        if isfilepath(ont_path_sqlite):
          return cls.tagsetses_from_path(ont_path_sqlite)
      raise ValueError(f"unsupported ont_path={ont_path!r}")
    return tagsets, ont_pfx_map

if __name__ == '__main__':
  sys.exit(main(sys.argv))
