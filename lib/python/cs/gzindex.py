#!/usr/bin/python
#
# Access a gzipped header index file.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import cs.sh

# read mail header index from gzipped file
# a generator that yields (key,[lines]) tuples
def iter(path):
  if os.access(path,os.F_OK):
    fp=cs.sh.vpopen(('gunzip',path),mode='r')
    while True:
      key=fp.readline()
      if len(key) == 0:
        break

      if key[-1] != "\n":
        error(path+": incomplete key line at EOF")
        break

      key=key[:-1]

      lines=[]
      while True:
        line=fp.readline()
        if len(line) == 0 or line[-1] != "\n":
          error(path+": EOF during headers for key: "+key)
          key=None
          break

        if line == "\n":
          break

        lines.append(line)

      if key is None:
        break

      yield (key,lines)

def appendFile(path):
  qpath=cs.sh.quote(path)
  return os.popen('gzip >>'+qpath)

def rewrite(path,ndx,keys=None):
  if keys is None:
    keys=ndx.keys()

  fp=appendFile(path)
  for key in keys:
    fp.write(key)
    fp.write("\n")
    fp.write(str(ndx[key]))
    fp.write("\n")
