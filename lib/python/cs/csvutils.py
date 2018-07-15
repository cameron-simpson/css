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
from cs.deco import strable
from cs.logutils import warning
from cs.mappings import named_column_tuples
from cs.pfx import Pfx

DISTINFO = {
    'description': "CSV file related facilities",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.logutils', 'cs.mappings', 'cs.pfx' ],
}

if sys.hexversion >= 0x03000000:
  # python 3 onwards

  @strable
  def csv_reader(fp, encoding='utf-8', errors='replace', **kw):
    ''' Read the file `fp` using csv.reader.
        `fp` may also be a filename.
        Yield the rows.
        Warning: _ignores_ the `encoding` and `errors` parameters
        because `fp` should already be decoded.
    '''
    return csv.reader(fp, **kw)

  def csv_writerow(csvw, row, encoding='utf-8'):
    ''' Write the supplied row as strings encoded with the supplied `encoding`,
        default 'utf-8'.
    '''
    with Pfx("csv_writerow(csvw=%s, row=%r, encoding=%r)", csvw, row, encoding):
      return csvw.writerow(row)

else:
  # python 2 compatability code

  @strable
  def csv_reader(fp, encoding='utf-8', errors='replace', **kw):
    ''' Read the file `fp` using csv.reader and decode the str
        fields into unicode using the supplied `encoding`,
        default "utf-8".
        `fp` may also be a filename.
        Yield the rows after decoding.
    '''
    r = csv.reader(fp, **kw)
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

def csv_import(fp, class_name=None, column_names=None, **kw):
  ''' Read CSV data where the first row contains column headers,
      yield named tuples for subsequent rows.

      `fp`: a file object containing CSV data, or the name of such a file
      `class_name`: optional class name for the namedtuple subclass
        used for the row data.
      `column_names`: optional iterable of column headings; if
        provided then the file is not expected to have internal column
        headings

      Example:
        >>> list(csv_import(['a, b', '1,2', '3,4'], class_name='Example_AB'))   #doctest: +ELLIPSIS
        [<function named_row_tuple.<locals>.factory at ...>, Example_AB(a='1', b='2'), Example_AB(a='3', b='4')]
        >>> list(csv_import(['1,2', '3,4'], class_name='Example_DEFG', column_names=['D E', 'F G ']))   #doctest: +ELLIPSIS
        [<function named_row_tuple.<locals>.factory at ...>, Example_DEFG(d_e='1', f_g='2'), Example_DEFG(d_e='3', f_g='4')]
  '''
  return named_column_tuples(csv_reader(fp, **kw), class_name=class_name, column_names=column_names)
