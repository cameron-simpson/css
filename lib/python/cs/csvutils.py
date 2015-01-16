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

DISTINFO = {
    'description': "CSV file related facilities",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.fileutils', 'cs.debug', 'cs.logutils', 'cs.queues'],
}

import csv
import sys
from StringIO import StringIO
from threading import Thread
from cs.debug import trace
from cs.fileutils import SharedAppendLines
from cs.logutils import warning
from cs.queues import IterableQueue

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

class SharedCSVFile(SharedAppendLines):

  def __init__(self, pathname, readonly=False, **kw):
    importer = kw.get('importer')
    if importer is not None:
      kw['importer'] = lambda line: self._queue_csv_text(line, importer)
    self._csv_partials = []
    self._importQ = IterableQueue(1, name="SharedCSVFile(%r)._importQ" % (pathname,))
    self._csvr = csv_reader(self._importQ)
    self._csv_stream_thread = Thread(target=self._csv_stream,
                                     name="SharedCSVFile(%r)._csv_stream_thread" % (pathname,),
                                     args=(importer,))
    self._csv_stream_thread.daemon = True
    self._csv_stream_thread.start()
    self._stringio = StringIO()
    SharedAppendLines.__init__(self, pathname, no_update=readonly, **kw)

  def _queue_csv_text(self, line, importer):
    ''' Importer for SharedAppendLines: convert to row from CSV data, pass to real importer.
    '''
    if line is None:
      importer(None)
    else:
      self._importQ.put(line)

  def _csv_stream(self, importer):
    for row in self._csvr:
      importer(row)

  def transcribe_update(self, fp, row):
    ''' Transcribe an update `row` to the supplied file `fp`.
    '''
    # sanity check: we should only be writing between foreign updates
    # and foreign updates should always be complete lines
    if len(self._csv_partials):
      warning("%s._transcribe_update while non-empty partials[]: %r",
              self, self._csv_partials)
    sfp = self._stringio
    try:
      csv_writerow(csv.writer(sfp), row)
    except IOError as e:
      warning("%s: IOError %s: discarding %s", sys.argv[0], e, row)
    else:
      fp.write(sfp.getvalue())
    sfp.flush()
