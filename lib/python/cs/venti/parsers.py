#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

import sys
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
  offset = 0
  for line in linesof(chunks):
    yield line
    next_offset = None
    for prefix in prefixes:
      if line.startswith(prefix):
        next_offset = offset
        break
    if next_offset is not None:
      yield next_offset
    offset += len(line)

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
