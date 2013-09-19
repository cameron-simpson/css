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
from cs.io import CatchupLines

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

class CatchUp(object):
  ''' A CSV layer to cs.io.CatchupLines.
      It is iterable, yields CSV data rows.
      At the end of iteration the .partial attribute contains any
      incomplete line.
      It is reusable; another iteration will commence with that
      partial line.
  '''

  def __init__(self, fp, partial=''):
    ''' Initialise the CatchUp with an open file `fp` and optional
        partial line `partial`.
    '''
    self.fp = fp
    self.partial = partial

  def __iter__(self):
    self.lines = CatchupLines(self.fp, self.partial)
    for row in csv_reader(self.lines):
      yield row
    self.partial = self.lines.partial

  def rewind(self):
    self.fp.seek(0, os.SEEK_SET)
    self.partial = ''
