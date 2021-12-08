#!/usr/bin/env python3

''' Utilities for working with newline delimiter JSON (NDJSON) files.
'''

import json
from os.path import abspath, exists as existspath, isfile as isfilepath
from threading import RLock

from cs.deco import strable
from cs.fileutils import rewrite_cmgr, gzifopen
from cs.logutils import warning
from cs.mappings import IndexedSetMixin, UUIDedDict
from cs.obj import SingletonMixin
from cs.pfx import Pfx

@strable
def scan_ndjson(f, dictclass=dict, error_list=None):
  ''' Read a newline delimited JSON file, yield instances of `dictclass`
      (default `dict`, otherwise a class which can be instantiated
      by `dictclass(a_dict)`).

      `error_list` is an optional list to accrue `(lineno,exception)` tuples
      for errors encountered during the scan.
  '''
  for lineno, line in enumerate(f, 1):
    with Pfx("line %d", lineno):
      try:
        d = json.loads(line)
      except json.JSONDecodeError as e:
        warning("%s", e)
        if error_list:
          error_list.append((lineno, e))
        continue
      if dictclass is not dict:
        d = dictclass(**d)
    yield d

# pylint: disable=consider-using-with
@strable(open_func=lambda filename: gzifopen(filename, 'w', encoding='utf8'))
def write_ndjson(f, objs):
  ''' Transcribe an iterable of objects to a file as newline delimited JSON.
  '''
  for lineno, o in enumerate(objs, 1):
    with Pfx("line %d", lineno):
      f.write(json.dumps(o, separators=(',', ':')))
      f.write('\n')

# pylint: disable=consider-using-with
@strable(open_func=lambda filename: gzifopen(filename, 'a', encoding='utf8'))
def append_ndjson(f, objs):
  ''' Append an iterable of objects to a file as newline delimited JSON.
  '''
  return write_ndjson(f, objs)

class UUIDNDJSONMapping(SingletonMixin, IndexedSetMixin):
  ''' A subclass of `IndexedSetMixin` which maintains records
      from a newline delimited JSON file.
  '''

  IndexedSetMixin__pk = 'uuid'

  # pylint: disable=unused-argument
  @staticmethod
  def _singleton_key(filename, dictclass=UUIDedDict, create=False):
    ''' Key off the absolute path of `filename`.
    '''
    return abspath(filename)

  def __init__(self, filename, dictclass=UUIDedDict, create=False):
    ''' Initialise the mapping.

        Parameters:
        * `filename`: the file containing the newline delimited JSON data;
          this need not yet exist
        * `dictclass`: a optional `dict` subclass to hold each record,
          default `UUIDedDict`
        * `create`: if true, ensure the file exists
          by transiently opening it for append if it is missing;
          default `False`
    '''
    if hasattr(self, '_lock'):
      return
    self.__ndjson_filename = filename
    self.__dictclass = dictclass
    if create and not isfilepath(filename):
      # make sure the file exists
      with gzifopen(filename, 'a'):  # pylint: disable=unspecified-encoding
        pass
    self.scan_errors = []
    self._lock = RLock()

  def __str__(self):
    return "%s(%r,%s)" % (
        type(self).__name__, self.__ndjson_filename, self.__dictclass.__name__
    )

  def scan(self):
    ''' Scan the backing file, yield records.
    '''
    if existspath(self.__ndjson_filename):
      self.scan_errors = []
      for record in scan_ndjson(self.__ndjson_filename, self.__dictclass,
                                error_list=self.scan_errors):
        yield record

  def add_backend(self, record):
    ''' Append `record` to the backing file.
    '''
    with gzifopen(self.__ndjson_filename, 'a', encoding='utf8') as f:
      f.write(record.as_json())
      f.write('\n')

  def rewrite_backend(self):
    ''' Rewrite the backing file.

        Because the record updates are normally written in append mode,
        a rewrite will be required every so often.
    '''
    with self._lock:
      with rewrite_cmgr(self.__ndjson_filename) as T:
        i = 0
        for i, record in enumerate(self.by_uuid.values(), 1):
          T.write(record.as_json())
          T.write('\n')
        T.flush()
      self.scan_length = i
