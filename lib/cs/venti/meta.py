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
  def __init__(self,s=None):
    if s is not None:
      for line in s.split('\n'):
        line=line.strip()
        if len(line) == 0 or line[0] == '#':
          continue
        if line.find(':') < 1:
          cmderr("bad metadata line (no colon): %s" % line)
        else:
          k,v = line.split(':',1)
          self[k]=v

  def encode(self):
    ks=self.keys()
    ks.sort()
    return "".join("%s:%s\n" % (k, self[k]) for k in ks)

  def mtime(self,when=None):
    if when is None:
      return float(self.get('m',0))
    self['m']=float(when)

  def unixPerms(self):
    ''' Return (user,group,unix-mode-bits).
        The user and group are strings, not uid/gid.
        For ACLs with more than one user or group this is only an approximation,
        keeping the permissions for the frontmost user and group.
    '''
    user=None
    uperms=0
    group=None
    gperms=0
    operms=0
    acl=[ ac for ac in self.get('a','').split(',') if len(ac) > 0 ]
    acl.reverse()
    for ac in acl:
      if len(ac) > 0:
        if ac.startswith('u:'):
          login, perms = ac[2:].split(':',1)
          if login != user:
            user=login
            uperms=0
          if '-' in perms:
            add, sub = perms.split('-',1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   uperms|=4
            elif a == 'w': uperms|=2
            elif a == 'x': uperms|=1
            elif a == 's': uperms|=32
          for s in sub:
            if s == 'r':   uperms&=~4
            elif s == 'w': uperms&=~2
            elif s == 'x': uperms&=~1
            elif s == 's': uperms&=~32
        elif ac.startswith('g:'):
          gname, perms = ac[2:].split(':',1)
          if gname != group:
            group=gname
            gperms=0
          if '-' in perms:
            add, sub = perms.split('-',1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   gperms|=4
            elif a == 'w': gperms|=2
            elif a == 'x': gperms|=1
            elif a == 's': gperms|=128
          for s in sub:
            if s == 'r':   gperms&=~4
            elif s == 'w': gperms&=~2
            elif s == 'x': gperms&=~1
            elif s == 's': gperms&=~128
        elif ac.startswith('*:'):
          perms = ac[2:]
          if '-' in perms:
            add, sub = perms.split('-',1)
          else:
            add, sub = perms, ''
          for a in add:
            if a == 'r':   operms|=4
            elif a == 'w': operms|=2
            elif a == 'x': operms|=1
            elif a == 't': operms|=512
          for s in sub:
            if s == 'r':   operms&=~4
            elif s == 'w': operms&=~2
            elif s == 'x': operms&=~1
            elif s == 't': operms&=~512
    return (user,group,(uperms<<6)+(gperms<<3)+operms)
