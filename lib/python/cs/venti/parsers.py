#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

from functools import partial
import sys
from cs.buffer import CornuCopyBuffer, chunky
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

def parse_text(bfr, prefixes=None):
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
    for line in linesof(bfr):
      next_offset = None
      for prefix in prefixes:
        if line.startswith(prefix):
          next_offset = offset
          break
      if next_offset is not None:
        yield next_offset
      offset += len(line)

parse_text_from_chunks = chunky(parse_text)

def report_offsets(bfr, parser):
  ''' Dispatch a parser in a separate Thread, return an IterableQueue yielding offsets.
      `bfr`: a CornuCopyBuffer
      `run_parser`: a callable with runs the parser; it should
        accept a CornuCopyBuffer as its sole argument.
      This function allocates an IterableQueue to receive the parser offset
      reports and sets the CornuCopyBuffer with report_offset copying
      offsets to the queue.
  '''
  offsetQ = IterableQueue()
  if bfr.copy_offsets is not None:
    warning("bfr %s already has copy_offsets, replacing", bfr)
  bfr.copy_offsets = offsetQ.put
  def thread_body():
    parser(bfr)
    offsetQ.close()
  T = PfxThread(target=thread_body)
  T.start()
  return offsetQ

report_offsets_from_chunks = chunky(report_offsets)

def parse_mp3(bfr):
  from cs.mp3 import framesof as parse_mp3_from_buffer
  with Pfx("parse_mp3"):
    def run_parser(bfr):
      for frame in parse_mp3_from_buffer(bfr):
        pass
    return report_offsets(bfr, run_parser)

parse_mp3_from_chunks = chunky(parse_mp3)

def parse_mp4(bfr):
  ''' Scan ISO14496 input and yield Box start offsets.
  '''
  from cs.iso14496 import parse_buffer as parse_mp4_from_buffer
  with Pfx("parse_mp4"):
    def run_parser(bfr):
      for B in parse_mp4_from_buffer(bfr, discard=True):
        pass
    return report_offsets(bfr, run_parser)

parse_mp4_from_chunks = chunky(parse_mp4)

def parser_from_filename(filename):
  ''' Choose a parser based a filename.
      Returns None if these is no special parser.
  '''
  root, ext = splitext(basename(filename))
  if ext:
    assert ext.startswith('.')
    parser = PARSERS_BY_EXT[lcext]
    if parser is not None:
      return parser
  return None

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

PARSERS_BY_EXT = {
  'go':     partial(parse_text, prefixes=PREFIXES_GO),
  'mp3':    parse_mp3,
  'mp4':    parse_mp4,
  'pl':     partial(parse_text, prefixes=PREFIXES_PERL),
  'pm':     partial(parse_text, prefixes=PREFIXES_PERL),
  'py':     partial(parse_text, prefixes=PREFIXES_PYTHON),
  'sh':     partial(parse_text, prefixes=PREFIXES_SH),
  'sql':    partial(parse_text, prefixes=PREFIXES_SQL_DUMP),
}

PREFIXES_ALL = (
    PREFIXES_MAIL
    + PREFIXES_PYTHON
    + PREFIXES_GO
    + PREFIXES_PERL
    + PREFIXES_SH
    + PREFIXES_SQL_DUMP
)
