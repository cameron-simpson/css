#!/usr/bin/python -tt

from __future__ import with_statement
from cs.logutils import Pfx, info
from cs.mail import ismaildir, ismbox, messagesFromPath
from cs.nodedb import NodeDB, Node
from cs.misc import the
from email.utils import getaddresses, formataddr
import sys
import os
import unittest

AddressNode=Node
PersonNode=Node

class MessageNode(Node):

  def references(self):
    return [ N for N in self.parentsByAttr('FOLLOWUPS') if N.TYPE == 'MESSAGE' ]
  def followups(self):
    return self.FOLLOWUPS

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

  def getAddrNode(self, addr):
    ''' Obtain the Node for the specified address `addr`.
    '''
    return self.get( ('ADDRESS', addr), doCreate=True)

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

  def importMessage(self, M):
    ''' Import the message `M`.
        Returns the MessageNode.
    '''
    info("import %s->%s: %s" % (M['from'], M['to'], M['subject']))
    N = self.getMessageNode(M['message-id'])
    N.SUBJECT = M['subject']
    if 'date' in M:
      N.DATE = M['date']
    name, addr = the(getaddresses(M.get_all('from')))
    N.FROM = self.getAddrNode(addr.lower())
    addrs = {}
    for hdr in ('to', 'cc', 'resent-to', 'resent-cc'):
      hdrs = M.get_all(hdr)
      if hdrs is None:
        continue
      for name, addr in getaddresses(hdrs):
        addr = addr.lower()
        if addr not in addrs:
          A = self.getAddrNode(addr)
          addrs[addr] = A
          if len(name) > 0 and not hasattr(A, 'REALNAME'):
            A.REALNAME = name
    N.RECIPIENTs = [ addrs[addr] for addr in addrs.keys() ]
    return N

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = MailDB(backend=None)

  def test01makeNodes(self):
    A = self.db.newNode('ADDRESS', 'cs@zip.com.au')
    print >>sys.stderr, "A =", `A`
    print >>sys.stderr, "ADDRESSes =", `self.db.ADDRESSes`

if __name__ == '__main__':
  unittest.main()
