#!/usr/bin/python

import sys
from cs.lex import hexify

def dumpBlock(block, indent=''):
  print >>sys.stderr, "%s%s %s %d bytes" \
                      % (indent,
                         hexify(block.hashcode()),
                         "indirect" if block.indirect else "direct",
                         len(block))
  if block.indirect:
    indent += '  '
    subblocks = block.subblocks()
    print >>sys.stderr, "%sindirect %d subblocks, span %d bytes" \
                        % (indent, len(subblocks), len(block))
    for B in subblocks:
      dumpBlock(B, indent=indent)
