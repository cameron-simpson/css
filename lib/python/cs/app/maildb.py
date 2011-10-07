#!/usr/bin/python -tt

from __future__ import with_statement
from cs.logutils import setup_logging, Pfx, info, warn, error
from cs.mail import ismaildir, ismbox, messagesFromPath
from cs.nodedb import NodeDB, Node, NodeDBFromURL
from cs.misc import the
from collections import deque
from getopt import getopt, GetoptError
from email.utils import getaddresses, parseaddr, formataddr
from itertools import chain
import logging
import sys
import os
import unittest

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = '''Usage:
    %s [-m mdburl] op [op-args...]
    Ops:
      import-addresses < addresses.txt
      list-groups [groups...]''' \
    % (cmd,)
  setup_logging(cmd)

  xit = 0
  badopts = False
  mdburl = None

  try:
    opts, args = getopt(argv[1:], 'm:')
  except GetoptError, e:
    error("unrecognised option: %s: %s"% (e.opt, e.msg))
    badopts = True
    opts, args = [], []

  for opt, val in opts:
    if opt == '-m':
      mdburl = val
    else:
      raise GetoptError("unrecognised option: %s", opt)

  if mdburl is None:
    mdburl = os.environ['MAILDB']

  if len(argv) == 0:
    error("missing op")
    badopts = True
  else:
    mdb = MailDB(mdburl, readonly=False)
    op = argv.pop(0)
    with Pfx(op):
      if op == 'import-addresses':
        if sys.stdin.isatty():
          error("stdin is a tty, file expected")
          xit=2
        else:
          mdb.importAddresses(sys.stdin)
      elif op == 'list-groups':
        if len(argv):
          groups = argv
        else:
          groups = sorted([ G.name for G in mdb.nodesByType('ADDRESS_GROUP') ])
        for group in groups:
          G = mdb.getAddressGroupNode(group)
          if not G:
            error("no such group: %s", group)
            xit = 1
            continue
          print group, ", ".join(A.formatted for A in G.ADDRESSes)
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    error(usage)
    xit = 2

  return xit

class AddressNode(Node):

  @property
  def formatted(self):
    return formataddr( (self.REALNAME, self.name) )

  @property
  def realname(self):
    return self.REALNAME

class MessageNode(Node):

  @property
  def references(self):
    return self.REFERENCEs

  @property
  def followups(self):
    return set( N for N, attr, count in self.references(attr='PARENT', type='MESSAGE') )

  def thread_root(self):
    M = self
    while True:
      ps = M.PARENTs
      if not ps:
        return M
      M = ps[0]
      if M is self:
        raise ValueError, "%s: recursive PARENTs" % (M,)

  def thread_walk(self, depthFirst=False):
    ''' Walk the threads from this message yielding MessageNodes.
        By default messages are returned in breadthfirst order
        unless the optional parameter `depthFirst` is true.
    '''
    seen = set()
    msgq = deque()
    msgq.append(self)
    while msgq:
      M = msgq.popleft()
      if M in seen:
        continue
      yield M
      seen.add(M)
      for M2 in M.followups:
        if depthFirst:
          msgq.appendleft(M2)
        else:
          msgq.append(M2)

TypeFactory = { 'MESSAGE':      MessageNode,
                'ADDRESS':      AddressNode,
              }

def MailDB(mdburl, readonly=True, klass=None):
  if klass is None:
    klass = _MailDB
  return NodeDBFromURL(mdburl, readonly=readonly, klass=klass)

class _MailDB(NodeDB):
  ''' Extend NodeDB for email.
  '''

  def _createNode(self, t, name):
    ''' Create a new Node of the specified type.
    '''
    if t in TypeFactory:
      return TypeFactory[t](t, name, self)
    return NodeDB._createNode(self, t, name)

  def getAddressNode(self, addr):
    ''' Obtain the AddressNode for the specified address `addr`.
        If the optional parameter `name` is specified and
        the Node has no .REALNAME attribute, sets .REALNAME to `name`.
    '''
    if type(addr) is str:
      realname, coreaddr = parseaddr(addr)
    else:
      realname, coreaddr = addr
    coreaddr = coreaddr.lower()
    if  len(coreaddr) == 0:
      raise ValueError("core(%s) => %s" % (`addr`, `coreaddr`))
    A = self.get( ('ADDRESS', coreaddr), doCreate=True)
    if 'REALNAME' not in A or not len(A.REALNAME):
      A.REALNAME = realname
    return A

  def getAddressGroupNode(self, group, default=None):
    return self.get(('ADDRESS_GROUP', group), default)

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
    return [ self.getAddressNode( (realname, addr), doCreate=True)
             for realname, addr
             in getaddresses(addrtexts)
           ]

  def importMessage(self, msg):
    ''' Import the message `msg`.
        Returns the MessageNode.
    '''
    info("import %s->%s: %s" % (msg['from'], msg['to'], msg['subject']))

    msgid = msg['message-id'].strip()
    if ( not msgid.startswith('<')
      or not msgid.endswith('>')
      or msgid.find("@") < 0
       ):
      raise ValueError, "invalid Message-ID: %s" % (msgid,)

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
    refhdr = None
    try:
      refhdr = msg['in-reply-to']
    except KeyError:
      try:
        refhdr = msg['references']
      except KeyError:
        pass
    if refhdr:
      refids = [ msgid for msgid in refhdr.split() if len(msgid) ]
      if refids:
        M.PARENT = self.getMessageNode(refids[-1])

    return M

  def importAddresses(self, fp):
    ''' Import addresses into groups from the file `fp`.
        Import lines are of the form:
          group[,...] email address
    '''
    with Pfx(str(fp)):
      lineno = 0
      for line in fp:
        lineno += 1
        with Pfx(str(lineno)):
          assert line.endswith('\n'), "unexpected EOF" % (lineno,)
          groups, addr = line.strip().split(None, 1)
          if not len(addr):
            info("SKIP - no address")
          ##print groups, addr
          try:
            A = self.getAddressNode(addr)
          except ValueError, e:
            error("bad address: %s: %s", addr, e)
            continue
          for group in groups.split(','):
            G = self.get("ADDRESS_GROUP:"+group, doCreate=True)
            G.ADDRESSes.add(A)


if __name__ == '__main__':
  sys.exit(main(list(sys.argv)))
