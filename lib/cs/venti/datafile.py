#!/usr/bin/python -tt

from zlib import compress, decompress
from cs.serialise import toBS, fromBSfp
from cs.venti import hash

def scanFile(fp):
  ''' A generator that reads a file storing data blocks.
      These files contain byte sequences of the form:
        BS(zlength)
        zblock
      where zblock is the zlib.compress()ed form of the stored block
      and zlength is the byte length of the zblock.
      The cs.misc.toBS() function is used to represent the length
      as a byte sequence.
  '''
  while True:
    zsize=fromBSfp(fp)
    if zsize is None:
      break
    offset=fp.tell()
    zblock=fp.read(zsize)
    try:
      block=decompress(zblock)
    except:
      continue
    h=hash(block)
    yield h, offset, zsize

def getBlock(fp,offset,zsize):
  ''' Read the zblock from a file at the specified offset.
      Return the decompressed block.
  '''
  fp.seek(offset)
  zblock=fp.read(zsize)
  assert len(zblock) == zsize
  return decompress(zblock)

def addBlock(fp,block,compressed=False):
    ''' Append a block to the specified file.
        If 'compressed' is True, the block is already a zblock.
        Return the offset and size of the zblock.
    '''
    if compressed:
      zblock=block
    else:
      zblock=compress(block)
    fp.seek(0,2)
    fp.write(toBS(len(zblock)))
    offset=fp.tell()
    fp.write(zblock)
    return offset, len(zblock)
