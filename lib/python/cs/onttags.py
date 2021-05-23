#!/usr/bin/env python3

''' An ontology command line tool for cs.fstags or cs.sqltags stores.
'''

from contextlib import contextmanager
import os
from os.path import expanduser, isfile as isfilepath
import sys
from cs.context import stackattrs
from cs.fstags import TagFile
from cs.pfx import Pfx
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

  def apply_defaults(self):
    options = self.options
    options.ont_path = os.environ.get(ONTTAGS_PATH_ENVVAR)

  @contextmanager
  def run_context(self):
    options = self.options
    ont_path = options.ont_path
    if ont_path is None:
      ont_path = options.ont_path = expanduser(ONTTAGS_PATH_DEFAULT)
    with Pfx("open %r", ont_path):
      if not isfilepath(ont_path):
        raise ValueError("not a file")
      if ont_path.endswith('.sqlite'):
        te_mapping = SQLTags(ont_path)
      else:
        te_mapping = TagFile(ont_path)
    with te_mapping:
      with TagsOntology(te_mapping) as ont:
        with stackattrs(options, ontology=ont):
          yield

if __name__ == '__main__':
  sys.exit(main(sys.argv))
