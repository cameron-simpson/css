#!/usr/bin/python

''' Directory entry metadata.
'''

from __future__ import print_function
from functools import lru_cache
import json
from math import isclose
import os
from collections import namedtuple
from pwd import getpwuid, getpwnam
from grp import getgrgid, getgrnam
from stat import S_ISUID, S_ISGID
import time
from cs.binary import BSData, BSString
from cs.buffer import CornuCopyBuffer
from cs.logutils import error, warning
from cs.threads import locked
from cs.x import X
from . import RLock
from .transcribe import Transcriber, register as register_transcriber, \
                        transcribe_mapping, parse_mapping

DEFAULT_DIR_ACL = 'o:rwx-'
DEFAULT_FILE_ACL = 'o:rw-x'

NOUSERID = -1
NOGROUPID = -1

@lru_cache(maxsize=64)
def username(uid):
  ''' Look up the login name associated with the supplied `uid`.
      Return None if unknown. Caches results, including lookup failure.
  '''
  try:
    pw = getpwuid(uid)
  except KeyError:
    return None
  return pw.pw_name

@lru_cache(maxsize=64)
def userid(user_name):
  ''' Look up the user id associated with the supplied `username`.
      Return None if unknown. Caches results, including lookup failure.
  '''
  try:
    pw = getpwnam(user_name)
  except KeyError:
    return None
  return pw.pw_uid

@lru_cache(maxsize=64)
def groupname(gid):
  ''' Look up the group name associated with the supplied `gid`.
      Return None if unknown. Caches results, including lookup failure.
  '''
  try:
    gr = getgrgid(gid)
  except KeyError:
    return None
  return gr.gr_name

@lru_cache(maxsize=64)
def groupid(group_name):
  ''' Look up the group id associated with the supplied `groupname`.
      Return None if unknown. Caches results, including lookup failure.
  '''
  try:
    gr = getgrnam(group_name)
  except KeyError:
    return None
  return gr.gr_gid

def permbits_to_allow_deny(bits):
  ''' Take a UNIX 3-bit permission value and return the ACL allow and deny strings.
      Example: 6 (110) => 'rw', 'x'
  '''
  add = ''
  sub = ''
  for c, bit in ('r', 0x04), ('w', 0x02), ('x', 0x01):
    if bits & bit:
      add += c
    else:
      sub += c
  return add, sub

def rwx(mode):
  ''' Transcribe 3 bits of a UNIX mode in 'rwx' form.
  '''
  return (
      ('r' if mode & 4 else '-') + ('w' if mode & 2 else '-') +
      ('x' if mode & 1 else '-')
  )

class AC(namedtuple('AccessControl', 'audience allow deny')):
  ''' An Access Control.
  '''

  def __str__(self):
    ''' Encode this access control as text.
    '''
    audience, allow, deny = self
    if ':' in audience:
      raise ValueError(
          "invalid audience, may not contain a colon: %r" % (audience,)
      )
    if '-' in allow:
      raise ValueError("invalid allow, may not contain a dash: %r" % (allow,))
    return audience + ':' + allow + '-' + deny

  def __repr__(self):
    return ':'.join((type(self).__name__, str(self)))

  @classmethod
  def from_str(cls, ac_text):
    ''' Factory function to return a new AC from an encoded AC.
    '''
    audience, allow_deny = ac_text.split(':', 1)
    if audience not in ('o', 'g', '*'):
      raise ValueError("invalid audience %r from %r" % (audience, ac_text))
    allow, deny = allow_deny.split('-', 1)
    return cls(audience, allow, deny)

  def __call__(self, M, accesses):
    ''' Call the AC with the Meta `M` and the required permissions `accesses`.

        *Note*: this does _not_ check the audience and presumes
        that the access control is applicable.

        Returns False if any access is in .deny, otherwise returns True if
        all accesses are in .allow.

        Subclasses override this method and check the access control
        audience for applicability before deferring to this method
        to test access; they return None if the access control is
        not applicable, for example an AC_Owner when the Meta has
        no owner or the Meta owner does not match the caller owner.
    '''
    allow = self.allow
    deny = self.deny
    for access in accesses:
      if access in deny:
        return False
      if access not in allow:
        return False
    return True

  @property
  def unixmode(self):
    ''' Return the 3-bit UNIX mode for this access.
    '''
    mode = 0
    for a in self.allow:
      if a == 'r':
        mode |= 4
      elif a == 'w':
        mode |= 2
      elif a == 'x':
        mode |= 1
      else:
        warning("AC.unixmode: ignoring unsupported permission %r", a)
    return mode

