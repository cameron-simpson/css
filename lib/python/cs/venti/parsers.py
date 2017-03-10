#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

from cs.logutils import X
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
  chunkQ = IterableQueue()
  yield chunkQ
  offset = 0
  for line in linesof(chunks):
    next_offset = None
    chunkQ.put(line)
    for prefix in prefixes:
      if line.startswith(prefix):
        next_offset = offset
        break
    if next_offset is not None:
      ##X("offset %d: %r", line.rstrip())
      X("yield next_offset:%d", next_offset)
      yield next_offset
    offset += len(line)
  X("yield final offset:%d", offset)
  yield offset
  X("close output chunkQ")
  chunkQ.close()
  X("exit parse_text")

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

PREFIXES_ALL = (
    PREFIXES_MAIL
    + PREFIXES_PYTHON
    + PREFIXES_GO
    + PREFIXES_PERL
    + PREFIXES_SH
)
