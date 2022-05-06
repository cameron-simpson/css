#!/usr/bin/python -tt

''' My database of mail information.
'''

from __future__ import with_statement, print_function
from collections import deque
from getopt import getopt, GetoptError
from email.utils import getaddresses, parseaddr, formataddr
from itertools import chain
import codecs
import re
import sys
import os
import tempfile
from cs.lex import get_identifier
from cs.logutils import setup_logging, info, warning, error, D
from cs.mailutils import message_addresses, Message
from cs.nodedb import NodeDB, Node, NodeDBFromURL
from cs.pfx import Pfx
from cs.py.func import derived_property
from cs.py3 import StringTypes, ustr
import cs.sh
from cs.threads import locked_property

DISTINFO = {
    'description':
    "a cs.nodedb NodeDB subclass for storing email address information"
    " (groups, addresses, so forth)",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.lex',
        'cs.logutils',
        'cs.mailutils',
        'cs.nodedb',
        'cs.pfx',
        'cs.py.func',
        'cs.py3',
        'cs.sh',
        'cs.threads',
    ],
    'entry_points': {
        'console_scripts': [
            'maildb = cs.app.maildb:main',
        ],
    },
}

# pylint: disable=too-many-branches,too-many-locals,too-many-statements
# pylint: disable=too-many-nested-blocks
def main(argv=None, stdin=None):
  ''' Command line main programme.
  '''
  if argv is None:
    argv = sys.argv
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  usage = '''Usage:
    %s [-m mdburl] op [op-args...]
    Ops:
      abbreviate abbrev address
        (also "abbrev")
      edit-group group
      edit-group /regexp/
      export exportpath
      import-addresses < addresses.txt
        File format:
          group,... rfc2822-address
      learn-addresses group,... < rfc822.xt
      list-abbreviations [-A] [abbrevs...]
        (also "list-abbrevs")
      list-groups [-A] [-G] [groups...]
        -A Emit mutt alias lines.
        -G Emit mutt group lines.
        Using both -A and -G emits mutt aliases lines with the -group option.
      rewrite
      update-domain @old-domain @new-domain [{/regexp/|address}...]''' \
    % (cmd,)
  setup_logging(cmd)

  xit = 0
  badopts = False
  mdburl = None

  try:
    opts, argv = getopt(argv, 'm:')
  except GetoptError as e:
    error("unrecognised option: %s: %s" % (e.opt, e.msg))
    badopts = True
    opts, argv = [], []

  for opt, val in opts:
    with Pfx(opt):
      if opt == '-m':
        mdburl = val
      else:
        error("unrecognised option")
        badopts = True

  if mdburl is None:
    mdburl = os.environ['MAILDB']

  if not argv:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      readonly = op not in (
          'abbreviate',
          'abbrev',
          'edit-group',
          'import-addresses',
          'learn-addresses',
          'update-domain',
          'rewrite',
      )
      with MailDB(mdburl, readonly=readonly) as MDB:
        if op == 'import-addresses':
          if stdin.isatty():
            error("stdin is a tty, file expected")
            badopts = True
          else:
            MDB.importAddresses(stdin)
            MDB.close()
        elif op in ('abbreviate', 'abbrev'):
          if len(argv) != 2:
            error("expected abbreviation and address, got: %r", argv)
            badopts = True
          if not badopts:
            abbrev, addr = argv
            A = MDB.getAddressNode(addr)
            try:
              A.abbreviation = abbrev
            except ValueError as e:
              error(e)
              xit = 1
        elif op == 'export':
          exportpath = argv.pop(0)
          if argv:
            warning("extra arguments after exportpath: %s", ' '.join(argv))
            badopts = True
          else:
            with Pfx(exportpath):
              if os.path.exists(exportpath):
                error("already exists")
              else:
                MDB.scrub()
                with open(exportpath, "w") as exfp:
                  MDB.dump(exfp)
        elif op in ('list-abbreviations', 'list-abbrevs'):
          try:
            opts, argv = getopt(argv, 'A')
          except GetoptError as e:
            error("unrecognised option: %s: %s" % (e.opt, e.msg))
            badopts = True
            opts, argv = [], []
          mutt_aliases = False
          for opt, val in opts:
            with Pfx(opt):
              if opt == '-A':
                mutt_aliases = True
              else:
                error("unrecognised option")
                badopts = True
          abbrevs = MDB.abbreviations
          if argv:
            abbrev_names = argv
          else:
            abbrev_names = sorted(abbrevs.keys())
          if not badopts:
            for abbrev in abbrev_names:
              with Pfx(abbrev):
                if abbrev in abbrevs:
                  A = MDB.getAddressNode(abbrevs[abbrev])
                  if mutt_aliases:
                    print('alias', abbrev, A.formatted)
                  else:
                    print("%-15s %s" % (abbrev, A.formatted))
                else:
                  error("unknown abbreviation")
                  xit = 1
            # generate other aliases automatically to aid mutt's reverse_alias=yes behaviour
            if mutt_aliases:
              noname_addrs = (
                  set(MDB.address_groups.get('noaliasname', ())) |
                  set(MDB.address_groups.get('polyname', ()))
              )
              alias_names = set(abbrevs.keys())
              auto_aliases = {}
              As = sorted(MDB.ADDRESSes, key=lambda a: a.name)
              for A in As:
                auto_alias = A.realname.strip()
                if auto_alias:
                  names = auto_alias.lower().split()
                  for i, name in enumerate(names):
                    if not name.isalpha():
                      name = ''.join([c for c in name if c.isalpha()])
                      names[i] = name
                  auto_alias_base = '.'.join(names)
                  auto_alias = auto_alias_base
                  n = 1
                  while auto_alias in alias_names:
                    n += 1
                    auto_alias = auto_alias_base + str(n)
                  auto_aliases[
                      auto_alias
                  ] = A.name if A.name in noname_addrs else A.formatted
                  alias_names.add(auto_alias)
              for auto_alias in sorted(auto_aliases.keys()):
                print('alias', auto_alias, auto_aliases[auto_alias])
        elif op == 'list-groups':
          try:
            opts, argv = getopt(argv, 'AG')
          except GetoptError as e:
            error("unrecognised option: %s: %s" % (e.opt, e.msg))
            badopts = True
            opts, argv = [], []
          mutt_aliases = False
          mutt_groups = False
          for opt, val in opts:
            with Pfx(opt):
              if opt == '-A':
                mutt_aliases = True
              elif opt == '-G':
                mutt_groups = True
              else:
                error("unrecognised option")
                badopts = True
          if argv:
            group_names = argv
          else:
            group_names = sorted(MDB.address_groups.keys())
          if not badopts:
            # addresses for which we should list the realname part
            # to stop mutt overruling the comment port in the index display
            nonames_group = MDB.address_groups.get('noaliasname')
            noname_addrs = set(nonames_group) if nonames_group else ()
            for group_name in group_names:
              with Pfx(group_name):
                address_group = MDB.address_groups.get(group_name)
                if not address_group:
                  error('no such group')
                  xit = 1
                  continue
                if mutt_aliases:
                  print('alias', end=' ')
                  if mutt_groups:
                    print('-group', group_name, end=' ')
                elif mutt_groups:
                  print('group', end=' ')
                print(group_name, end=' ')
                formatted_addresses = sorted(
                    (
                        address_entry.name if address_entry.name in
                        noname_addrs else address_entry.formatted
                    ) for address_entry in map(
                        lambda address: MDB['ADDRESS', address], address_group
                    )
                )
                if mutt_aliases or mutt_groups:
                  # write slosh extended lines
                  first = True
                  for formatted_address in formatted_addresses:
                    if first:
                      first = False
                    else:
                      print(', ', end='')
                    print('\\\n               ', formatted_address, end='')
                  print()
                else:
                  # write single huge line
                  print(', '.join(formatted_addresses))
        elif op == 'learn-addresses':
          only_ungrouped = False
          if argv and argv[0] == '--ungrouped':
            argv.pop(0)
            only_ungrouped = True
          if not argv:
            error("missing groups")
            badopts = True
          else:
            group_names = argv.pop(0)
            group_names = [name for name in group_names.split(',') if name]
            if argv:
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
          if not argv:
            error("missing group")
            badopts = True
          else:
            group = argv.pop(0)
            if argv:
              error(
                  "extra arguments after %s \"%s\": %s",
                  ('regexp' if group.startswith('/') else 'group'), group, argv
              )
              badopts = True
            else:
              edit_group(MDB, group)
        elif op == 'rewrite':
          MDB.rewrite()
        elif op == 'update-domain':
          if not argv:
            error("missing @old-domain")
            badopts = True
          else:
            old_domain = argv.pop(0)
            if not old_domain.startswith('@'):
              error('old domain must start with "@": %s' % (old_domain,))
              badopts = True
          if not argv:
            error("missing @new-domain")
            badopts = True
          else:
            new_domain = argv.pop(0)
            if not new_domain.startswith('@'):
              error('new domain must start with "@": %s' % (new_domain,))
              badopts = True
          if not badopts:
            update_domain(MDB, old_domain, new_domain, argv)
        else:
          error("unsupported op")
          badopts = True

  if badopts:
    error(usage)
    xit = 2

  return xit

