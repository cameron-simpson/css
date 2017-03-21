#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

import sys
from cs.iso14496 import parse_chunks as parse_chunks_mp4
from cs.logutils import X, Pfx, PfxThread
from cs.mp3 import framesof as mp3_frames
from cs.queues import IterableQueue

def linesof(chunks):
  ''' Process binary chunks, yield binary lines ending in '\n'.
      The final line might not have a trailing newline.
  '''
  pending = []
  for chunk in chunks:
    mv_chunk = memoryview(chunk)
    upto = 0
    nlpos = chunk.find(b'\n')
    while nlpos >= 0:
      pending.append(mv_chunk[upto:nlpos+1])
      yield b''.join(pending)
      pending = []
      upto = nlpos + 1
      nlpos = chunk.find(b'\n', upto)
    if upto < len(chunk):
      pending.append(mv_chunk[upto:])
  if pending:
    yield b''.join(pending)

def parse_text(chunks, prefixes=None):
  ''' Scan textual data, yielding offsets of lines starting with
      useful prefixes, such as function definitions.
  '''
  with Pfx("parse_text"):
    if prefixes is None:
      prefixes = PREFIXES_ALL
    prefixes = [ ( prefix
                   if isinstance(prefix, bytes)
                   else bytes(prefix)
                        if isinstance(prefix, memoryview)
                        else prefix.encode('utf-8')
                             if isinstance(prefix, str)
                             else prefix
                 )
                 for prefix in prefixes
               ]
    offset = 0
    for line in linesof(chunks):
      next_offset = None
      for prefix in prefixes:
        if line.startswith(prefix):
          next_offset = offset
          break
      if next_offset is not None:
        yield next_offset
      offset += len(line)

def parse_mp3(chunks, offset=0):
  with Pfx("parse_mp3"):
    for frame in mp3_frames(chunks):
      yield offset
      offset += len(frame)

def parse_mp4(chunks):
  ''' Scan ISO14496 input and yield Box start offsets.
  '''
  with Pfx("parse_mp4"):
    offsetQ = IterableQueue()
    def run_parser():
      for B in parse_chunks_mp4(chunks, discard=True, copy_offsets=offsetQ.put):
        pass
      offsetQ.close()
    T = PfxThread(target=run_parser)
    T.start()
    return offsetQ

PREFIXES_MAIL = ( 'From ', '--' )
PREFIXES_PYTHON = (
    'def ', '  def ', '    def ', '\tdef ',
    'class ', '  class ', '    class ', '\tclass ',
)
PREFIXES_GO = (
    'func ',
)
PREFIXES_PERL = (
    'package ', 'sub ',
)
PREFIXES_SH = (
    'function ',
)
PREFIXES_SQL_DUMP = (
    'INSERT INTO ',
    'DROP TABLE ',
    'CREATE TABLE ',
)

PREFIXES_ALL = (
    PREFIXES_MAIL
    + PREFIXES_PYTHON
    + PREFIXES_GO
    + PREFIXES_PERL
    + PREFIXES_SH
    + PREFIXES_SQL_DUMP
)
