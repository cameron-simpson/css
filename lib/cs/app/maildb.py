#!/usr/bin/python -tt

from __future__ import with_statement
from cs.nodedb import NodeDB, Node
from cs.mail import ismaildir, ismbox, messagesFromPath
from cs.misc import the
from email.utils import getaddresses, formataddr
from contextlib import closing
from types import StringTypes
import sys
import os

AddressNode=Node
PersonNode=Node
class MessageNode(Node):
  def message_id(self):
    return self.MESSAGEID

TypeFactory = { 'MESSAGE':      MessageNode,
                'PERSON':       PersonNode,
                'ADDRESS':      AddressNode,
              }

class MailDB(NodeDB):
  ''' Extend NodeDB for email.
  '''

  def __init__(self, engine=None, nodes=None, attrs=None):
    if engine is None:
      HOME=os.environ['HOME']
      MAILDIR=os.environ.get('MAILDIR',
                             os.path.join(HOME,'mail'))
      DBPATH=os.environ.get('MAIL_NODEDB',
                            os.path.join(MAILDIR,'.nodedb.sqlite'))
      engine="sqlite:///"+DBPATH
    print >>sys.stderr, "engine=%s" % (engine,)
    NodeDB.__init__(self,engine,nodes,attrs)

  def _newNode(self, _node, attrs):
    t = _node.TYPE
    if t not in TypeFactory:
      raise ValueError("MailDB: unsupported type \"%s\"" % (t,))
    return TypeFactory[t](_node, self, attrs)

  def getAddrNode(self, addr):
    return self.nodeByNameAndType(addr, 'ADDRESS', doCreate=True)

  def getMessageNode(self, message_id):
    return self.nodeByNameAndType(message_id, 'MESSAGE', doCreate=True)

  def importPath(self, path):
    for M in messagesFromPath(path):
      self.importMessage(M)

  def importMessage(self,M):
    print >>sys.stderr, "import %s->%s: %s" % (M['from'], M['to'], M['subject'])
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
