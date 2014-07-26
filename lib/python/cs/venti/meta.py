#!/usr/bin/python

from __future__ import print_function
import os
from os import geteuid, getegid
import stat
from collections import namedtuple
from pwd import getpwuid, getpwnam
from grp import getgrgid, getgrnam
from threading import RLock
from cs.logutils import error, X
from cs.threads import locked

DEFAULT_ACL = 'u:rw-,g:rw-,*:r-'

Stat = namedtuple('Stat', 'st_mode st_ino st_dev st_nlink st_uid st_gid st_size st_atime st_mtime st_ctime')

def permbits_to_acl(bits):
  ''' Take a UNIX 3-bit permission value and return the ACL add-sub string.
      Example: 6 (110) => "rw-x"
  '''
  add = ''
  sub = ''
  for c, bit in ('r', 0x04), ('w', 0x02), ('x', 0x01):
    if bits&bit:
      add += c
    else:
      sub += c
  return add+'-'+sub

class AC(object):
  __slots__ = ('prefix', 'allow', 'deny')

  def __init__(self, prefix, allow, deny):
    ''' Initialise AC with `allow` and `deny` permission strings.
    '''
    self.prefix = prefix
    self.allow = allow
    self.deny = deny

  def textencode(self):
    return self.prefix + ':' + self.allow + '-' + self.deny

  def __call__(self, M, accesses):
    ''' Call the AC with the Meta `M` and the required permissions `accesses`.
        Returns False if any access is in .deny, otherwise returns True if
        all accesses are in .allow.
        Subclasses override this method and check the AC for
        applicability before deferring to this method to test access;
        they return None if the AC is not applicable, for example
        an AC_Owner when the Meta has no owner or the Meta owner
        does not match the caller owner.
    '''
    allow = self.allow
    deny = self.deny
    for access in accesses:
      if access in deny:
        return False
      if access not in allow:
        return False
    return True

class AC_Owner(AC):
  def __init__(self, allow, deny):
    AC.__init__(self, 'o', allow, deny)
  def __call__(self, M, accesses, owner):
    Mowner = M.get('u')
    if Mowner is None or Mowner != owner:
      return None
    return AC.__call__(self, M, accesses)

class AC_Group(AC):
  def __init__(self, allow, deny):
    AC.__init__(self, 'g', allow, deny)
  def __call__(self, M, accesses, group):
    Mgroup = M.get('g')
    if Mgroup is None or Mgroup != group:
      return None
    return AC.__call__(self, M, accesses)

class AC_Other(AC):
  def __init__(self, allow, deny):
    AC.__init__(self, '*', allow, deny)

_AC_prefix_map = {
  'o':  AC_Owner,
  'g':  AC_Group,
  '*':  AC_Other,
}

def decodeAC(ac_text):
  ''' Factory function to return a new AC from an encoded AC.
  '''
  prefix, allow_deny = ac_text.split(':', 1)
  allow, deny = allow_deny.split('-', 1)
  try:
    ac_class = _AC_prefix_map[prefix]
  except KeyError:
    raise ValueError("invlaid prefix %r from %r" % (prefix, ac_text))
  return ac_class(allow, deny)

def decodeACL(acl_text):
  ''' Return a list of ACs from the encoded list `acl_text`.
  '''
  acl = []
  for ac_text in acl_text.split(','):
    if ac_text:
      try:
        ac = decodeAC(ac_text)
      except ValueError as e:
        error("invalid ACL element ignored: %r", ac_text)
  return acl

def encodeACL(acl):
  ''' Encode a list of AC instances as text.
  '''
  return ','.join( [ ac.encode() for ac in acl ] )

