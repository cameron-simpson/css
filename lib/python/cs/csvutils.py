#!/usr/bin/python -tt
#
# Utility functions for CSV files, particularly to provide consistent
# decoding in python 2 and 3.
#       - Cameron Simpson <cs@zip.com.au> 02may2013
#
# In python 2 the CSV reader reads 8 bit byte data and returns str objects;
# these need to be decoded into unicode objects.
# In python 3 the CSV reader reads an open text file and returns str
# objects (== unicode).
# So we provide csv_reader() generators to yield rows containing unicode.
#

import csv
import sys
from itertools import takewhile
from cs.debug import trace
from cs.fileutils import SharedAppendFile
from cs.logutils import warning, X
from cs.lex import as_lines

if sys.hexversion < 0x03000000:

  def csv_reader(fp, encoding='utf-8', errors='replace'):
    ''' Read the file `fp` using csv.reader and decode the str
        fields into unicode using the supplied `encoding`,
        default "utf-8".
        Yield the rows after decoding.
    '''
    r = csv.reader(fp)
    for row in r:
      for i in range(len(row)):
        value = row[i]
        if isinstance(value, str):
          # transmute str (== bytes) to unicode
          try:
            value = value.decode(encoding)
          except UnicodeDecodeError as e:
            warning("%s, using errors=%s", e, errors)
            value = value.decode('utf-8', errors=errors)
          row[i] = value
      yield row

  def csv_writerow(csvw, row, encoding='utf-8'):
    ''' Write the supplied row as strings encoded with the supplied `encoding`,
        default 'utf-8'.
    '''
    csvw.writerow([ unicode(value).encode(encoding) for value in row ])

else:

  def csv_reader(fp, encoding='utf-8', errors='replace'):
    ''' Read the file `fp` using csv.reader.
        Yield the rows.
    '''
    return csv.reader(fp)

  def csv_writerow(csvw, row, encoding='utf-8'):
    return csvw.writerow(row)

class SharedCSVFile(SharedAppendFile):

  def __init__(self, pathname, transcribe_update=None, **kw):
    if 'binary' in kw:
      raise ValueError('may not specify binary=')
    if transcribe_update is None:
      transcribe_update = self._transcribe_update
    self._csv_partials = []
    SharedAppendFile.__init__(self, pathname,
                              binary=False, transcribe_update=transcribe_update,
                              **kw)

  def _transcribe_update(self, fp, item):
    ''' Transcribe an update `item` to the supplied file `fp`.
        This the default function passed as SharedAppendFile's transcribe_update parameter.
    '''
    # sanity check: we should only be writing between foreign updates
    # and foreign updates should always be complete lines
    if len(self._csv_partials):
      warning("%s._transcribe_update while non-empty partials[]: %r",
              self, self._csv_partials)
    try:
      csv_writerow(csv.writer(fp), item)
    except IOError as e:
      warning("%s: IOError %s: discarding %s", sys.argv[0], e, row)

  def foreign_rows(self, to_eof=False):
    ''' Generator yielding update rows from other writers.
        `to_eof`: stop when the EOF marker is seen; requires self.eof_markers to be true.
    '''
    if to_eof:
      if not self.eof_markers:
        raise ValueError("to_eof forbidden if not self.eof_markers")
      chunks = takewhile(lambda x: len(x) > 0, self._outQ)
    else:
      chunks = self._outQ
    for row in csv_reader(as_lines(chunks, self._csv_partials)):
      yield row
