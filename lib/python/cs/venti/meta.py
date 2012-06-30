#!/usr/bin/python

from __future__ import print_function
from pwd import getpwuid
from grp import getgrgid
from cs.logutils import error

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
      for line in s.split('\n'):
        line = line.strip()
        if len(line) == 0 or line.startswith('#'):
          continue
        if line.find(':') < 1:
          error("bad metadata line (no colon): %s" % (line,))
        else:
          k, v = line.split(':', 1)
          self[k] = v

  def encode(self):
    return "".join("%s:%s\n" % (k, self[k]) for k in sorted(self.keys()))

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

  def updateFromStat(self, st):
    self.mtime = st.st_mtime
    user = getpwuid(st.st_uid)[0]
    group = getgrgid(st.st_gid)[0]
    uid = st.st_uid
    gid = st.st_gid
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

if __name__ == '__main__':
  import os
  print(MetaFromStat(os.stat(__file__)))
