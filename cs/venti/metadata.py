#!/usr/bin/python
#
# Inode metadata.
#       - Cameron Simpson <cs@zip.com.au>
#

def MetaData(dict):
  def __init__(self,isdir,meta=None):
    dict.__init__(self)
    self.isdir=isdir
    self.__encoded=meta

  def __init(self):
    if self.__encoded is not None:
      self['OWNER']=None
      self['ACL']=[]
      for melem in decompress(self.__encoded).split():
        if melem.startswidth("o:"):
          self['OWNER']=melem[2:]
        else:
          self['ACL'].append(melem)
      self.__encoded=None

  def encode(self):
    self.__init()
    m=[]
    o=self['OWNER']
    if o is not None:
      m.append("o:"+o)
    for ac in self['ACL']:
      m.append(ac)
    return compress(" ".join(m))

  def chown(self,user):
    self['OWNER']=user

  def ACL(self):
    acl=self.get('ACL')
    if acl is None:
      acl=()
    return acl

  def UNIXstat(self):
    import stat
    owner=None
    mode=0

    o=self.get('OWNER')
    if o is not None:
      owner=o
      tag="u:"+o+":"
      acl=self.acl()
      acl.reverse()
      for ac in [a for a in self.acl() if a.startswith(tag)]:
        perms=ac[len(tag):]
        if perms[:1] == '-':
          for p in perms[1:]:
            if p == '*':
              mode &= ~0700
            elif p == 'r':
              mode &= ~0400
            elif p == 'w':
              mode &= ~0200
            elif p == 'x':
              mode &= ~0100
        else:
          for p in perms:
            if p == '*':
              mode |= 0700
            elif p == 'r':
              mode |= 0400
            elif p == 'w':
              mode |= 0200
            elif p == 'x':
              mode |= 0100

    ##group=None
    ##for ac in [a for a in self.acl() if a.startswith("g:")]:
    ##  if group is None:

    return None
