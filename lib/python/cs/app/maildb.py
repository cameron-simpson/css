#!/usr/bin/python -tt

from __future__ import with_statement, print_function
from collections import deque
from getopt import getopt, GetoptError
from email.utils import getaddresses, parseaddr, formataddr
from itertools import chain
import codecs
import logging
import sys
import os
import tempfile
import unittest
from cs.logutils import setup_logging, Pfx, info, warning, error, D
from cs.mailutils import ismaildir, message_addresses, Message
from cs.nodedb import NodeDB, Node, NodeDBFromURL
import cs.sh
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
      edit-group group
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
  except GetoptError as e:
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
    op = argv.pop(0)
    with Pfx(op):
      with MailDB(mdburl, readonly=False) as MDB:
        if op == 'import-addresses':
          if stdin.isatty():
            error("stdin is a tty, file expected")
            badopts = True
          else:
            MDB.importAddresses(stdin)
            MDB.close()
        elif op == 'list-groups':
          if len(argv):
            group_names = argv
          else:
            group_names = sorted(MDB.address_groups.keys())
          for group_name in group_names:
            address_group = MDB.address_groups.get(group_name)
            if not address_group:
              error("no such group: %s", group_name)
              xit = 1
              continue
            print(group_name, ", ".join(MDB['ADDRESS', address].formatted
                                        for address in address_group))
        elif op == 'learn-addresses':
          only_ungrouped = False
          if len(argv) and argv[0] == '--ungrouped':
            argv.pop(0)
            only_ungrouped = True
          if not len(argv):
            error("missing groups")
            badopts = True
          else:
            group_names = argv.pop(0)
            group_names = [ name for name in group_names.split(',') if name ]
            if len(argv):
              error("extra arguments after groups: %s", argv)
              badopts = True
            else:
              if only_ungrouped:
                for A in MDB.importAddresses_from_message(stdin, ()):
                  if not A.GROUPs:
                    A.GROUPs.update(group_names)
              else:
                MDB.importAddresses_from_message(stdin, group_names)
        elif op == 'edit-group':
          if not len(argv):
            error("missing group")
            badopts = True
          else:
            group = argv.pop(0)
            if len(argv):
              error("extra arguments after group \"%s\": %s", group, argv)
              badopts = True
            else:
              edit_group(MDB, group)
              MDB.rewrite()
        else:
          error("unsupported op")
          badopts = True

  if badopts:
    error(usage)
    xit = 2

  return xit

def edit_group(MDB, group):
  return edit_groupness(MDB, [ A for A in MDB.ADDRESSes if group in A.GROUPs ])

