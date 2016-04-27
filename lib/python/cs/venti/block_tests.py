#!/usr/bin/python
#
# Self tests for cs.venti.block.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from cs.py3 import bytes
from . import totext
from .block import Block, IndirectBlock, RLEBlock, LiteralBlock, \
                verify_block, \
                BlockType, \
                encodeBlock, decodeBlock
from .cache import MemoryCacheStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MemoryCacheStore()

  def _verify_block(self, B, **kw):
    errs = list(verify_block(B, **kw))
    self.assertEqual(errs, [])
    
  def test00Block(self):
    # make some randbom blocks, check size and content
    with self.S:
      for _ in range(10):
        size = rand0(16384)
        rs = randblock(size)
        self.assertEqual(len(rs), size)
        B = Block(data=rs)
        self._verify_block(B)
        self.assertEqual(len(B), size)
        self.assertEqual(B.span, size)
        self.assertEqual(B.data, rs)
        self.assertEqual(B.all_data(), rs)

  def test10IndirectBlock(self):
    S = self.S
    with S:
      for _ in range(8):
        fullblock = bytes(())
        subblocks = []
        total_length = 0
        for _ in range(rand0(16)):
          size = rand0(16384)
          rs = randblock(size)
          total_length += len(rs)
          B = Block(data=rs)
          subblocks.append(B)
          fullblock += rs
        IB = IndirectBlock(subblocks=subblocks)
        self._verify_block(IB, recurse=True)
        IBspan = IB.span
        self.assertEqual(IBspan, total_length)
        IBH = IB.hashcode
        IBdata = IB.all_data()
        self.assertEqual(len(IBdata), total_length)
        self.assertEqual(IBdata, fullblock)
        # refetch block by hashcode
        IB2 = IndirectBlock(hashcode=IBH)
        self._verify_block(IB2, recurse=True)
        IB2data = IB2.all_data()
        self.assertEqual(IBdata, IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

  def test02RoundTripSingleBlock(self):
    # TODO: round trip indirect blocks?
    S = self.S
    with S:
      for block_type in BlockType.BT_HASHREF, BlockType.BT_RLE, BlockType.BT_LITERAL:
        size = rand0(16384)
        with self.subTest(type=block_type, size=size):
          if block_type == BlockType.BT_HASHREF:
            rs = randblock(size)
            B = Block(data=rs)
          elif block_type == BlockType.BT_RLE:
            rb = bytes((rand0(256),))
            B = RLEBlock(size, rb)
          elif block_type == BlockType.BT_LITERAL:
            rs = randblock(size)
            B = LiteralBlock(data=rs)
          else:
            raise ValueError("unknow block type %r" % (block_type,))
          if B.type != block_type:
            raise RuntimeError("new Block is wrong type: %r, short be %r" % (B.type, block_type,))
          self._verify_block(B)
          Bserial = encodeBlock(B)
          B2, offset = decodeBlock(Bserial, 0)
          self.assertEqual(offset, len(Bserial), "decoded %d bytes but len(Bserial)=%d" % (offset, len(Bserial)))
          self._verify_block(B2)
          self.assertEqual(B.type, B2.type, "block types differ")
          self.assertEqual(B.indirect, B2.indirect, "block indirects differ")
          self.assertEqual(B.span, B2.span, "span lengths differ")
          self.assertEqual(B.data, B2.data, "spanned data differ")
          if block_type == BlockType.BT_HASHREF:
            self.assertEqual(B.hashcode, B2.hashcode)
          elif block_type == BlockType.BT_RLE:
            self.assertFalse(B2.indirect)
            self.assertEqual(B2.data, B2.octet * B2.span)
          elif block_type == BlockType.BT_LITERAL:
            self.assertFalse(B2.indirect)
          else:
            raise RuntimeError("no type specific tests for Block type %r" % (block_type,))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
