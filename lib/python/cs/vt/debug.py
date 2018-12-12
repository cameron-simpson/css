#!/usr/bin/python

''' Assorted debugging assistance functions.
'''

from binascii import hexlify
from cs.fileutils import shortpath
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
  X("%s%r", indent, E)
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

def dump_Store(S, indent=''):
  from .cache import FileCacheStore
  from .store import MappingStore, ProxyStore, DataDirStore
  X("%s%s:%s", indent, type(S).__name__, S.name)
  indent += '  '
  if isinstance(S, DataDirStore):
    X("%sdir = %s", indent, shortpath(S._datadir.statedirpath))
  elif isinstance(S, FileCacheStore):
    X("%sdatadir = %s", indent, shortpath(S.cache.dirpath))
  elif isinstance(S, ProxyStore):
    for attr in 'save', 'read', 'save2', 'read2', 'copy2':
      backends = getattr(S, attr)
      if backends:
        backends = sorted(backends, key=lambda S: S.name)
        X("%s%s = %s", indent, attr, ','.join(backend.name for backend in backends))
        for backend in backends:
          dump_Store(backend, indent + '  ')
  elif isinstance(S, MappingStore):
    mapping = S.mapping
    X("%smapping = %s", indent, type(mapping))
  else:
    X("%sUNRECOGNISED Store type", indent)