class Meta(dict):
  ''' Inode metadata: times, permissions, ownership etc.

      This is a dictionary with the following keys:

      'u': owner
      'g': group owner
      'a': ACL
      'm': modification time, a float
  '''
  def __init__(self, E):
    dict.__init__(self)
    self.E = E
    self._acl = None
    self._lock = RLock()

  def textencode(self):
    ''' Encode the metadata in text form.
    '''
    # update 'a' if necessary
    _acl = self._acl
    if _acl is not None:
      self['a'] = encodeACL(_acl)
    return ";".join("%s:%s" % (k, self[k]) for k in sorted(self.keys()))

  def encode(self):
    ''' Encode the metadata in binary form: just text transcribed in UTF-8.
    '''
    return self.textencode().encode()

  @property
  def mtime(self):
    return self.get('m', 0.0)

  @mtime.setter
  def mtime(self, when):
    self['m'] = when

  @property
  @locked
  def acl(self):
    ''' Return the live list of AC instances, decoded at need.
    '''
    _acl = self._acl
    if _acl is None:
      _acl = self._acl = decodeACL(self.get('a', DEFAULT_ACL))
    return _acl

  @acl.setter
  @locked
  def acl(self, ac_L):
    ''' Rewrite the ACL with a list of AC instances.
    '''
    self['a'] = encodeACL(ac_L)
    self._acl = None

  def update(self, metatext):
    ''' Update the Meta fields from the supplied metatext.
    '''
    for metafield in metatext.split(';'):
      metafield = metafield.strip()
      if not metafield:
        continue
      try:
        k, v = metafield.split(':', 1)
      except ValueError:
        error("ignoring bad metatext field: %r", metafield)
        continue
      self[k] = v

  def update_from_stat(self, st):
    ''' Apply the contents of a stat object to this Meta.
    '''
    self.mtime = float(st.st_mtime)
    user = getpwuid(st.st_uid)[0]
    group = getgrgid(st.st_gid)[0]
    if ':' in user:
      raise ValueError("invalid username for uid %d, colon forbidden: %s" % (st.st_uid, user))
    if ':' in group:
      raise ValueError("invalid groupname for gid %d, colon forbidden: %s" % (st.st_gid, group))
    self.acl = decodeACL(';'.join( (
                    "o:"+user+":"+permbits_to_acl( (st.st_mode>>6)&7 ),
                    "g:"+group+":"+permbits_to_acl( (st.st_mode>>3)&7 ),
                    "*:"+permbits_to_acl( (st.st_mode)&7 ),
               ) ) )

  @property
  def unix_perms(self):
    ''' Return (user, group, unix-mode-bits).
        The user and group are strings, not uid/gid.
        For ACLs with more than one user or group this is only an approximation,
        keeping the permissions for the frontmost user and group.
    '''
    user = None
    uperms = 0
    group = None
    gperms = 0
    operms = 0
    for ac in reversed(self.acl):
      if len(ac) > 0:
        if ac.startswith('u:'):
          login, perms = ac[2:].split(':', 1)
          if login != user:
            user = login
            uperms = 0
          if '-' in perms:
            add, sub = perms.split('-', 1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   uperms |= 4
            elif a == 'w': uperms |= 2
            elif a == 'x': uperms |= 1
            elif a == 's': uperms |= 32
          for s in sub:
            if s == 'r':   uperms &= ~4
            elif s == 'w': uperms &= ~2
            elif s == 'x': uperms &= ~1
            elif s == 's': uperms &= ~32
        elif ac.startswith('g:'):
          gname, perms = ac[2:].split(':', 1)
          if gname != group:
            group = gname
            gperms = 0
          if '-' in perms:
            add, sub = perms.split('-', 1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   gperms |= 4
            elif a == 'w': gperms |= 2
            elif a == 'x': gperms |= 1
            elif a == 's': gperms |= 128
          for s in sub:
            if s == 'r':   gperms &= ~4
            elif s == 'w': gperms &= ~2
            elif s == 'x': gperms &= ~1
            elif s == 's': gperms &= ~128
        elif ac.startswith('*:'):
          perms = ac[2:]
          if '-' in perms:
            add, sub = perms.split('-', 1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   operms |= 4
            elif a == 'w': operms |= 2
            elif a == 'x': operms |= 1
            elif a == 't': operms |= 512
          for s in sub:
            if s == 'r':   operms &= ~4
            elif s == 'w': operms &= ~2
            elif s == 'x': operms &= ~1
            elif s == 't': operms &= ~512
    perms = (uperms<<6) + (gperms<<3) + operms
    if self.E.isdir:
      X("meta.unix_perms: %s: set S_IFDIR", self.E.name)
      perms |= stat.S_IFDIR
    elif self.E.isfile:
      X("meta.unix_perms: %s: set S_IFREG", self.E.name)
      perms |= stat.S_IFREG
    operms = perms
    perms |= 0o755
    X("unix_perms: %o ==> %o", operms, perms)
    return user, group, perms

  def access(self, amode, user=None, group=None):
    ''' POSIX like access call, accepting os.access `amode`.
    '''
    X("Meta.access: return TRUE ALWAYS")
    return True
    u, g, perms = self.unix_perms
    if amode & os.R_OK:
      if user is not None and user == u:
        if not ( (perms>>6) & 4 ):
          X("Meta.access: FALSE")
          return False
      elif group is not None and group == g:
        if not ( (perms>>3) & 4 ):
          X("Meta.access: FALSE")
          return False
      elif not ( perms & 4 ):
          X("Meta.access: FALSE")
          return False
    if amode & os.W_OK:
      if user is not None and user == u:
        if not ( (perms>>6) & 2 ):
          X("Meta.access: FALSE")
          return False
      elif group is not None and group == g:
        if not ( (perms>>3) & 2 ):
          X("Meta.access: FALSE")
          return False
      elif not ( perms & 2 ):
          X("Meta.access: FALSE")
          return False
    if amode & os.X_OK:
      if user is not None and user == u:
        if not ( (perms>>6) & 1 ):
          X("Meta.access: FALSE")
          return False
      elif group is not None and group == g:
        if not ( (perms>>3) & 1 ):
          X("Meta.access: FALSE")
          return False
      elif not ( perms & 1 ):
          X("Meta.access: FALSE")
          return False
    X("Meta.access: TRUE")
    return True

  def stat(self):
    ''' Return a stat object computed from this Meta data.
    '''
    user, group, st_mode = self.unix_perms
    if user is None:
      st_uid = os.geteuid()
    else:
      try:
        st_uid = getpwnam(user).pw_uid
      except KeyError:
        st_uid = os.geteuid()
    if group is None:
      st_gid = getegid()
    else:
      try:
        st_gid = getgrnam(group).gr_gid
      except KeyError:
        st_gid = getegid()
    st_ino = -1
    st_dev = -1
    st_nlink = 1
    st_size = self.E.size()
    st_atime = 0
    st_mtime = 0
    st_ctime = 0
    return Stat(st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, st_atime, st_mtime, st_ctime)
