#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@cskk.id.au> 05mar2017
#

''' Parsers for various recognised data formats to aid block edge selection.
'''

from functools import partial
from os.path import basename, splitext
from cs.buffer import chunky
from cs.logutils import warning, exception
from cs.pfx import Pfx, PfxThread
from cs.queues import IterableQueue
from .datafile import DataRecord

def linesof(chunks):
  ''' Process binary chunks, yield binary lines ending in '\n'.
      The final line might not have a trailing newline.
  '''
  pending = []
  for chunk in chunks:
    # get a memoryview so that we can cheaply queue bits of it
    mv_chunk = memoryview(chunk)
    upto = 0
    # but scan the chunk, because memoryviews do not have .find
    nlpos = chunk.find(b'\n')
    while nlpos >= 0:
      pending.append(mv_chunk[upto:nlpos + 1])
      yield b''.join(pending)
      pending = []
      upto = nlpos + 1
      nlpos = chunk.find(b'\n', upto)
    # stash incomplete line in pending
    if upto < len(chunk):
      pending.append(mv_chunk[upto:])
  if pending:
    yield b''.join(pending)

def scan_text(bfr, prefixes=None):
  ''' Scan textual data, yielding offsets of lines starting with
      useful prefixes, such as function definitions.
  '''
  with Pfx("scan_text"):
    if prefixes is None:
      prefixes = PREFIXES_ALL
    prefixes = [
        (
            prefix if isinstance(prefix, bytes) else (
                bytes(prefix) if isinstance(prefix, memoryview) else (
                    prefix.encode('utf-8')
                    if isinstance(prefix, str) else prefix
                )
            )
        ) for prefix in prefixes
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

scan_text_from_chunks = chunky(scan_text)

def report_offsets(bfr, run_parser):
  ''' Dispatch a parser in a separate Thread, return an IterableQueue yielding offsets.

      Parameters:
      * `bfr`: a `CornuCopyBuffer` providing data to parse
      * `run_parser`: a callable which runs the parser; it should accept a
        `CornuCopyBuffer` as its sole argument.

      This function allocates an `IterableQueue` to receive the parser offset
      reports and sets the `CornuCopyBuffer` with `report_offset` copying
      offsets to the queue.
      It is the task of the parser to call `bfr.report_offset` as
      necessary to indicate suitable offsets.
  '''
  with Pfx("report_offsets(bfr,run_parser=%s)", run_parser):
    offsetQ = IterableQueue()
    if bfr.copy_offsets is not None:
      warning("bfr %s already has copy_offsets, replacing", bfr)
    bfr.copy_offsets = offsetQ.put

    def thread_body():
      with Pfx("parser-thread"):
        try:
          run_parser(bfr)
        except Exception as e:
          exception("exception: %s", e)
          raise
        finally:
          offsetQ.close()

    T = PfxThread(target=thread_body)
    T.start()
    return offsetQ

report_offsets_from_chunks = chunky(report_offsets)

def scan_vtd(bfr):
  ''' Scan a datafile from `bfr` and yield chunk start offsets.
  '''
  with Pfx("scan_vtd"):

    def run_parser(bfr):
      for offset, _, _ in DataRecord.parse_buffer_with_offsets(bfr):
        bfr.report_offset(offset)

    return report_offsets(bfr, run_parser)

def scan_mp3(bfr):
  ''' Scan MP3 data from `bfr` and yield frame start offsets.
  '''
  from cs.mp3 import MP3Frame
  for frame in MP3Frame.scan(bfr):
    yield bfr.offset

def scan_mp4(bfr):
  ''' Scan ISO14496 input and yield Box start offsets.

      This is more complex than the MP3 scanner because Boxes nest
      in the MP4 structure.
  '''
  from cs.iso14496 import Box
  with Pfx("parse_mp4"):

    def run_parser(bfr):
      for _ in Box.scan(bfr):
        pass

    return report_offsets(bfr, run_parser)

parse_mp4_from_chunks = chunky(scan_mp4)

def scanner_from_filename(filename):
  ''' Choose a scanner based on a filename.
      Returns None if these is no special scanner.
  '''
  _, ext = splitext(basename(filename))
  if ext:
    assert ext.startswith('.')
    parser = SCANNERS_BY_EXT.get(ext[1:].lower())
    if parser is not None:
      return parser
  return None

def scanner_from_mime_type(mime_type):
  ''' Choose a scanner based on a mime_type.
  '''
  return SCANNERS_BY_MIME_TYPE.get(mime_type)

PREFIXES_MAIL = ('From ', '--')
PREFIXES_PYTHON = (
    'def ',
    '  def ',
    '    def ',
    '\tdef ',
    'class ',
    '  class ',
    '    class ',
    '\tclass ',
)
PREFIXES_GO = ('func ',)
PREFIXES_PERL = (
    'package ',
    'sub ',
)
PREFIXES_PDF = (
    '<<',
    'stream',
)
PREFIXES_SH = ('function ',)
PREFIXES_SQL_DUMP = (
    'INSERT INTO ',
    'DROP TABLE ',
    'CREATE TABLE ',
)

SCANNERS_BY_EXT = {
    'go': partial(scan_text, prefixes=PREFIXES_GO),
    'mp3': scan_mp3,
    'mp4': scan_mp4,
    'pdf': partial(scan_text, prefixes=PREFIXES_PDF),
    'pl': partial(scan_text, prefixes=PREFIXES_PERL),
    'pm': partial(scan_text, prefixes=PREFIXES_PERL),
    'py': partial(scan_text, prefixes=PREFIXES_PYTHON),
    'sh': partial(scan_text, prefixes=PREFIXES_SH),
    'sql': partial(scan_text, prefixes=PREFIXES_SQL_DUMP),
    'vtd': scan_vtd,
}

SCANNERS_BY_MIME_TYPE = {
    'text/x-go': partial(scan_text, prefixes=PREFIXES_GO),
    'audio/mpeg': scan_mp3,
    'video/mp4': scan_mp4,
    'text/x-perl': partial(scan_text, prefixes=PREFIXES_PERL),
    'text/x-python': partial(scan_text, prefixes=PREFIXES_PYTHON),
    'text/x-sh': partial(scan_text, prefixes=PREFIXES_SH),
}

PREFIXES_ALL = (
    PREFIXES_MAIL + PREFIXES_PYTHON + PREFIXES_GO + PREFIXES_PERL +
    PREFIXES_SH + PREFIXES_SQL_DUMP
)
