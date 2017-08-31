#!/usr/bin/python

from cs.lex import hexify, texthexify
from cs.logutils import X
from .block import BlockType
from .dir import InvalidDirent

def dump_Block(block, indent=''):
  X("%s%s %s %d bytes",
    indent,
    hexify(block.hashcode),
    "indirect" if block.indirect else "direct",
    len(block))
  if block.indirect:
    indent += '  '
    subblocks = block.subblocks()
    X("%sindirect %d subblocks, span %d bytes",
      indent, len(subblocks), len(block))
    for B in subblocks:
      dump_Block(B, indent=indent)

def dump_Dirent(E, indent='', recurse=False, not_dir=False):
  if isinstance(E, InvalidDirent):
    details = '<INVALID:%s:%s>' % (E.components, texthexify(E.chunk))
  elif E.issym:
    details = '-> ' + repr(E.pathref)
  elif E.ishardlink:
    details = 'inode ' + str(E.inum)
  elif E.block.type == BlockType.BT_LITERAL:
    details = "literal(%r)" % (E.block.data,)
  else:
    details = hexify(E.block.hashcode)
  if E.isfile:
    details += " %s  size=%d meta=%s block=%s" % (indent, len(E.block), E.meta.textencode(), E.block)
  X("%s%s %r %s",
    indent,
    'd' if E.isdir else '-',
    E.name,
    details,
  )
  if E.isdir and not not_dir:
    indent += '  '
    for name in sorted(E.keys()):
      E2 = E[name]
      dump_Dirent(E2, indent, recurse=recurse, not_dir=not recurse)