def edit_group(MDB, groupname):
  ''' Edit the membership of the named `groupname`.
      If `groupname` starts with a slash then the tail is taken to be
      a regexp used to select addresses.
  '''
  if groupname.startswith('/'):
    # select by regular expression
    # regexp as "/re" or "/re/"
    if groupname.endswith('/'):
      rexp = groupname[1:-1]
    else:
      rexp = groupname[1:]
    As = MDB.matchAddresses(rexp)
    Gs = []
  else:
    # select AddressNodes and GroupNodes by groupname
    As = [A for A in MDB.ADDRESSes if groupname in A.GROUPs]
    Gs = [G for G in MDB.GROUPs if groupname in G.GROUPs]
  return edit_groupness(MDB, As, Gs)

# pylint: disable=too-many-branches,too-many-locals,too-many-statements
def edit_groupness(MDB, addresses, subgroups):
  ''' Modify the group memberships of the supplied addresses and groups.
      Removed addresses or groups are not modified.
  '''
  with Pfx("edit_groupness()"):
    Gs = sorted(set(subgroups), key=lambda G: G.name)
    As = sorted(set(addresses), key=lambda A: A.realname.lower())
    with tempfile.NamedTemporaryFile(suffix='.txt') as T:
      with Pfx(T.name):
        with codecs.open(T.name, "w", encoding="utf-8") as ofp:
          # present groups first
          for G in Gs:
            supergroups = sorted(set(G.GROUPs), key=lambda g: g.name)
            line = u'%-15s @%s\n' % (",".join(supergroups), G.name)
            ofp.write(line)
          # present addresses next
          for A in As:
            groups = sorted(set(A.GROUPs))
            af = A.formatted
            ab = A.abbreviation
            if ab:
              af = "=%s %s" % (ab, af)
            line = u"%-15s %s\n" % (",".join(groups), af)
            ofp.write(line)
        editor = os.environ.get('EDITOR', 'vi')
        xit = os.system("%s %s" % (editor, cs.sh.quotestr(T.name)))
        if xit != 0:
          # TODO: catch SIGINT etc?
          raise RuntimeError("error editing \"%s\"" % (T.name,))
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
              groups = [group for group in groups.split(',') if group]
              if addrtext.startswith('@'):
                # presume single group name
                groupname, offset = get_identifier(addrtext, 1)
                if offset < len(addrtext):
                  warning("invalid @groupname: %r", addrtext)
                else:
                  MDB.make(('GROUP', groupname)).GROUPs = groups
                continue
              # otherwise, address list on RHS
              As = set()
              with Pfx(addrtext):
                for realname, addr in getaddresses((addrtext,)):
                  with Pfx("realname=%r, addr=%r", realname, addr):
                    A = MDB.getAddressNode(addr)
                    if realname.startswith('='
                                           ) and not realname.startswith('=?'):
                      with Pfx(repr(realname)):
                        ab, realname = realname.split(None, 1)
                        ab = ab[1:]
                        if not ab:
                          ab = None
                    else:
                      ab = None
                    try:
                      A.abbreviation = ab
                    except ValueError as e:
                      error(e)
                    # add named groups to those associated with this address
                    new_groups.setdefault(A, set()).update(groups)
                    realname = ustr(realname.strip())
                    if realname and realname != A.realname:
                      A.realname = realname
    # apply groups of whichever addresses survived
    for A, groups in new_groups.items():
      if set(A.GROUPs) != groups:
        # reset .GROUP list if changed
        A.GROUPs = groups

