#!/usr/bin/env python3

''' An ontology command line tool for cs.fstags or cs.sqltags stores.
'''

from contextlib import contextmanager
import os
from os.path import expanduser, isfile as isfilepath
import sys
from typeguard import typechecked
from cs.context import stackattrs
from cs.fstags import TagFile
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.tagset import TagsOntology, TagsOntologyCommand

ONTTAGS_PATH_DEFAULT = '~/var/ontology.sqlite'
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
    tagsets = self.tagsets_from_path(ont_path)
    super().__init__(tagsets)

  @staticmethod
  def tagsets_from_path(ont_path):
    ''' Return a `BaseTagSets` instance from `ont_path`,
        provided for subclassing.
    '''
    if not isfilepath(ont_path):
      raise ValueError("not a file: %r" % (ont_path,))
    if ont_path.endswith('.sqlite'):
      tagsets = SQLTags(ont_path)
    else:
      tagsets = TagFile(ont_path)
    return tagsets

if __name__ == '__main__':
  sys.exit(main(sys.argv))
