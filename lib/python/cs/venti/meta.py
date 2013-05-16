#!/usr/bin/python

from __future__ import print_function
from collections import namedtuple
from os import geteuid, getegid
from pwd import getpwuid, getpwnam
from grp import getgrgid, getgrnam
from cs.logutils import error

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

class Meta(dict):
  ''' Metadata:
        Modification time:
          m:unix-seconds(int or float)
        Access Control List:
          a:ac,...
            ac:
              u:user:perms-perms
              g:group:perms-perms
              *:perms-perms
          ? a:blockref of encoded Meta
          ? a:/path/to/encoded-Meta
  '''
  def __init__(self, s=None):
    dict.__init__(self)
    if s is not None:
      for metafield in s.split(';'):
        metafield = metafield.strip()
        if not metafield:
          continue
        if metafield.find(':') < 1:
          error("bad metadata field (no colon): %s" % (metafield,))
        else:
          k, v = metafield.split(':', 1)
          self[k] = v

  def textencode(self):
    ''' Encode the metadata in text form.
    '''
    return "".join("%s:%s;" % (k, self[k]) for k in sorted(self.keys()))

  def encode(self):
    ''' Encode the metadata in binary form: just text transcribed in UTF-8.
    '''
    return self.textencode().encode()

  @property
  def mtime(self):
    return float(self.get('m', 0))

  @mtime.setter
  def mtime(self, when):
    self['m'] = float(when)

  @property
  def acl(self):
    return [ ac for ac in self.get('a', '').split(',') if len(ac) ]

  @acl.setter
  def acl(self, acl):
    self['a'] = ','.join(acl)

  def update_from_stat(self, st):
    ''' Apply the contents of a stat object to this Meta.
    '''
    self.mtime = st.st_mtime
    user = getpwuid(st.st_uid)[0]
    group = getgrgid(st.st_gid)[0]
    if ':' in user:
      raise ValueError("invalid username for uid %d, colon forbidden: %s" % (st.st_uid, user))
    if ':' in group:
      raise ValueError("invalid groupname for gid %d, colon forbidden: %s" % (st.st_gid, group))
    self.acl = ( "u:"+user+":"+permbits_to_acl( (st.st_mode>>6)&7 ),
                 "g:"+group+":"+permbits_to_acl( (st.st_mode>>3)&7 ),
                 "*:"+permbits_to_acl( (st.st_mode)&7 ),
               )

  def unixPerms(self):
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
    return (user, group, (uperms<<6)+(gperms<<3)+operms)

  def stat(self):
    ''' Return a stat object computed from this Meta data.
    '''
    user, group, st_mode = self.unixPerms()
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
    st_ino = None
    st_dev = None
    st_nlink = 1
    st_size = None
    st_atime = 0
    st_mtime = 0
    st_ctime = 0
    return Stat(st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, st_atime, st_mtime, st_ctime)

def MetaFromStat(st):
  M = Meta()
  M.update_from_stat(st)
  return M

if __name__ == '__main__':
  import os
  M = MetaFromStat(os.stat(__file__))
  print(M)
  print(M.stat())