def update_domain(MDB, old_domain, new_domain, argv):
  ''' Update the `@domain` of addresses in `MDB`.
  '''
  if not argv:
    addrs = [A.name for A in MDB.ADDRESSes if A.name.endswith(old_domain)]
  else:
    addrs = []
    for pattern in argv:
      if pattern.startswith('/'):
        if pattern.endswith('/'):
          rexp = pattern[1:-1]
        else:
          rexp = pattern[1:]
        addrs.extend([A.name for A in MDB.matchAddresses(rexp)])
      else:
        addrs.append(pattern)
  if not addrs:
    warning("no matching addresses")
  else:
    for addr in addrs:
      with Pfx(addr):
        if not addr.endswith(old_domain):
          warning("does not end in old domain (%s)", old_domain)
        else:
          MDB.update_domain(addr, old_domain, new_domain)

PersonNode = Node

class AddressNode(Node):
  ''' An address `Node`.
  '''

  @property
  def formatted(self):
    ''' The formatted form of this address, typically `"realname" <name>`.
    '''
    return ustr(formataddr((self.realname, self.name)))

  @property
  def realname(self):
    ''' Use the last .REALNAME value; presumes latest is best.
        (Yes, working around junk in the database pending better debugging of another issue.)
    '''
    names = list(self.REALNAMEs)
    if not names:
      return u''
    return names[-1]

  @realname.setter
  def realname(self, newname):
    ''' Set the `.REALNAME` attribute.
    '''
    self.REALNAME = newname

  def groups(self):
    ''' Return the groups of which this address is a member.
    '''
    return [
        address_group for address_group in self.nodedb.address_groups
        if self.name in address_group
    ]

  def in_group(self, group_name):
    ''' Test whther this address is a member of the group named `group_name`.
    '''
    address_group = self.nodedb.address_groups.get(group_name)
    if address_group is None:
      return False
    return self.name in address_group

  @property
  def abbreviation(self):
    ''' The preferred abbreviation for this address or `None`.
    '''
    return self.get0('ABBREVIATION')

  @abbreviation.setter
  def abbreviation(self, abbrev):
    ''' Set the preferred abreviation for this address.
    '''
    return self._setAbbreviation(abbrev)

  @abbreviation.deleter
  def abbreviation(self):
    ''' Delete the preferred abreviation for this address.
    '''
    return self._setAbbreviation(None)

  def _setAbbreviation(self, abbrev):
    abbrevs = self.nodedb.abbreviations
    my_abbrev = self.abbreviation
    if abbrev is None:
      # deleting abbrev
      if my_abbrev is None:
        # unchanged
        return
    else:
      # new abbrev
      if my_abbrev is not None and my_abbrev == abbrev:
        # unchanged
        return
      # new abbrev: check abbrev not taken
      if abbrev is not None and abbrev in abbrevs:
        raise ValueError(
            "%s.ABBREVIATION=%s: abbreviation already maps to %s" %
            (self.name, abbrev, abbrevs[abbrev])
        )
    if my_abbrev is not None:
      # remove old abbrev from mapping
      del abbrevs[my_abbrev]
    if abbrev is None:
      self.ABBREVIATIONs = ()
    else:
      # new mapping
      abbrevs[abbrev] = self.name
      self.ABBREVIATION = abbrev

