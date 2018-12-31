#!/usr/bin/python
#
# Block tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Unit testst for cs.vt.block.
'''

import sys
from random import choice
import unittest
from cs.binary_tests import _TestPacketFields
from .randutils import rand0, randblock
from . import totext, block as block_module
from .block import Block, \
    IndirectBlock, \
    RLEBlock, LiteralBlock, SubBlock, \
    verify_block, BlockType, \
    BlockRecord
from .store import MappingStore

class TestDataFilePacketFields(_TestPacketFields, unittest.TestCase):
  ''' Hook to test the hash PacketFields.
  '''

  def setUp(self):
    ''' Test the block module PacketField classes.
    '''
    self.module = block_module

class TestAll(unittest.TestCase):
  ''' All Block tests.
  '''

  def setUp(self):
    ''' Make a dict backed MappingStore.
    '''
    self.S = MappingStore("TestAll", {})

  def _verify_block(self, B, **kw):
    with self.subTest(task="_verify_block", block=B, **kw):
      BR = BlockRecord(B)
      BRbs = bytes(BR)
      BR2, offset = BlockRecord.from_bytes(BRbs)
      self.assertEqual(offset, len(BRbs))
      self.assertEqual(BR, BR2)
      for err in verify_block(B):
        raise ValueError("verify_block(%s): %s" % (B, err))
      errs = list(verify_block(B, **kw))
      self.assertEqual(errs, [])

  def _make_random_Block(self, block_type=None, size=None, leaf_only=False):
    with self.subTest(
        task="_make_random_Block",
        block_type=block_type, size=size, leaf_only=leaf_only
    ):
      if block_type is None:
        choices = [
            BlockType.BT_HASHCODE,
            BlockType.BT_RLE,
            BlockType.BT_LITERAL,
        ]
        if not leaf_only:
          choices.append(BlockType.BT_SUBBLOCK)
          choices.append(BlockType.BT_INDIRECT)
        block_type = choice(choices)
      if size is None:
        size = rand0(16385)
      with self.subTest(
          subtask="instantiate",
          block_type=block_type, size=size,
      ):
        if block_type == BlockType.BT_INDIRECT:
          subblocks = [self._make_random_Block() for _ in range(rand0(8))]
          B = IndirectBlock(subblocks, force=True)
        elif block_type == BlockType.BT_HASHCODE:
          rs = randblock(size)
          B = Block(data=rs)
          # we can get a literal block back - this is acceptable
          if B.type == BlockType.BT_LITERAL:
            block_type = BlockType.BT_LITERAL
        elif block_type == BlockType.BT_RLE:
          rb = bytes((rand0(256),))
          B = RLEBlock(size, rb)
        elif block_type == BlockType.BT_LITERAL:
          rs = randblock(size)
          B = LiteralBlock(data=rs)
        elif block_type == BlockType.BT_SUBBLOCK:
          B2 = self._make_random_Block()
          self._verify_block(B2)
          if len(B2) == 0:
            suboffset = 0
            subspan = 0
          else:
            suboffset = rand0(B2.span)
            subspan = rand0(B2.span - suboffset)
          B = SubBlock(B2, suboffset, subspan)
          # SubBlock returns an empty literal for an empty subblock
          if subspan == 0:
            block_type = BlockType.BT_LITERAL
        else:
          raise ValueError("unknow block type")
        self.assertEqual(
            B.type, block_type,
            "new Block is wrong type: %r, should be %r"
            % (B.type, block_type,))
        self._verify_block(B)
      return B

  def test00Block(self):
    ''' Make some random blocks, check size and content.
    '''
    with self.S:
      for _ in range(16):
        size = rand0(16385)
        rs = randblock(size)
        self.assertEqual(len(rs), size)
        B = Block(data=rs)
        self._verify_block(B)
        self.assertEqual(len(B), size)
        self.assertEqual(B.span, size)
        self.assertEqual(B.get_spanned_data(), rs)

  def test10IndirectBlock(self):
    ''' Construct various random indirect blocks and test.
    '''
    S = self.S
    with S:
      for _ in range(64):
        with self.subTest(loop=_):
          chunks = []
          subblocks = []
          total_length = 0
          for _ in range(rand0(16)):
            B = self._make_random_Block()
            subblocks.append(B)
            total_length += B.span
            chunks.append(B.get_spanned_data())
          fullblock = b''.join(chunks)
          IB = IndirectBlock(subblocks=subblocks, force=True)
          self._verify_block(IB, recurse=True)
          IBspan = IB.span
          self.assertEqual(
              IBspan, total_length,
              "IBspan(%d) != total_length(%d)" % (IB.span, total_length))
          IBH = IB.superblock.hashcode
          IBdata = IB.get_spanned_data()
          self.assertEqual(len(IBdata), total_length)
          self.assertEqual(IBdata, fullblock)
          # refetch block by hashcode
          IB2 = IndirectBlock(hashcode=IBH, span=len(IBdata))
          self._verify_block(IB2, recurse=True)
          IB2data = IB2.get_spanned_data()
          self.assertEqual(
              IBdata, IB2data,
              "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))
          for _ in range(32):
            with self.subTest(loop2=_):
              start = rand0(len(IB) + 1)
              length = rand0(len(IB) - start + 1) if start < len(IB) else 0
              end = start + length
              with self.subTest(start=start, end=end):
                chunk1 = IB[start:end]
                self.assertEqual(len(chunk1), length)
                chunk1a = fullblock[start:end]
                self.assertEqual(len(chunk1a), length)
                self.assertEqual(
                    chunk1, chunk1a,
                    "IB[%d:%d] != fullblock[%d:%d]" % (start, end, start, end))
                chunk2 = IB2[start:end]
                self.assertEqual(len(chunk2), length)
                self.assertEqual(
                    chunk1, chunk2,
                    "IB[%d:%d] != IB2[%d:%d]" % (start, end, start, end))

  def test02RoundTripSingleBlock(self):
    ''' Generate various block types, serialise then deserialise each.
    '''
    S = self.S
    with S:
      for block_type in BlockType.BT_HASHCODE, BlockType.BT_RLE, \
                        BlockType.BT_LITERAL, BlockType.BT_SUBBLOCK, \
                        BlockType.BT_INDIRECT:
        size = rand0(16385)
        with self.subTest(type=block_type, size=size):
          B = self._make_random_Block(block_type=block_type)
          Bserial = B.encode()
          B2, offset = BlockRecord.value_from_bytes(Bserial)
          self.assertEqual(
              offset, len(Bserial),
              "decoded %d bytes but len(Bserial)=%d" % (offset, len(Bserial)))
          self._verify_block(B2)
          if block_type != BlockType.BT_INDIRECT:
            self.assertEqual(B.type, B2.type, "block types differ")
            self.assertEqual(B.indirect, B2.indirect, "block indirects differ")
          self.assertEqual(B.span, B2.span, "span lengths differ")
          self.assertEqual(
                  B.get_spanned_data(),
                  B2.get_spanned_data(),
                  "spanned data differ")
          Btype = B2.type
          if Btype == BlockType.BT_INDIRECT:
            self.assertTrue(B.indirect)
            self._verify_block(B2.superblock)
          else:
            self.assertFalse(B.indirect)
            self.assertEqual(B.span, sum(len(chunk) for chunk in B.datafrom()))
            if Btype == BlockType.BT_HASHCODE:
              self.assertEqual(B.hashcode, B2.hashcode)
            elif Btype == BlockType.BT_RLE:
              self.assertEqual(B2.get_spanned_data(), B2.octet * B2.span)
            elif Btype == BlockType.BT_LITERAL:
              raise unittest.SkipTest("no specific test for LiteralBlock")
            elif Btype == BlockType.BT_SUBBLOCK:
              self._verify_block(B2.superblock)
            else:
              raise unittest.SkipTest(
                  "no type specific tests for Block type %r" % (block_type,))

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
