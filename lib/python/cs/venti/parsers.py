#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

import sys
from cs.buffer import CornuCopyBuffer
from cs.logutils import X, Pfx, PfxThread
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

def report_offsets(run_parser, chunks, offset=0):
  ''' Dispatch a parser in a separate Thread, return an IterableQueue yielding offsets.
      `run_parser`: a callable with runs the parser; it should
        accept a CornuCopyBuffer as its sole argument.
      `chunks`: an iterable yielding parser input data chunks.
      `offset`: initial logical offset for the buffer, default 0.
      This function allocates an IterableQueue to receive the parser offset
      reports and a CornuCopyBuffer for the parser with report_offset copying
      offsets to the queue.
  '''
  offsetQ = IterableQueue()
  bfr = CornuCopyBuffer(chunks, offset=offset, copy_offsets=offsetQ.put)
  def thread_body():
    run_parser(bfr)
    offsetQ.close()
  T = PfxThread(target=thread_body)
  T.start()
  return offsetQ

def parse_mp3(chunks, offset=0):
  from cs.mp3 import framesof as parse_mp3_from_buffer
  with Pfx("parse_mp3"):
    def run_parser(bfr):
      for frame in parse_mp3_from_buffer(bfr):
        pass
    return report_offsets(run_parser, chunks, offset=offset)

def parse_mp4(chunks, offset=0):
  ''' Scan ISO14496 input and yield Box start offsets.
  '''
  from cs.iso14496 import parse_buffer as parse_mp4_from_buffer
  with Pfx("parse_mp4"):
    def run_parser(bfr):
      for B in parse_mp4_from_buffer(bfr, discard=True):
        pass
    return report_offsets(run_parser, chunks, offset=offset)

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
