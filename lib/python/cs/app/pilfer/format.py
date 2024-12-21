#!/usr/bin/env python3

class FormatArgument(str):

  @property
  def as_int(self):
    return int(self)

class FormatMapping(object):
  ''' A mapping object to set or fetch user variables or URL attributes.
      Various URL attributes are known, and may not be assigned to.
      This mapping is used with str.format to fill in {value}s.
  '''

  def __init__(self, P, U=None, factory=None):
    ''' Initialise this `FormatMapping` from a Pilfer `P`.
        The optional parameter `U` (default from `P._`) is the
        object whose attributes are exposed for format strings,
        though `P.user_vars` preempt them.
        The optional parameter `factory` is used to promote the
        value `U` to a useful type; it calls `URL.promote` by default.
    '''
    self.pilfer = P
    if U is None:
      U = P._
    if factory is None:
      factory = URL.promote
    self.url = factory(U)

  def _ok_attrkey(self, k):
    ''' Test for validity of `k` as a public non-callable attribute of self.url.
    '''
    if not k[0].isalpha():
      return False
    U = self.url
    try:
      attr = getattr(U, k)
    except AttributeError:
      return False
    return not callable(attr)

  def keys(self):
    ks = (
        set([k for k in dir(self.url) if self._ok_attrkey(k)]) +
        set(self.pilfer.user_vars.keys())
    )
    return ks

  def __getitem__(self, k):
    return FormatArgument(self._getitem(k))

  def _getitem(self, k):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if k in P.user_vars:
        return P.user_vars[k]
      if not self._ok_attrkey(k):
        raise KeyError(
            "unapproved attribute (missing or callable or not public): %r" %
            (k,)
        )
      try:
        attr = getattr(url, k)
      except AttributeError as e:
        raise KeyError("no such attribute: .%s: %s" % (k, e))
      return attr

  def get(self, k, default):
    try:
      return self[k]
    except KeyError:
      return default

  def __setitem__(self, k, value):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if self._ok_attrkey(k):
        raise KeyError("it is forbidden to assign to attribute .%s" % (k,))
      else:
        P.user_vars[k] = value

  def format(self, s):
    ''' Format the string `s` using this mapping.
    '''
    return Formatter().vformat(s, (), self)
