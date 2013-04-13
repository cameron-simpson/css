#!/usr/bin/python
#

''' Classes to support access to Beyonwiz TVWiz data structures.
'''

import sys
import os.path
from collections import namedtuple
import struct
from cs.logutils import Pfx, error, setup_logging

def main(argv):
  args = list(argv)
  cmd = os.path.basename(args.pop(0))
  setup_logging(cmd)
  usage = '''Usage:
    %s  cat tvwizdirs...
    %s  scan tvwizdirs...''' % (cmd, cmd)

  badopts = False

  if not args:
    error("missing operation")
    badopts = True
  else:
    op = args.pop(0)
    with Pfx(op):
      if op == "cat":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      elif op == "scan":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      else:
        error("unrecognised operation")
        badopts = True

  if badopts:
    print >>sys.stderr, usage
    return 2

  xit = 0

  if op == "cat":
    for arg in args:
      TVWiz(arg).copyto(sys.stdout)
  elif op == "scan":
    for arg in args:
      print arg
      total = 0
      chunkSize = 0
      chunkOff = 0
      for wizOffset, fileNum, flags, offset, size in TVWiz(arg).trunc():
        print "  wizOffset=%d, fileNum=%d, flags=%02x, offset=%d, size=%d" \
              % ( wizOffset, fileNum, flags, offset, size )
        total += size
        if chunkOff != wizOffset:
          skip = wizOffset - chunkOff
          if chunkSize == 0:
            print "    %d skipped" % skip
          else:
            print "    %d skipped, after a chunk of %d" % (skip, chunkSize)
          chunkOff = wizOffset
          chunkSize = 0
        chunkOff += size
        chunkSize += size
      if chunkOff > 0:
        print "    final chunk of %d" % chunkSize
      print "  total %d" % total
  else:
    error("unsupported operation: %s" % op)
    xit = 2

  return xit

TruncRecord = namedtuple('TruncRecord', 'wizOffset fileNum flags offset size')

class Trunc(object):
  ''' A parser for the "trunc" file in a TVWiz directory.
      It is iterable, yielding tuples:
        wizOffset, fileNum, flags, offset, size
      as described at:
        http://openwiz.org/wiki/Recorded_Files#trunc_file
  '''
  def __init__(self, path):
    self.path = path

  def __iter__(self):
    ''' The iterator to yield record tuples.
    '''
    fp = open(self.path, "rb")
    while True:
      buf = fp.read(24)
      if len(buf) == 0:
        break
      assert len(buf) == 24
      yield TruncRecord(*struct.unpack("<QHHQL", buf))

class TVWiz(object):
  def __init__(self, wizdir):
    self.dir = wizdir

  def trunc(self):
    ''' Obtain a Trunc object for this TVWiz dir.
    '''
    return Trunc(os.path.join(self.dir, "trunc"))

  def data(self):
    ''' A generator that yields MPEG2 data from the stream.
    '''
    with Pfx("data(%s)", self.dir):
      T = self.trunc()
      lastFileNum = None
      for rec in T:
        wizOffset, fileNum, flags, offset, size  = rec
        if lastFileNum is None or lastFileNum != fileNum:
          if lastFileNum is not None:
            fp.close()
          fp = open(os.path.join(self.dir, "%04d" % (fileNum,)))
          filePos = 0
          lastFileNum = fileNum
        if filePos != offset:
          fp.seek(offset)
        while size > 0:
          rsize = min(size, 8192)
          buf = fp.read(rsize)
          assert len(buf) <= rsize
          if len(buf) == 0:
            error("%s: unexpected EOF", fp)
            break
          yield buf
          size -= len(buf)
      if lastFileNum is not None:
        fp.close()

  def copyto(self, output):
    ''' Transcribe the uncropped content to a file named by output.
    '''
    if type(output) is str:
      with open(output, "w") as out:
        self.copyto(out)
    else:
      for buf in self.data():
        output.write(buf)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
