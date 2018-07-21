#!/usr/bin/python
#
# Convenience iTunes related functions.
#       - Cameron Simpson <cs@cskk.id.au> 05jan2013
#

''' Convenience iTunes related functions.
'''

from __future__ import print_function
import datetime
from cs.mappings import named_column_tuples
from cs.x import X

def read_playlist(path):
  ''' Read an iTunes playlist file, return a list of entry objects.
  '''
  with open(path, encoding='utf-16le', newline='\r') as plf:
    # skip the BOM
    plf.seek(2)
    def preprocess(context, row):
      ''' Convert some row columns before assimilation.
      '''
      if context.index > 0:
        for i, attr in enumerate(context.cls.attrs_):
          if attr in (
              'bit_rate',
              'disc_count',
              'disc_number',
              'my_rating',
              'plays',
              'sample_rate',
              'size',
              'time',
              'track_count',
              'track_number',
              'year',
          ):
            row[i] = int(row[i]) if row[i] else None
          elif attr in (
              'date_added',
              'date_modified',
              'last_played',
          ):
            row[i] = playlist_date(row[i]) if row[i] else None
      X("row = %r", row)
      return row
    _, entries = named_column_tuples(
        [ line[-1].split('\t') for line in plf ],
        class_name='ApplePlaylistEntry',
        preprocess=preprocess
    )
    entries = list(entries)
  return entries

def playlist_date(s):
  ''' Parse a date time field from an Apple playlist file.
  '''
  return datetime.datetime.strptime( s, '%d/%m/%y %I:%S %p' )

if __name__ == '__main__':
  import sys
  for argv_path in sys.argv[1:]:
    print(argv_path)
    for item in read_playlist(argv_path):
      print(item)
