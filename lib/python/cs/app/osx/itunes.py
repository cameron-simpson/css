#!/usr/bin/python
#
# Convenience iTunes related functions.
#       - Cameron Simpson <cs@zip.com.au>05jan2013
#

import datetime

def read_playlist(path):
  ''' Read an iTunes playlist file, yield dicts.
  '''
  with open(path, encoding='utf-16le', newline='\r') as plf:
    plf.seek(2)
    lineno = 0
    first = True
    for line in plf:
      lineno += 1
      if not line.endswith('\r'):
        raise ValueError("%s:%d: missing end of line" % (path, lineno))
      fields = line[:-1].split('\t')
      if lineno == 1:
        headers = [ hdr.lower().replace(' ', '_') for hdr in fields ]
      else:
        for i in range(len(fields)):
          header = headers[i]
          if header in (
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
            fields[i] = int(fields[i]) if len(fields[i]) else None
          elif header in (
                           'date_added',
                           'date_modified',
                           'last_played',
                         ):
            fields[i] = playlist_date(fields[i]) if len(fields[i]) else None
        yield dict(zip(headers, fields))

def playlist_date(s):
  return datetime.datetime.strptime( s, '%d/%m/%y %I:%S %p' )

if __name__ == '__main__':
  import sys
  for path in sys.argv[1:]:
    print(path)
    for item in read_playlist('/Users/cameron/Documents/tag-mellow.txt'):
      print(item)