class MessageNode(Node):
  ''' A node representing a particular message.
  '''

  @property
  def references(self):
    ''' Return the `.REFERENCEs` of this message.
    '''
    return self.REFERENCEs

  @property
  def followups(self):
    ''' Locate the immediate followups to a message.
    '''
    return set(
        N for N, attr, count in self.references(attr='PARENT', type='MESSAGE')
    )

  def thread_root(self):
    ''' Locate the root message of a message thread.
    '''
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

def MailDB(mdburl, readonly=True, klass=None):
  ''' Factory to obtain the `NodeDB` for `mdburl`.
  '''
  if klass is None:
    klass = _MailDB
  return NodeDBFromURL(mdburl, readonly=readonly, klass=klass)

_MailDB_TypeFactories = {
    'MESSAGE': MessageNode,
    'ADDRESS': AddressNode,
}

class _MailDB(NodeDB):
  ''' Extend NodeDB for email.
  '''

  def __init__(self, backend, readonly=False):
    NodeDB.__init__(
        self, backend, readonly=readonly, type_factories=_MailDB_TypeFactories
    )

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    obackend = self.backend
    self.backend = None
    self.scrub()
    self.backend = obackend
    self.backend.rewrite()

  def scrub(self):
    ''' Normalise some of the attributes.
    '''
    for N in self.ADDRESSes:
      gs = N.GROUPs
      if gs:
        gsu = set(gs)
        if len(gsu) < len(gs):
          N.GROUPs = sorted(list(gsu))
      rns = N.REALNAMEs
      if rns:
        rnsu = set(rns)
        if len(rnsu) < len(rns):
          N.REALNAMEs = rnsu
      abbrevs = N.ABBREVIATIONs
      if abbrevs:
        abbrevs_unique = set(abbrevs)
        if len(abbrevs_unique) < len(abbrevs):
          N.ABBREVIATIONs = sorted(list(abbrevs_unique))

  @staticmethod
  def parsedAddress(addr):
    ''' Return `(realname,addr)` for the address string `addr`.
    '''
    if isinstance(addr, StringTypes):
      realname, coreaddr = parseaddr(addr)
    else:
      realname, coreaddr = addr
    return realname, coreaddr

  def getAddressNode(self, addr, noCreate=False):
    ''' Obtain the AddressNode for the specified address `addr`.
        If `addr` is a string, parse it into `realname` and `coreaddr`
        components. Otherwise, `addr` is expected to be a 2-tuple of
        `realname` and `coreaddr`.
        If the AddressNode has no .REALNAME and `realname` is not empty,
        update the AddressNode from `realname`.
        If `noCreate` is True (default False) and the address is not in the
        MailDB, return None and do not create an AddressNode.
    '''
    realname, coreaddr = self.parsedAddress(addr)
    if not coreaddr:
      raise ValueError(
          "getAddressNode(addr=%r): coreaddr => %r" % (addr, coreaddr)
      )
    coreaddr = coreaddr.lower()
    A = self.get(('ADDRESS', coreaddr), doCreate=not noCreate)
    if noCreate and A is None:
      return None
    Aname = A.realname
    if not Aname and realname:
      A.REALNAME = ustr(realname)
    return A

  def matchAddresses(self, rexp):
    ''' Return AddressNodes matching the supplied regular expression string `rexp`.
    '''
    R = re.compile(rexp, re.I)
    As = [A for A in self.ADDRESSes if R.search(A.formatted)]
    return As

  def shortname(self, addr):
    ''' Return a short name for an address.
        Pick the first of: abbreviation from maildb, realname from maildb, coreaddr.
    '''
    realname, coreaddr = self.parsedAddress(addr)
    A = self.getAddressNode((realname, coreaddr), noCreate=True)
    if A is None:
      short = coreaddr
    else:
      abbrev = A.abbreviation
      if abbrev is None:
        rns = A.get('REALNAME')
        if rns:
          rn = rns[0]
        else:
          rn = ''
        short = rn if rn else coreaddr
      else:
        short = abbrev
    return short

  def header_shortlist(self, M, hdrs):
    ''' Return a list of the unique shortnames for the addresses in the specified headers.
    '''
    L = []
    for realname, coreaddr in message_addresses(M, hdrs):
      short = self.shortname((realname, coreaddr))
      if short not in L:
        L.append(short)
    return L

  def address_group(self, group_name):
    ''' Return the set of addresses in the group `group_name`.
        Create the set if necessary.
    '''
    return self.address_groups.setdefault(group_name, set())

  @derived_property
  def address_groups(self):
    ''' Compute the address_group sets, a mapping of GROUP names to a
        set of A.name.lower().
        Return the mapping.
    '''
    try:
      agroups = {'all': set()}
      allgroup = agroups['all']
      for A in self.ADDRESSes:
        coreaddr = A.name
        if coreaddr != coreaddr.lower():
          warning(
              'ADDRESS %r does not have a lowercase .name attribute: %s', A,
              A.name
          )
        for group_name in A.GROUPs:
          agroup = agroups.setdefault(group_name, set())
          agroup.add(coreaddr)
          allgroup.add(coreaddr)
    except AttributeError as e:
      D("address_groups(): e = %r", e)
      raise ValueError("disaster")
    return agroups

  @locked_property
  def subgroups_map(self):
    ''' Cached map of groupname to subgroup names.
    '''
    subgroups = {}
    for G in self.GROUPs:
      for parent_group in G.GROUPs:
        subgroups.setdefault(parent_group, []).append(G.name)

  def subgroups(self, group_name):
    ''' Return a list of the subgroup names of the names group.
    '''
    return self.subgroups_map.get(group_name, [])

  @derived_property
  def abbreviations(self):
    ''' Compute a mapping of abbreviations to their source address.
    '''
    abbrevs = {}
    for A in self.ADDRESSes:
      abbrev = A.abbreviation
      if abbrev is not None:
        if abbrev in abbrevs:
          warning(
              "abbrev %r: ignoring mapping to %s, already mapped to %s",
              abbrev, A.name, abbrevs[abbrev]
          )
        else:
          abbrevs[abbrev] = A.name
    return abbrevs

  def getAddressAbbreviation(self, addr):
    ''' Return the addreviation of this address if known, else None.
    '''
    A = self.getAddressNode(addr, noCreate=True)
    if A is None:
      return None
    return A.abbreviation

  def getMessageNode(self, message_id):
    ''' Obtain the Node for the specified Message-ID `message_id`.
    '''
    return self.get(('MESSAGE', message_id), doCreate=True)

  def getAddressNodes(self, *addrtexts):
    ''' Get the `AddressNode`s for supplied `(realname,addr)` pairs.
    '''
    return [
        self.getAddressNode((realname, addr))
        for realname, addr in getaddresses(addrtexts)
    ]

  def importMessage(self, msg):
    ''' Import the message `msg`.
        Returns the MessageNode.
    '''
    info("import %s->%s: %s" % (msg['from'], msg['to'], msg['subject']))

    msgid = msg['message-id'].strip()
    if (not msgid.startswith('<') or not msgid.endswith('>')
        or msgid.find("@") < 0):
      raise ValueError("invalid Message-ID: %s" % (msgid,))

    M = self.getMessageNode(msgid)
    M.MESSAGE = msg
    M.SUBJECT = msg['subject']
    if 'date' in msg:
      M.DATE = msg['date']
    M.FROMs = self.getAddressNodes(*msg.get_all('from', []))
    M.RECIPIENTS = self.getAddressNodes(
        *chain(
            msg.get_all(hdr, [])
            for hdr in ('to', 'cc', 'bcc', 'resent-to', 'resent-cc')
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
      refids = [msgid for msgid in refhdr.split() if len(msgid)]
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
          if not addr:
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
      header_names = (
          'from', 'to', 'cc', 'bcc', 'resent-to', 'resent-cc', 'reply-to'
      )
    addrs = set()
    if isinstance(M, StringTypes) or hasattr(M, 'readline'):
      return self.importAddresses_from_message(Message(M), group_names)
    for realname, coreaddr in message_addresses(M, header_names):
      A = self.getAddressNode((realname, coreaddr))
      A.GROUPs.update(group_names)
      addrs.add(A)
    return addrs

  def update_domain(self, addr, old_domain, new_domain):
    ''' Rewrite the domain part of an address.
    '''
    with Pfx("update_domain(%s, %s, %s)", addr, old_domain, new_domain):
      if not old_domain.startswith('@'):
        raise ValueError('old_domain does not start with "@"')
      if not new_domain.startswith('@'):
        raise ValueError('new_domain does not start with "@"')
      if not addr.endswith(old_domain):
        raise ValueError('addr does not end in old_domain')
      addr2 = addr[:-len(old_domain)] + new_domain
      A1 = self.getAddressNode(addr)
      A2 = self.getAddressNode(addr2)
      realname = A1.realname
      if realname and not A2.realname:
        A2.realname = realname
      abbrev = A1.abbreviation
      if abbrev and not A2.abbreviation:
        del A1.abbreviation
        A2.abbreviation = abbrev
      groups = A1.GROUPs
      if groups and not A2.GROUPs:
        A2.GROUPs = groups

if __name__ == '__main__':
  sys.exit(main(list(sys.argv)))