def edit_groupness(MDB, addresses):
  ''' Modify the group memberships of the supplied addresses.
      Removed addresses are not modified.
  '''
  with Pfx("edit_groupness()"):
    As = sorted( set(addresses),
                 ( lambda A1, A2: cmp(A1.realname.lower(), A2.realname.lower()) ),
               )
    with tempfile.NamedTemporaryFile(suffix='.txt') as T:
      with Pfx(T.name):
        with codecs.open(T.name, "w", "utf-8") as ofp:
          for A in As:
            groups = sorted(set(A.GROUPs))
            line = "%-15s %s\n" % (",".join(groups), A.formatted)
            ofp.write(line)
        editor = os.environ.get('EDITOR', 'vi')
        xit = os.system("%s %s" % (editor, cs.sh.quotestr(T.name)))
        if xit != 0:
          # TODO: catch SIGINT etc?
          raise RunTimeError("error editing \"%s\"" % (T.name,))
        new_groups = {}
        with codecs.open(T.name, "r", "utf-8") as ifp:
          lineno = 0
          for line in ifp:
            lineno += 1
            with Pfx("%d", lineno):
              if not line.endswith("\n"):
                raise ValueError("truncated file, missing trailing newline")
              line = line.rstrip()
              groups, addrtext = line.split(None, 1)
              groups = [ group for group in groups.split(',') if group ]
              As = set()
              for realname, addr in getaddresses((addrtext,)):
                A = MDB.getAddress(addr)
                new_groups.setdefault(A, set()).update(groups)
                realname = realname.strip()
                if realname and realname != A.realname:
                  A.REALNAME = realname
    # apply groups of whichever addresses survived
    for A, groups in new_groups.items():
      if set(A.GROUPs) != groups:
        A.GROUPs = groups

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
        raise ValueError("%s: recursive PARENTs" % (M,))

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
    self._O_omit = ('address_groups',)
    NodeDB.__init__(self, backend, readonly=readonly)
    self._address_groups = None

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    self.backend.rewrite()

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
    if isinstance(addr, (str, unicode)):
      realname, coreaddr = parseaddr(addr)
    else:
      realname, coreaddr = addr
    coreaddr = coreaddr.lower()
    if  len(coreaddr) == 0:
      raise ValueError("getAddressNode(addr=%r): coreaddr => %r" % (addr, coreaddr))
    A = self.get( ('ADDRESS', coreaddr), doCreate=True)
    Aname = A.realname
    if not len(Aname) and len(realname) > 0:
      A.REALNAME = realname
    return A

  @locked_property
  def address_group(self, group_name):
    ''' Return the set of addresses in the group `group_name`.
        Create the set if necessary.
    '''
    return self.address_groups.set_default(group_name, set())

  @locked_property
  def address_groups(self):
    ''' Compute the address_group sets, a mapping of GOUP names to a
        set of A.name.lower().
        Return the mapping.
    '''
    address_groups = { 'all': set() }
    all = address_groups['all']
    for A in self.ADDRESSes:
      coreaddr = A.name
      if coreaddr != coreaddr.lower():
        warning('ADDRESS %r does not have a lowercase .name attribute: %s', A, A.name)
      for group_name in A.GROUPs:
        address_group = address_groups.set_default(group_name, set())
        address_group.add(coreaddr)
        all.add(coreaddr)
    return address_groups

  def getMessageNode(self, message_id):
    ''' Obtain the Node for the specified Message-ID `message_id`.
    '''
    return self.get( ('MESSAGE', message_id), doCreate=True)

  def getAddressNodes(self, *addrtexts):
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
      raise ValueError("invalid Message-ID: %s" % (msgid,))

    M = self.getMessageNode(msgid)
    M.MESSAGE = msg
    M.SUBJECT = msg['subject']
    if 'date' in msg:
      M.DATE = msg['date']
    M.FROMs = self.getAddressNodes(*msg.get_all('from', []))
    addrs = {}
    M.RECIPIENTS = self.getAddressNodes(
                       *chain( msg.get_all(hdr, [])
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
          try:
            A = self.getAddressNode(addr)
          except ValueError as e:
            error("bad address: %s: %s", addr, e)
            continue
          A.GROUPs.update(groups.split(','))
      # forget the old mapping
      self._address_groups = None

  def importAddresses_from_message(self, M, group_names, header_names=None):
    ''' Import the addresses found in the message `M`.
        Add then to the groups named in `group_names`.
        Specific header lines to consult may be specified in
        `header_names`, which defaults to ( 'from', 'to', 'cc', 'bcc',
        'resent-to', 'resent-cc', 'reply-to' ).
    '''
    if header_names is None:
      header_names = ( 'from', 'to', 'cc', 'bcc', 'resent-to', 'resent-cc',
                       'reply-to' )
    addrs = set()
    if isinstance(M, (str, file)):
      return self.importAddresses_from_message(Message(M), group_names)
    for realname, coreaddr in message_addresses(M, header_names):
      A = self.getAddressNode( (realname, coreaddr) )
      A.GROUPs.update(group_names)
      addrs.add(A)
    return addrs

if __name__ == '__main__':
  sys.exit(main(list(sys.argv)))