AC_Owner = lambda allow, deny: AC('o', allow, deny)
AC_Group = lambda allow, deny: AC('g', allow, deny)
AC_Other = lambda allow, deny: AC('*', allow, deny)

class ACL(list):
  ''' An access control list.
  '''

  def __str__(self):
    ''' Transcribe a list of AC instances as text.
    '''
    return ','.join([str(ac) for ac in self])

  @classmethod
  def from_str(cls, acl_text):
    ''' Return an ACL from the str `acl_text`.
    '''
    acl = cls()
    for ac_text in acl_text.split(','):
      if ac_text:
        try:
          ac = AC.from_str(ac_text)
        except ValueError as e:
          error("invalid ACL element ignored: %r: %s", ac_text, e)
          raise
        else:
          acl.append(ac)
    return acl

def xattrs_from_bytes(bs, offset=0):
  ''' Decode an XAttrs from some bytes, return the xattrs dictionary.
  '''
  bfr = CornuCopyBuffer.from_bytes(bs)
  if offset > 0:
    bfr.skip(offset)
  xattrs = {}
  while not bfr.at_eof():
    name = BSString.parse_value(bfr)
    data = BSData.parse_value(bfr)
    if name in xattrs:
      warning("repeated name, ignored: %r", name)
    else:
      xattrs[name] = data
  return xattrs

