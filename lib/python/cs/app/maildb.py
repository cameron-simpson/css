#!/usr/bin/python -tt

from __future__ import with_statement
from collections import deque
from getopt import getopt, GetoptError
from email.utils import getaddresses, parseaddr, formataddr
from itertools import chain
import logging
import sys
import os
import unittest
from cs.logutils import setup_logging, Pfx, info, warning, error, D
from cs.mail import ismaildir, ismbox, messagesFromPath
from cs.mailutils import message_addresses
from cs.nodedb import NodeDB, Node, NodeDBFromURL
from cs.threads import locked_property
from cs.misc import the

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  usage = '''Usage:
    %s [-m mdburl] op [op-args...]
    Ops:
      import-addresses < addresses.txt
        File format:
          group,... rfc2822-address
      learn-addresses group,... < rfc822.xt
      list-groups [groups...]''' \
    % (cmd,)
  setup_logging(cmd)

  xit = 0
  badopts = False
  mdburl = None

  try:
    opts, argv = getopt(argv, 'm:')
  except GetoptError, e:
    error("unrecognised option: %s: %s"% (e.opt, e.msg))
    badopts = True
    opts, argv = [], []

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
        if stdin.isatty():
          error("stdin is a tty, file expected")
          badopts = True
        else:
          mdb.importAddresses(stdin)
          mdb.close()
      elif op == 'list-groups':
        if len(argv):
          group_names = argv
        else:
          group_names = sorted(mdb.address_groups.keys())
        for group_name in group_names:
          address_group = mdb.address_groups.get(group_name)
          if not address_group:
            error("no such group: %s", group_name)
            xit = 1
            continue
          print group_name, ", ".join(mdb['ADDRESS', address].formatted
                                      for address in address_group)
      elif op == 'learn-addresses':
        if not len(argv):
          error("missing groups")
          badopts = True
        else:
          group_names = [ name for name in argv.pop(0).split(',') if name ]
          if len(argv):
            error("extra arguments after groups: %s", argv)
            badopts = True
          else:
            mdb.importAddresses_from_message(stdin)
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    error(usage)
    xit = 2

  return xit

PersonNode = Node

class AddressNode(Node):

  @property
  def formatted(self):
    return formataddr( (self.realname, self.name) )

  @property
  def realname(self):
    return getattr(self, 'REALNAME', '')

  def groups(self):
    return [ address_group for address_group in self.nodedb.address_groups
             if self.name in address_group ]

  def in_group(self, group_name):
    address_group = self.nodedb.address_groups.get(group_name)
    if address_group is None:
      return False
    return self.name in G

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

  def __init__(self, backend, readonly=False):
    NodeDB.__init__(self, backend, readonly=readonly)
    self._address_groups = None

  def _createNode(self, t, name):
    ''' Create a new Node of the specified type.
    '''
    if t in TypeFactory:
      return TypeFactory[t](t, name, self)
    return NodeDB._createNode(self, t, name)

  def getAddressNode(self, addr):
    ''' Obtain the AddressNode for the specified address `addr`.
        If `addr` is a string, parse it into `realname` and `coreaddr`
        components. Otherwise, `addr` is expected to be a 2-tuple of
        `realname` and `coreaddr`.
        If the AddressNode has no .REALNAME and `realname` is not empty,
        update the AddressNode from `realname`.
    '''
    if type(addr) is str:
      realname, coreaddr = parseaddr(addr)
    else:
      realname, coreaddr = addr
    coreaddr = coreaddr.lower()
    if  len(coreaddr) == 0:
      raise ValueError("core(%r) => %r" % (addr, coreaddr))
    A = self.get( ('ADDRESS', coreaddr), doCreate=True)
    Aname = A.realname
    if not len(Aname) and len(realname) > 0:
      A.REALNAME = realname
    return A

  def address_group(self, group_name):
    ''' Return the set of addresses in the group `group_name`.
        Create the set if necessary.
    '''
    address_groups = self.address_groups
    address_group = address_groups.get(group_name)
    if address_group is None:
      address_groups[group_name] = address_group = set()
    return address_group

  @locked_property
  def address_groups(self):
    ''' Compute the address_group sets.
        Return the mapping.
    '''
    address_groups = { 'all': set() }
    all = address_groups['all']
    for A in self.ADDRESSes:
      for group_name in A.GROUPs:
        if group_name in address_groups:
          address_group = address_groups[group_name]
        else:
          address_group = address_groups[group_name] = set()
        address_group.add(A.name)
        all.add(A.name)
    return address_groups

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
          group[,...] email_address
    '''
    with Pfx(str(fp)):
      lineno = 0
      for line in fp:
        lineno += 1
        with Pfx(str(lineno)):
          if not line.endswith('\n'):
            error("unexpected EOF")
            break
          try:
            groups, addr = line.strip().split(None, 1)
          except ValueError:
            error("no addresses")
            continue
          if not len(addr):
            info("SKIP - no address")
          ##print groups, addr
          try:
            A = self.getAddressNode(addr)
          except ValueError, e:
            error("bad address: %s: %s", addr, e)
            continue
          A.GROUPs.update(groups.split(','))
      self._address_groups = None

  def importAddresses_from_message(self, M, group_names):
    if isinstance(M, str):
      pathname = M
      with Pfx(pathname):
        with open(pathname) as mfp:
          return self.importAddresses_from_message(read_message(mfp, headersonly=True))
    for addrtext in message_addresses(M, ('from', 'to', 'cc', 'bcc', 'resent-to', 'resent-cc', 'reply-to')):
      self.getAddressNode(addrtext).GROUPs.update(group_names)

if __name__ == '__main__':
  sys.exit(main(list(sys.argv)))
