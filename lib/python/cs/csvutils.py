#!/usr/bin/python -tt
#
# Utility functions for CSV files, particularly to provide consistent
# decoding in python 2 and 3.
#       - Cameron Simpson <cs@cskk.id.au> 02may2013
#
# In python 2 the CSV reader reads 8 bit byte data and returns str objects;
# these need to be decoded into unicode objects.
# In python 3 the CSV reader reads an open text file and returns str
# objects (== unicode).
# So we provide csv_reader() generators to yield rows containing unicode.
#

''' Utility functions for CSV files.
'''

from __future__ import absolute_import
import csv
import sys
from cs.logutils import warning
from cs.pfx import Pfx

DISTINFO = {
    'description': "CSV file related facilities",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.pfx' ],
}

if sys.hexversion >= 0x03000000:
  # python 3 onwards

  def csv_reader(fp, encoding='utf-8', errors='replace'):
    ''' Read the file `fp` using csv.reader.
        Yield the rows.
    '''
    return csv.reader(fp)

  def csv_writerow(csvw, row, encoding='utf-8'):
    ''' Write the supplied row as strings encoded with the supplied `encoding`,
        default 'utf-8'.
    '''
    with Pfx("csv_writerow(csvw=%s, row=%r, encoding=%r)", csvw, row, encoding):
      return csvw.writerow(row)

else:
  # python 2 compatability code

  def csv_reader(fp, encoding='utf-8', errors='replace'):
    ''' Read the file `fp` using csv.reader and decode the str
        fields into unicode using the supplied `encoding`,
        default "utf-8".
        Yield the rows after decoding.
    '''
    r = csv.reader(fp)
    for row in r:
      for i, value in enumerate(row):
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
    csvw.writerow([unicode(value).encode(encoding) for value in row])