# This is a direct dict subclass for memory efficiency.
# pylnt: disable=too-many-public-methods,too-many-instance-attributes
class Meta(dict, Transcriber):
  ''' Inode metadata: times, permissions, ownership etc.

      This is a dictionary with the following keys:
      * `'u'`: owner
      * `'g'`: group owner
      * `'a'`: ACL
      * `'c'`: st_ctime, last change to inode/metadata
      * `'m'`: modification time, a float
      * `'su'`: setuid
      * `'sg'`: setgid
      * `'pathref'`: pathname component for symlinks
      * `'x'`: xattrs
  '''

  transcribe_prefix = 'M'

  def __init__(self, mapping=None):
    dict.__init__(self)
    self._lock = RLock()
    self._xattrs = {}
    dict.__setitem__(self, 'x', self._xattrs)
    self.acl = ACL()
    self._ctime = 0
    if mapping:
      for k, v in mapping.items():
        self[k] = v

  def __repr__(self):
    return ':'.join((type(self).__name__, str(self)))

  def __eq__(self, other):
    d1 = self._as_dict()
    m1 = d1.pop('m', 0.0)
    d2 = other._as_dict()
    m2 = d2.pop('m', 0.0)
    return isclose(m1, m2) and d1 == d2

  def textencode(self):
    ''' Return the encoding of this Meta as text.
    '''
    d = self._as_dict()
    if all(k in ('u', 'g', 'a', 'm', 'su', 'sg') for k in d.keys()):
      # these are all "safe" fields - use the compact encoding
      encoded = ';'.join(
          ':'.join((k, "%f" % v if isinstance(v, float) else str(v)))
          for k, v in sorted(d.items())
      )
    else:
      # use the more verbose safe JSON encoding
      encoded = json.dumps(d, separators=(',', ':'))
    return encoded

  def _as_dict(self):
    ''' A dictionary usable for JSON or the compact transcription.
    '''
    d = dict(self)
    # drop xattrs if empty
    xa = d.get('x', {})
    if not xa and 'x' in d:
      del d['x']
    # convert ACL to str, leave other types alone
    d = dict(
        (k, (str(v) if isinstance(v, ACL) else v))
        for k, v in sorted(d.items())
    )
    return d

  def transcribe_inner(self, T, fp):
    ''' Transcribe the Meta.
    '''
    d = self._as_dict()
    return transcribe_mapping(d, fp, T=T)

  # pylint: disable=too-many-arguments
  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse a Meta transcription.
    '''
    if prefix != 'M':
      raise ValueError("expected prefix='M', got: %r" % (prefix,))
    m, offset = parse_mapping(s, offset, stopchar=stopchar, T=T)
    return cls(m), offset

  def __getitem__(self, k):
    if k == 'c':
      return self._ctime
    return dict.__getitem__(self, k)

  def __setitem__(self, k, v):
    # intercept some keys
    if k == 'c':
      v = float(v)
      self._ctime = v
      return
    if k == 'x':
      for xk, xv in v.items():
        self.setxattr(xk, xv)
      return
    if k == 'a':
      if isinstance(v, str):
        v = ACL.from_str(v)
      elif not isinstance(v, ACL):
        raise ValueError("not an ACL: %r" % (v,))
    elif k in ('m',):
      v = float(v)
    dict.__setitem__(self, k, v)
    self._ctime = time.time()

  def __delitem__(self, k):
    dict.__delitem__(self, k)
    self._ctime = time.time()

  @classmethod
  def from_text(cls, metatext):
    ''' Construct a new Meta from `metatext`.
    '''
    M = cls()
    M.update_from_text(metatext)
    return M

  def update_from_text(self, metatext):
    ''' Update the Meta fields from the supplied metatext.
    '''
    if metatext.startswith('{'):
      # wordy JSON encoding of metadata
      metadata = json.loads(metatext)
      kvs = metadata.items()
    else:
      # old style compact metadata
      kvs = []
      for metafield in metatext.split(';'):
        metafield = metafield.strip()
        if not metafield:
          continue
        try:
          k, v = metafield.split(':', 1)
        except ValueError:
          error("ignoring bad metatext field (no colon): %r", metafield)
          continue
        else:
          kvs.append((k, v))
    for k, v in kvs:
      if k == 'x':
        # update the xattrs from `v`, which should be a dict
        for xk, xv in v.items():
          self.setxattr(xk, xv)
      else:
        self[k] = v

  @property
  def user(self):
    ''' Return the username associated with this Meta's owner.
    '''
    u = self.get('u')
    if u is not None and not isinstance(u, str):
      u = username(u)
    return u

  @user.setter
  def user(self, u):
    ''' Set the owner (user) of this Meta.
    '''
    self['u'] = u

  @user.deleter
  def user(self):
    ''' Remove the owner of this Meta.
    '''
    if 'u' in self:
      del self['u']

  @property
  def uid(self):
    ''' Return the user id associated with this Meta's owner.
    '''
    u = self.get('u')
    if u is not None and not isinstance(u, int):
      u = userid(u)
    return u

  @uid.setter
  def uid(self, u):
    ''' Set the owner (user) of this Meta.
        Saves the user name of the supplied uid, or the uid if the
        user name cannot be looked up.
    '''
    user = username(u)
    if user is not None:
      u = user
    self['u'] = u

  @uid.deleter
  def uid(self):
    ''' Remove the owner (user) of this Meta.
    '''
    if 'u' in self:
      del self['u']

  @property
  def group(self):
    ''' Return the groupname associated with this Meta's group owner.
    '''
    g = self.get('g')
    if g is not None and not isinstance(g, str):
      g = username(g)
    return g

  @group.setter
  def group(self, g):
    ''' Set the group owner of this Meta.
    '''
    self['g'] = g

  @group.deleter
  def group(self):
    ''' Remove the group owner of this Meta.
    '''
    if 'g' in self:
      del self['g']

  @property
  def gid(self):
    ''' Return the group id associated with this Meta's group owner.
    '''
    g = self.get('g')
    if g is not None and not isinstance(g, int):
      g = groupid(g)
    return g

  @gid.setter
  def gid(self, g):
    ''' Set the owner (user) of this Meta.
        Saves the group name of the supplied gid, or the gid if the
        group name cannot be looked up.
    '''
    group = groupname(g)
    if group is not None:
      g = group
    self['g'] = g

  @gid.deleter
  def gid(self):
    ''' Remove the owner (user) of this Meta.
    '''
    if 'g' in self:
      del self['g']

  @property
  def mtime(self):
    ''' Return the time of last data modification, or 0.0.
    '''
    return self.get('m', 0.0)

  @mtime.setter
  def mtime(self, when):
    ''' Set the time of last modification.
    '''
    self['m'] = when

  @property
  @locked
  def acl(self):
    ''' Return the live list of AC instances, decoded at need.
    '''
    return self['a']

  @acl.setter
  @locked
  def acl(self, ac_L):
    ''' Rewrite the ACL with a list of AC instances.
    '''
    self['a'] = ac_L

  @property
  def setuid(self):
    ''' Return whether this Meta is setuid.
    '''
    return self.get('su', False)

  @setuid.setter
  def setuid(self, flag):
    ''' Set the setuidness of this Meta.
    '''
    if flag:
      self['su'] = flag
    elif 'su' in self:
      del self['su']

  @property
  def setgid(self):
    ''' Return whether this Meta is setgid.
    '''
    return self.get('sg', False)

  @setgid.setter
  def setgid(self, flag):
    ''' Set the setgidness of this Meta.
    '''
    if flag:
      self['sg'] = flag
    elif 'sg' in self:
      del self['sg']

  def chmod(self, mode):
    ''' Apply UNIX permissions to ACL.
    '''
    self.setuid = bool(mode & S_ISUID)
    self.setgid = bool(mode & S_ISGID)
    acl = ACL()
    acl.append(AC_Owner(*permbits_to_allow_deny((mode >> 6) & 7)))
    acl.append(AC_Group(*permbits_to_allow_deny((mode >> 3) & 7)))
    acl.append(AC_Other(*permbits_to_allow_deny(mode & 7)))
    acl.extend(ac for ac in self.acl if ac.audience not in ('o', 'g', '*'))
    self.acl = acl

  def update_from_stat(self, st):
    ''' Apply the contents of a stat object to this Meta.
    '''
    self.mtime = float(st.st_mtime)
    self.uid = st.st_uid
    self.gid = st.st_gid
    self.chmod(st.st_mode & 0o777)
    # TODO: setuid, setgid, sticky

  @property
  def unix_perm_bits(self):
    ''' Return unix-mode-bits.
        *Note*: excludes the type bits (S_IFREG etc).

        For ACLs with more than one user or group this is only an
        approximation.
    '''
    perms = 0
    for ac in self.acl:
      if ac.audience == 'o':
        perms |= ac.unixmode << 6
      elif ac.audience == 'g':
        perms |= ac.unixmode << 3
      elif ac.audience == '*':
        perms |= ac.unixmode
      else:
        warning("Meta.unix_perms: ignoring ACL element %s", ac)
    # TODO: sticky bit?
    if self.setuid:
      perms |= S_ISUID
    if self.setgid:
      perms |= S_ISUID
    return perms

  @property
  def pathref(self):
    ''' Return the `pathref`, the path used by SymlinkDirents.
        Returns None if there is no pathref.
    '''
    return self.get('pathref')

  @pathref.setter
  def pathref(self, newref):
    ''' Set the `pathref` on a SymlinkDirent.
    '''
    self['pathref'] = newref

  # pylint: disable=too-many-arguments,too-many-return-statements,too-many-branches
  def access(
      self,
      access_mode,
      access_uid=None,
      access_gid=None,
      default_uid=None,
      default_gid=None
  ):
    ''' POSIX like access call, accepting os.access `access_mode`.

        Parameters:
        * `access_mode`: a bitmask of os.{R_OK,W_OK,X_OK} as for
          the os.access function.
        * `access_uid`: the effective uid of the querying user.
        * `access_gid`: the effective gid of the querying user.
        * `default_uid`: the reference uid to use if this Meta.uid is None
        * `default_gid`: the reference gid to use if this Meta.gid is None

        If the Meta has no uid or `access_uid == Meta.uid`,
        use the owner permissions.
        Otherwise if the Meta has no gid or `access_gid == Meta.gid`,
        use the group permissions.
        Otherwise use the "other" permissions.
    '''
    X("META.ACCESS...")
    u = self.uid
    if u is None:
      u = default_uid
    g = self.gid
    if g is None:
      g = default_gid
    perms = self.unix_perm_bits
    if access_mode & os.R_OK:
      if u is None or (access_uid is not None and access_uid == u):
        if not (perms >> 6) & 4:
          return False
      elif g is None or (access_gid is not None and access_gid == g):
        if not (perms >> 3) & 4:
          return False
      elif not perms & 4:
        return False
    if access_mode & os.W_OK:
      if u is None or (access_uid is not None and access_uid == u):
        if not (perms >> 6) & 2:
          return False
      elif g is None or (access_gid is not None and access_gid == g):
        if not (perms >> 3) & 2:
          return False
      elif not perms & 2:
        return False
    if access_mode & os.X_OK:
      if u is None or (access_uid is not None and access_uid == u):
        if not (perms >> 6) & 1:
          return False
      elif g is None or (access_gid is not None and access_gid == g):
        if not (perms >> 3) & 1:
          return False
      elif not perms & 1:
        return False
    return True

  @staticmethod
  def xattrify(xkv):
    ''' Convert value `xkv` to str as though an ISO8859-1 encoding.

        We keep a mapping of str->str for the JSON encoding.
    '''
    if isinstance(xkv, (int, float)):
      xkv = str(xkv)
    if isinstance(xkv, str):
      xkv = xkv.encode('utf-8')
    if isinstance(xkv, bytes):
      return xkv.decode('iso8859-1')
    raise TypeError("cannot convert to bytes: %r" % (xkv,))

  def getxattr(self, xk, xv_default):
    ''' Return the bytes value for key `kx`, or `xv_default` if missing.
    '''
    k = self.xattrify(xk)
    try:
      v = self._xattrs[k]
    except KeyError:
      return xv_default
    # convert the str back into bytes
    return v.encode('iso8859-1')

  def setxattr(self, xk, xv):
    ''' Set the value for key `xk` to `xv`.
        Accepted types include: bytes, str, int, float.
    '''
    xk = self.xattrify(xk)
    xv = self.xattrify(xv)
    self._xattrs[xk] = xv
    dict.__setitem__(self, 'x', self._xattrs)

  def delxattr(self, xk):
    ''' Delete the key `xk` if present.
    '''
    xk = self.xattrify(xk)
    if xk in self._xattrs:
      del self._xattrs[xk]

  def listxattrs(self):
    ''' Return the xattr keys, a list of bytes.
    '''
    return [xk.encode('iso8859-1') for xk in self._xattrs.keys()]

  @property
  def mime_type(self):
    ''' Return the file's `user.mime_type` xattr, or None.
    '''
    return self.getxattr('user.mime_type', None)

  @mime_type.setter
  def mime_type(self, new_type):
    ''' Set the file's `user.mime_type` xattr.
    '''
    self.setxattr('user.mime_type', new_type)

  @mime_type.deleter
  def mime_type(self):
    ''' Delete the file's `user.mime_type` xattr.
    '''
    self.delxattr('user.mime_type')

register_transcriber(Meta)
