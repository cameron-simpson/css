#!/usr/bin/python -tt

from __future__ import with_statement
from cs.logutils import Pfx, info, warn, error
from cs.mail import ismaildir, ismbox, messagesFromPath
from cs.nodedb import NodeDB, Node
from cs.misc import the
from email.utils import getaddresses, formataddr
from itertools import chain
import logging
import sys
import os
import unittest

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = '''Usage:
    %s test Run selftests.''' \
    % (cmd,)

  xit = 1
  badopts = False

  if len(argv) == 0:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'test':
        tests = TestAll('test01makeNodes')
        tests()
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    error(usage)
    xit = 2

  return xit

AddressNode=Node
PersonNode=Node

class MessageNode(Node):

  def referers(self):
    return set( N for N, attr, count in self.references(attr='FOLLOWUP', type='MESSAGE') )

  def followups(self):
    return self.FOLLOWUPs

TypeFactory = { 'MESSAGE':      MessageNode,
                'PERSON':       PersonNode,
                'ADDRESS':      AddressNode,
              }

class MailDB(NodeDB):
  ''' Extend NodeDB for email.
  '''

  def _createNode(self, t, name):
    ''' Create a new Node of the specified type.
    '''
    if t not in TypeFactory:
      raise ValueError("unsupported type \"%s\"" % (t,))
    return TypeFactory[t](t, name, self)

  def getAddrNode(self, addr, name=None):
    ''' Obtain the AddressNode for the specified address `addr`.
        If the optional parameter `name` is specified and
        the Node has no .REALNAME attribute, sets .REALNAME to `name`.
    '''
    A = self.get( ('ADDRESS', addr), doCreate=True)
    if name and 'REALNAME' not in A:
      A.NAME = name
    return A

  def getMessageNode(self, message_id):
    ''' Obtain the Node for the specified Message-ID `message_id`.
    '''
    return self.get( ('MESSAGE', message_id), doCreate=True)

  def importPath(self, path):
    ''' Import all the messages stored at `path`.
    '''
    with Pfx(path):
      for M in messagesFromPath(path):
        self.importMessage(M)

  def addrtexts_to_AddressNodes(self, addrtexts):
    return [ self.getAddrNode(addr.lower(), name)
             for name, addr
             in getaddresses(addrtexts)
           ]

  def importMessage(self, msg):
    ''' Import the message `msg`.
        Returns the MessageNode.
    '''
    info("import %s->%s: %s" % (msg['from'], msg['to'], msg['subject']))
    msgid = msg['message-id'].strip()
    M = self.getMessageNode(msgid)
    M.MESSAGE = msg
    M.SUBJECT = msg['subject']
    if 'date' in msg:
      M.DATE = msg['date']
    M.FROMs = self.addrtexts_to_AddressNodes(msg.get_all('from', []))
    addrs = {}
    M.RECIPIENTS = self.addrtexts_to_AddressNodes(
                       chain( msg.get_all(hdr, [])
                              for hdr
                              in ('to', 'cc', 'bcc', 'resent-to', 'resent-cc')
                            )
                     )
    return M

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = MailDB(backend=None)

  def test01makeNodes(self):
    A = self.db.newNode('ADDRESS', 'cs@zip.com.au')
    print >>sys.stderr, "A =", `A`
    print >>sys.stderr, "ADDRESSes =", `self.db.ADDRESSes`

if __name__ == '__main__':
  logging.basicConfig(format="%(message)s")
  sys.exit(main(list(sys.argv)))
