#!/usr/bin/python

''' Assorted debugging assistance functions.
'''

from binascii import hexlify
from cs.tty import ttysize
from cs.x import X

def dump_Block(block, indent=''):
  ''' Dump a Block.
  '''
  X("%s%s", indent, block)
  if block.indirect:
    indent += '  '
    subblocks = block.subblocks
    X("%sindirect %d subblocks, span %d bytes",
      indent, len(subblocks), len(block))
    for B in subblocks:
      dump_Block(B, indent=indent)

def dump_Dirent(E, indent='', recurse=False, not_dir=False):
  ''' Dump a Dirent.
  '''
  X("%s%s", indent, E)
  if E.isdir and not not_dir:
    indent += '  '
    for name in sorted(E.keys()):
      E2 = E[name]
      dump_Dirent(E2, indent, recurse=recurse, not_dir=not recurse)

def dump_chunk(data, leadin, max_width=None, one_line=False):
  ''' Dump a data chunk.
  '''
  if max_width is None:
    _, columns = ttysize(1)
    if columns is None:
      columns = 80
    max_width = columns - 1
  leadin += ' %5d' % (len(data),)
  leadin2 = ' ' * len(leadin)
  data_width = max_width - len(leadin)
  slice_size = (data_width - 1) // 3
  assert slice_size > 0
  doff = 0
  while doff < len(data):
    doff2 = doff + slice_size
    chunk = data[doff:doff2]
    hex_text = hexlify(chunk).decode('utf-8')
    txt_text = ''.join(
        c if c.isprintable() else '.'
        for c in chunk.decode('iso8859-1')
    )
    print(leadin, txt_text, hex_text)
    if one_line:
      break
    leadin = leadin2
    doff = doff2
