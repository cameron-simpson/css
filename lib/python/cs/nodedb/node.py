#!/usr/bin/python
#

import os.path
from cmd import Cmd
import csv
import fnmatch
import re
import sys
if sys.hexversion < 0x02060000:
  from sets import Set as set
import itertools
from getopt import GetoptError
from thread import allocate_lock
from threading import Thread
from types import StringTypes
from collections import namedtuple
from cs.lex import str1
from cs.misc import the, get0
from cs.mappings import parseUC_sAttr
from cs.logutils import Pfx, D, error, warning, info, debug, exception
from .backend import _NoBackend
from .export import edit_csv_wide, export_csv_wide

# regexp to match TYPE:name
re_NODEREF = re.compile(r'([A-Z]+):([^:#]+)')
# regexp to match a bareword name
re_NAME = re.compile(r'[a-z][-a-z_0-9]*(?![a-zA-Z0-9_])')

def by_name(a, b):
  ''' Compare two objects by their .name attributes.
  '''
  return cmp(a.name, b.name)

def by_type_then_name(a, b):
  ''' Compare two objects by their .type and then .name attributes.
  '''
  c = cmp(a.type, b.type)
  if c != 0:
    return c
  return cmp(a.name, b.name)

def nodekey(*args):
  ''' Convert some sort of key to a (TYPE, NAME) tuple.
      Sanity check the values.
      Return (TYPE, NAME).
      Raises ValueError if the arguments cannot be recognised.
      Subclasses can override this to parse special forms such as
      "hostname-ifname", which might return ('NIC', "hostname-ifname").
  '''
  with Pfx("nodekey(%s)" % (args,)):
    if len(args) == 2:
      t, name = args
    elif len(args) == 1:
      item = args[0]
      if type(item) is str:
        # TYPE:NAME
        t, name = item.split(':', 1)
      else:
        # (TYPE, NAME)
        t, name = item
    else:
      raise ValueError, "nodekey() takes (TYPE, NAME) args or a single arg: args=%s" % ( args, )

    if type(t) not in (str, unicode):
      raise ValueError, "expected TYPE to be a string: %r" % (t,)
    if type(name) is int:
      name = str(name)
    elif type(name) not in (str, unicode):
      raise ValueError, "expected NAME to be a string: %r" % (name,)
    if not t.isupper() and t != '_':
      raise ValueError, "invalid TYPE, not upper case or _"
    if not len(name):
      raise ValueError, "empty NAME"
    return t, name

class _AttrList(list):
  ''' An _AttrList is a list subtype that understands Nodes
      and .ATTR[s] attribute access and drives a backend.
  '''

  def __init__(self, node, attr, _items=None):
    ''' Initialise an _AttrList.
        `node` is the node to which this _AttrList is attached.
        `attr` is the _singular_ form of the attribute name.
        `_items` is a private parameter for prepopulating an _AttrList.
          Usually this is one not attached to a Node, such as one
          derived from the .Xs notation.

        TODO: we currently do not rely on the backend to preserve ordering so
              lots of operations just ask the backend to totally resave the
              attribute list.
    '''
    if _items:
      list.__init__(self, _items)
    else:
      list.__init__(self)
    self.node = node
    self.attr = attr
    if node is not None:
      self.nodedb = node.nodedb

  def __delitemrefs(self, nodes):
    ''' Remove the reverse references of this attribute.
    '''
    if self.node is None:
      return
    for N in nodes:
      try:
        delref = N._delReference
      except AttributeError:
        continue
      if hasattr(N, 'name') and hasattr(N, 'type') and hasattr(N, 'nodedb'):
        delref(self.node, self.attr)

  def __additemrefs(self, nodes):
    ''' Add the reverse references of this attribute.
    '''
    if self.node is None:
      return
    for N in nodes:
      try:
        addref = N._addReference
      except AttributeError:
        continue
      if hasattr(N, 'name') and hasattr(N, 'type') and hasattr(N, 'nodedb'):
        addref(self.node, self.attr)

  def __str__(self):
    return str(list(self))

  def __repr__(self):
    if self.node is None:
      return ".%ss[...]" % (self.attr,)
    return "%s.%ss" % (str(self.node), self.attr)

  def __delitem__(self, index):
    if type(index) is int:
      items = (self[index],)
    else:
      items = itertools.islice(self, index)
    value = list.__delitem__(self, index)
    self.__delitemrefs(items)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __delslice__(self, i, j):
    del self[max(0, i):max(0, j):]

  def __iadd__(self, other):
    self.__additemrefs(other)
    value = list.__iadd__(self, other)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __imul__(self, other):
    oitems = list(self)
    value = list.__imul__(self, other)
    self.__additemrefs(self)
    self.__delitemrefs(oitems)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __setitem__(self, index, value):
    if type(index) is int:
      ovalues = (self[index],)
      values = (value,)
      index = slice(index, index+1)
    else:
      assert type(index) is slice
      ovalues = itertools.islice(self, index.start, index.stop, index.step)
      values = list(value)
    self.__delitemrefs(ovalues)
    list.__setitem__(self, index, values)
    self.__additemrefs(values)
    self.nodedb._backend.saveAttrs(self)

  def __setslice__(self, i, j, values):
    self[max(0, i):max(0, j):] = values

  def append(self, value, noBackend=False):
    if not noBackend:
      N = self.node
      self.nodedb._backend.extendAttr(N.type, N.name, self.attr, (value,))
    list.append(self, value)
    self.__additemrefs((value,))

  def extend(self, values, noBackend=False):
    # turn iterator into tuple
    if not noBackend and type(values) not in (list, tuple):
      values = tuple(values)
    if len(values) > 0:
      if not noBackend:
        N = self.node
        self.nodedb._backend.extendAttr(N.type, N.name, self.attr, values)
      list.extend(self, values)
      self.__additemrefs(values)

  def insert(self, index, value):
    value = list.insert(self, index, value)
    self.nodedb._backend.saveAttrs(self)
    self.__additemrefs((value,))
    return value

  def pop(self, index=-1):
    value = list.pop(self, index)
    self.nodedb._backend.saveAttrs(self)
    self.__delitemrefs((value,))
    return value

  def remove(self, value):
    list.remove(self, value)
    self.nodedb._backend.saveAttrs(self)
    self.__delitemrefs(value)

  def reverse(self, *args):
    list.reverse(self, *args)
    if self:
      self.nodedb._backend.saveAttrs(self)

  def sort(self, *args):
    list.sort(self, *args)
    if self:
      self.nodedb._backend.saveAttrs(self)

  def __getattr__(self, attr):
    ''' Using a .ATTR[s] attribute on an _AttrList indirects through
        the list members:
          .Xs Return a list of all the .Xs attributes of the list members.
              All members must support the .Xs attribution.
          .X  Return .Xs[0]. Requires len(.Xs) == 1.
    '''
    k, plural = parseUC_sAttr(attr)
    if k:
      hits = itertools.chain(*[ N[k] for N in self ])
      if plural:
        return _AttrList(node=None, attr=k, _items=hits)
      try:
        hit = the(hits)
      except IndexError, e:
        raise AttributeError, "%s.%s: %s" % (self, attr, e)
      return hit
    raise AttributeError, '.'.join([str(self), attr])

  def where(self, **kw):
    ''' Return a new _AttrList consisting of elements from this list where
        the attribute values equal the supplied keyword arguments.
    '''
    hits = []
    keys = kw.keys()
    for N in self:
      ok = True
      for k in keys:
        if getattr(N, k) != kw[k]:
          ok = False
          break
      if ok:
        hits.append(N)
    return _AttrList(node=None, attr=self.attr, _items=hits)

  def add(self, element):
    if element not in self:
      self.append(element)

  def update(self, *others):
    extras = []
    S = set(self)
    for o in others:
      for element in o:
        if element not in S:
          S.add(element)
          extras.append(element)
    self.extend(extras)

# we return a namedtuple from Node.references()
RefTuple = namedtuple('RefTuple', 'node attr nrefs')

class Node(dict):
  ''' A Node is a subclass of dict, mapping an attribute name to a list
      of values.
      It also supports object attributes of the form .ATTR and .ATTR[[e]s],
      meaning the single value of the attribute named "ATTR" or the _AttrList
      associated with that ATTR respectively. Use of the singular form
      requires that len(Node["ATTR"]) == 1. Use of the plural form may return
      an empty list; in this case hasattr(Node, "ATTR") will be false and
      Node["ATTR"] will raise a KeyError.
  '''

  def __init__(self, t, name, nodedb):
    self.type = str1(t) if t is not None else None
    self.name = name
    self.nodedb = nodedb
    self._reverse = {}  # maps (OtherNode, ATTR) => count

  def __nonzero__(self):
    ''' bool(Node) returns True, unlike a dict.
        Conversely, the NoNode singleton returns False from bool().
    '''
    return True

  def __call__(self, name):
    if self.name == '_':
      # this Node it the "type" metanode
      # .TYPE(key) is the at-need factory for a node
      return self.nodedb.make( (self.type, name) )
    raise TypeError, "only the NodeDB.TYPE metanode is callable"

  def seq(self):
    seqs = self.get('SEQ', (0,))
    i = seqs[0] + 1
    seqs[0] = i
    return i

  def seqNode(self):
    while True:
      key = (self.type, str(self.seq()))
      if key not in self.nodedb:
        return self.nodedb.make(key)

  def _addReference(self, onode, oattr):
    ''' Add a reference to this Node.
    '''
    key = (onode, str1(oattr))
    if key in self._reverse:
      self._reverse[key] += 1
    else:
      self._reverse[key] = 1

  def _delReference(self, onode, oattr):
    ''' Remove a reference to this Node.
    '''
    key = (onode, str1(oattr))
    if self._reverse[key] == 1:
      del self._reverse[key]
    else:
      self._reverse[key] -= 1

  def references(self, attr=None, type=None):
    ''' Generator to yield:
          onode, oattr, count
        for every other Node referring to this Node.
        The parameter `attr`, if supplied and not None,
        constrains the result to attributes matching that name.
        The parameter `type`, if supplied and not None,
        constrains the result to nodes of that type.
        `onode` is the other Node.
        `oattr` is the attribute containing the reference.
        `count` is the number of references to this Node in the attribute.
    '''
    for key, count in list(self._reverse.items()):
      onode, oattr = key
      if attr is None or oattr == attr:
        if type is None or onode.type == type:
          yield RefTuple(onode, oattr, count)

  def __repr__(self):
    return "%s:%s:%s" % (self.type, self.name, dict.__repr__(self))

  def __str__(self):
    return self.type+":"+self.name

  def html(self, prefix, label=None, ext=None):
    ''' An HTML token to transcribe for this Node.
    '''
    from .html import _noderef
    return _noderef(self, prefix=prefix, label=label, ext=ext)

  def __cmp__(self, other):
    ''' Nodes compare by type and then name and then id(Node).
        Note that two Nodes that compare equal can thus still return non-zero
        from cmp().
    '''
    try:
      c = cmp(self.type, other.type)
      if c != 0:
        return c
      c = cmp(self.name, other.name)
      if c != 0:
        return c
    except AttributeError:
      return 1
    return cmp( id(self), id(other) )

  def __eq__(self, other):
    ''' Two Nodes are equal if their name and type are equal.
        Attributes are not compared.
    '''
    if self is other:
      return True
    try:
      if self.name != other.name or self.type != other.type:
        return False
    except AttributeError:
      return False
    return True

  def __hash__(self):
    ''' Hash function, based on name, type and nodedb id.
    '''
    return hash(self.name)^hash(self.type)^id(self.nodedb)

  def get(self, k, default=None):
    ''' Fetch the item specified.
        Create an empty list if necessary.
    '''
    try:
      values = self[k]
    except KeyError:
      if default is None:
        default = ()
      values = _AttrList(self, k, _items=default)
      dict.__setitem__(self, k, values) # ensure this gets used later
    return values

  # __getitem__ goes directly to the dict implementation

  def __setitem__(self, item, new_values):
    ''' Set Node[item] = new_values.
        Unlike a normal dictionary, a shallow copy of new_values is stored,
        not new_values itself.
    '''
    k, plural = parseUC_sAttr(item)
    if k is None:
      raise KeyError, repr(item)
    assert not plural and k not in ('NAME', 'TYPE'), \
           "forbidden index: %r" % (item,)
    values = self.get(k)
    if len(values):
      # discard old values (removes reverse map)
      values[:]=[]
    new_values = list(new_values)
    if len(new_values):
      values.extend(new_values)

  def __delitem__(self, item):
    k, plural = parseUC_sAttr(item)
    if k is None or plural:
      raise KeyError, repr(item)
    dict.__setitem__(self, k, ())
    dict.__delitem__(self, k)

  def __getattr__(self, attr):
    ''' Support .ATTR[s] and .inTYPE.

        .inTYPE returns Nodes of type TYPE which refer to this Node.
        .ATTR returns the value of ATTR. There must be exactly one.
        .ATTRs returns the values of ATTR.
    '''
    # .inTYPE -> referring nodes of type TYPE
    if attr.startswith('in') and len(attr) > 2:
      k, plural = parseUC_sAttr(attr[2:])
      if k and not plural:
        return _AttrList(node=None, attr=None,
                         _items=[ N for N, a, c in self.references(type=k) ]
                        )

    # .ATTR and .ATTRs
    k, plural = parseUC_sAttr(attr)
    if k:
      values = self.get(k)
      if plural:
        return values
      if len(values) == 1:
        return values[0]
      if self.nodedb.noNode is None:
        raise AttributeError, "%s.%s (values=%s %s, len=%s)" % (self, attr, type(values), values, len(values))
      return self.nodedb.noNode

    raise AttributeError, str(self)+'.'+repr(attr)

  def __setattr__(self, attr, value):
    ''' Support .ATTR[s] = value[s].
    '''
    # forbid .inTYPE attribute setting
    if attr.startswith('in') and len(attr) > 2:
      k, plural = parseUC_sAttr(attr[2:])
      if k:
        raise ValueError, "setting .%s is forbidden" % (attr,)

    k, plural = parseUC_sAttr(attr)
    if k:
      # .ATTR[s] = value
      if not plural:
        value = (value,)
      self[k] = value
    else:
      dict.__setattr__(self, attr, value)

  def get0(self, attr, default=None):
    ''' Return the first item in self[attr], or `default`.
        `default` defaults to None.
    '''
    return get0(self.get(attr, ()), default=default)

  def apply(self, mapping):
    ''' Extend a Node's attributes with the values in mapping.
    '''
    for attr, values in mapping.items():
      self.get(attr).extend(values)

  def update(self, new_attrs, delete_missing=False):
    ''' Update this Node with new attributes, optionally removing
        extraneous attributes.
        `new_attrs` is a mapping from an attribute name to a value list.
        If `delete_missing` is supplied true, remove attribute not
        specified in `new_attrs`.
    '''
    with Pfx("%s.update" % (self,)):
      # add new attributes
      for attr in sorted(new_attrs.keys()):
        k, plural = parseUC_sAttr(attr)
        if not k:
          error("ignore non-ATTRs: %s", attr)
          continue
        if k not in self:
          info("new .%s=%s", k+'s', new_attrs[attr])
          self[k] = new_attrs[attr]

      # change or possibly remove old attributes
      old_attr_names = self.keys()
      old_attr_names.sort()
      for attr in old_attr_names:
        k, plural = parseUC_sAttr(attr)
        if not k:
          warning("ignore non-ATTRs old attr: %s", attr)
          continue
        if k.endswith('_ID'):
          error("ignoring bad old ATTR_ID: %s", attr)
          continue
        if k in new_attrs:
          nattrs = new_attrs[k]
          if self[k] != nattrs:
            info("set .%ss=%s", k, nattrs)
            self[k] = nattrs
        elif delete_missing:
          info("del .%ss", k)
          del self[k]

  def textdump(self, fp):
    ''' Write a vertical CSV dump of this node.
    '''
    self.nodedb.dump(fp, nodes=(self,))

  def assign(self, assignment, doCreate=False):
    from .text import commatext_to_values
    eqpos = assignment.find('=')
    pleqpos = assignment.find('+=')
    if eqpos > 0 and (pleqpos < 0 or pleqpos > eqpos):
      lvalue, rvalue = assignment.split('=', 1)
      append = False
    elif pleqpos > 0 and (eqpos < 0 or eqpos > pleqpos):
      lvalue, rvalue = assignment.split('+=', 1)
      append = True
    else:
      raise ValueError("assign: cannot choose = or +=: %s" % (assignment,))
    k, plural = parseUC_sAttr(lvalue)
    if not k:
      raise ValueError("invalid lvalue: %s" % (lvalue,))
    values = list(commatext_to_values(rvalue, self.nodedb, doCreate=doCreate))
    if append:
      self[k].extend(values)
    else:
      self[k] = values

  def substitute(self, s, safe=False):
    ''' Construct a CurlyTemplate for the supplied string `s`
        and return the result of its .substitute() method
        with this Node as 'self' in an EvalMapping.
    '''
    if False:
      # string.Template is buggy for custom patterns
      # so we use curly_substitute below
      from cs.curlytplt import CurlyTemplate, EvalMapping
      T = CurlyTemplate(s)
      M = EvalMapping(locals={ 'self': self, 'NL': "\n" })
      return T.safe_substitute(M) if safe else T.substitute(M)

    from cs.curlytplt import curly_substitute, EvalMapping
    M = EvalMapping(locals={ 'self': self })
    with Pfx(str(self)):
      mapfn = lambda foo: M[foo]
      return ''.join( [ curly_substitute(line,
                                         mapfn = lambda foo: M[foo],
                                         safe=safe,
                                         permute=True)
                        for line in s.splitlines(True)
                      ]
                    )

  def safe_substitute(self, s):
    return self.substitute(s, safe=True)

class _NoNode(Node):
  ''' If a NodeDB has a non-None .noNode attribute, normally it
      will be a singleton (per-class) instance of _NoNode, a dummy Node
      that permits .ATTR deferences for easy use.
      The distinguishing feature of a _NoNode is that bool(noNode) is False.
  '''

  def __init__(self, nodedb):
    Node.__init__(self, '_NoNode', '<_NoNode>', nodedb)

  def __nonzero__(self):
    ''' A NodeDB's NoNode returns False from bool().
        Other Nodes return True.
    '''
    return False

  def __str__(self):
    return "<NoNode>"

  def __getattr__(self, attr):
    ''' Return ourself (NoNode) from .ATTR.
        Otherwise behave like an empty Node.
    '''
    k, plural = parseUC_sAttr(attr)
    if not k or plural:
      return Node.__getattr__(self, attr)
    return self

class NodeDB(dict):

  _key = ('_', '_')     # index of metadata node

  def __init__(self, backend, readonly=False):
    dict.__init__(self)
    self._lock = allocate_lock()
    self.readonly = readonly
    self.noNode = None
    self.__attr_type_registry = {}
    self.__attr_scheme_registry = {}
    # run initially with no backend
    # load data from backend
    # attach backend to collect updates
    self._backend = _NoBackend()
    self.__nodesByType = {}
    if backend is not None:
      backend.set_nodedb(self)
      backend.apply_to(self)
      self._backend = backend

  def __str__(self):
    return "%s[_backend=%s]" % (type(self).__name__, self._backend)

  def useNoNode(self):
    ''' Enable "no node" mode.
        After this call, a reference to a missing .ATTR will return
        a dummy Node that can itself be deferenced further. This
        permits casual use of expressions like: someNode.THIS.THAT.
        The "no node" dummy node returns false in boolean contexts,
        unlike regular Nodes which are true.
    '''
    if self.noNode is None:
      self.noNode = _NoNode(self)

  class __AttrTypeRegistration(object):
    ''' An object to hold an attribute value type registration, with the
        following attributes:
          .type      the registered type
          .scheme    the scheme label to use for the type
          .totext    function to render a value as text
          .fromtext  function to compute a value from text
          .tobytes   function to render a value in a compact binary form
          .frombytes function to compute a value from the binary form
    '''

    def __init__(self,
                   t, scheme,
                   totext, fromtext,
                   tobytes, frombytes):
      '''
      '''
      self.type = t
      self.scheme = scheme
      self.totext = totext
      self.fromtext = fromtext
      self.tobytes = tobytes
      self.frombytes = tobytes

  def register_attr_type(self,
                         t, scheme,
                         totext, fromtext,
                         tobytes=None, frombytes=None):
    ''' Register an attribute value type for storage and retrieval in this
        NodeDB. This permits the storage of values that are not the
        presupported string, non-negative integer and Node types.
        Parameters:
          `t`, the value type to register
          `scheme`, the scheme label to use for the type
          `totext`, a function to render a value as text
          `fromtext`, a function to compute a value from text
          `tobytes`, a function to render a value in a compact binary form
          `frombytes`, a function to compute a value from the binary form
        If `tobytes` is None or unspecified, `totext` is used.
        If `frombytes` is None or unspecified, `fromtext` is used.
    '''
    reg = self.__attr_type_registry
    sch = self.__attr_scheme_registry
    assert t not in reg, "type %s already registered" % (t,)
    assert scheme not in sch, "scheme '%s' already registered" % (scheme,)
    if tobytes is None:
      tobytes = totext
    if frombytes is None:
      frombytes = fromtext
    R = NodeDB.__AttrTypeRegistration(t, scheme,
                                  totext, fromtext,
                                  tobytes, frombytes)
    reg[t] = R
    sch[scheme] = R

  def _createNode(self, t, name):
    ''' Factory method to make a new Node (or Node subclass instance).
        Subclasses of NodeDB should use this to make Nodes of appropriate
        types.
    '''
    return Node(t, name, self)

  def close(self):
    self._backend.close()

  def type(self, t):
    ''' Return the Nodes of the specified type `t`.
    '''
    return self.__nodesByType.get(t, ())

  def nodeByTypeName(self, t, name, doCreate=False):
    N = self.get( (t, name), doCreate=doCreate )
    if N is None:
      raise KeyError, "no Node with key (%s,%s)" % (t, name)
    return N

  def _noteNode(self, N):
    ''' Update the cross reference tables for a new Node.
    '''
    t = N.type
    byType = self.__nodesByType
    if t not in byType:
      byType[t] = set()
    byType[t].add(N)

  def _forgetNode(self, N):
    ''' Update the cross reference tables for removal of a Node.
    '''
    self.__nodesByType[N.type].remove(N)

  @property
  def types(self):
    ''' Return a list of the types in use.
    '''
    byType = self.__nodesByType
    return [ t for t in byType.keys() if byType[t] ]

  def __contains__(self, item):
    key = self.nodekey(item)
    return dict.__contains__(self, key)

  def get(self, item, default=None, doCreate=False):
    try:
      return self[item]
    except KeyError:
      if doCreate:
        if default is not None:
          raise ValueError, "doCreate is True but default is %r" % (default,)
        return self.newNode(item)
      return default

  def make(self, item):
    ''' make(item) does get(item, doCreate=True)
    '''
    return self.get(item, doCreate=True)

  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k:
      if plural:
        # .TYPEs
        # return Nodes of this type
        return self.__nodesByType.get(k, ())
      else:
        return self.make( (k, '_') )
    return getattr(super(NodeDB, self), attr)

  def __getitem__(self, item):
    try:
      key = nodekey(item)
    except ValueError, e:
      raise KeyError, "can't get key %s: %s" % (item, e)
    N = dict.__getitem__(self, key)
    assert isinstance(N, Node), "__getitem(%s) got non-Node: %r" % (item, N)
    return N

  def __setitem__(self, item, N):
    assert isinstance(N, Node), "tried to store non-Node: %r" % (N,)
    assert N.nodedb is self, "tried to store foreign Node: %r" % (N,)
    key = nodekey(item)
    assert key == (N.type, N.name), \
           "tried to store Node(%s:%s) as key (%s:%s)" \
             % (N.type, N.name, key[0], key[1])
    if key in self:
      self._forgetNode(self[key])
    dict.__setitem__(self, key, N)
    self._noteNode(N)

  def nodekey(self, *args):
    ''' Convert some sort of key to a (TYPE, NAME) tuple.
        Sanity check the values.
        Return (TYPE, NAME).
    '''
    if len(args) == 2:
      t, name = args
      assert type(t) is str
      assert type(name) is str
    elif len(args) == 1:
      item = args[0]
      if type(item) is str:
        # TYPE:NAME
        t, name = item.split(':', 1)
      else:
        # (TYPE, NAME)
        t, name = item
        assert type(t) is str
        assert type(name) is str
      assert t.isupper(), "TYPE should be upper case, got \"%s\"" % (t,)
      assert len(name) > 0
      k, plural = parseUC_sAttr(t)
      assert k is not None and not plural
    else:
      raise TypeError, "nodekey() takes (TYPE, NAME) args or a single arg: args=%s" % ( args, )
    return t, name

  def newNode(self, *args):
    ''' Create and register a new Node.
        Subclasses of NodeDB should override _createNode, not this method.
    '''
    t, name = nodekey(*args)
    with self._lock:
      if (t, name) in self:
        raise KeyError, 'newNode(%s, %s): already exists' % (t, name)
      N = self[t, name] = self._createNode(t, name)
      self._backend[t, name] = N
      self[t, name] = N
    return N

  @property
  def _s(self):
    ''' Nodes of type _.
    '''
    return self.__nodesByType.get('_', ())

  @property
  def _(self):
    ''' Obtain the metadata node, creating it if necessary.
    '''
    key = NodeDB._key
    try:
      return self[key]
    except KeyError:
      return self.newNode(key)

  @property
  def _s(self):
    ''' Return Nodes of type _.
    '''
    return self.__nodesByType.get('_', ())

  def seq(self):
    ''' Obtain a new sequence number for this NodeDB.
    '''
    return self._.seq()

  def seqNode(self, t=None):
    ''' Obtain a new Node of type `t` whose name is a db-unique decimal
        number. If `t` is missing or None, the type defaults to '_'.
    '''
    if t is None:
      t = '_'
    return self.make( (t, '_') ).seqNode()

  def otherDB(self, dburl):
    ''' Take a database URL or sequence number and return:
          ( db, seq )
        where db is the NodeDB and seq is the sequence number
        in this NodeDB associated with the other NodeDB.
    '''
    N_ = self._
    if type(dburl) is str:
      # a URL that attaches to a NodeDB
      db = NodeDBFromURL(dburl)
      for Ndb in N_.DBs:
        if dburl in Ndb.URLs:
          # the dburl is known - return now
          return db, Ndb.SEQ
      # new URL - record it for posterity
      Ndb = newNode('_NODEDB', str(ndb))
      seqnum = self.seq()
      Ndb.URL = dburl
      Ndb.SEQ = seqnum
      return db, seqnum

    # presume we were handed an int, the db sequence number
    ss = (dburl,)
    for Ndb in N_.DBs:
      if Ndb.SEQs == ss:
        return NodeDBFromURL(Ndb.URL)

    raise ValueError, "unknown DB sequence number: %s; _.DBs = %s" % (s, N_.DBs)

  def fromtoken(self, valuetxt, node=None, attr=None, doCreate=False):
    ''' Method to extract a token from the start of a string, for use
        in the named attribute `attr`.
        It is intended to be overridden by subclasses to add
        recognition for domain specific things such as IP addresses.
        overrides should fall back to this method if they do not
        recognise their special syntaxes.
        This is to be used to parse human friendly value lists.
        Conversely, totext() and fromtext() below are for external data storage.
    '''
    # NAME with implied TYPE
    if attr:
      m = re_NAME.match(valuetxt)
      if m and m.group() == valuetxt:
        if attr == "SUB"+node.type:
          try:
            value = self.nodeByTypeName(node.type, m.group(), doCreate=doCreate)
          except ValueError:
            value = m.group()
        else:
          try:
            value = self.nodeByTypeName(attr, m.group(), doCreate=doCreate)
          except ValueError:
            value = m.group()
        return value

    # TYPE:NAME
    m = re_NODEREF.match(valuetxt)
    if m and m.group() == valuetxt:
      value = self.nodeByTypeName(m.group(1), m.group(2), doCreate=doCreate)
      return value

    import cs.nodedb.text
    return cs.nodedb.text.fromtoken(valuetxt, self, doCreate=doCreate)

  def totoken(self, value, node=None, attr=None):
    ''' Convert a value to human friendly token.
    '''
    if isinstance(value, Node):
      # Node representation:
      # If value.type == FOO, Node is of type FOO and attr is SUBFOO,
      #   just write the value Node name
      if attr:
        if attr == "SUB"+node.type and value.type == node.type:
          return value.name
        # If value.type == FOO and attr == FOO,
        #   just write the value Node name
        if attr == value.type:
          return value.name
      return ":".join((value.type, value.name))

    import cs.nodedb.text
    return cs.nodedb.text.totoken(value)

  def totext(self, value):
    ''' Convert a value for external string storage.
          text        The string "text" for strings not commencing with a colon.
          ::text      The string ":text" for strings commencing with a colon.
          :TYPE:name  Node of specified name and TYPE in local NodeDB.
          :\+[0-9]+:TYPE:name Node of specified name and TYPE in other NodeDB.
          :[0-9]+     A non-negative integer.
          :scheme:text Encoding of value as "text" using its registered scheme.
    '''
    if isinstance(value, Node):
      assert ':' not in value.type, \
             "illegal colon in TYPE \"%s\"" % (value.type,)
      if value.nodedb is self:
        # Node from local NodeDB
        assert value.type[0].isupper(), "non-UPPER type: %s" % (value.type,)
        return ":%s:%s" % (value.type, value.name)
      odb, seqnum = self.nodedb.otherDB(value.nodedb.url)
      return ":+%d:%s:%s" % (seqnum, value.type, value.name)
    t = type(value)
    if t in StringTypes:
      if value.startswith(':'):
        return ':'+value
      return value
    if t is int:
      s = str(value)
      assert s[0].isdigit()
      return ':' + s
    R = self.__attr_type_registry.get(t, None)
    if R:
      scheme = R.scheme
      assert scheme[0].islower() and scheme.find(':',1) < 0, \
             "illegal scheme name: \"%s\"" % (scheme,)
      return ':'+scheme+':'+R.totext(value)
    raise ValueError, "can't totext( <%s> %s )" % (type(value),value)

  def fromtext(self, text, doCreate=True):
    ''' Convert a stored string into a value.
          text        The string "text" for strings not commencing with a colon.
          ::text      The string ":text" for strings commencing with a colon.
          :TYPE:name  Node of specified name and TYPE in local NodeDB.
          :\+[0-9]+:TYPE:name Node of specified name and TYPE in other NodeDB.
          :[0-9]+     A non-negative integer.
          :scheme:text Encoding of value as "text" using its registered scheme.
    '''
    if not text.startswith(':'):
      # plain string
      return text
    if len(text) < 2:
      raise ValueError, "unparsable text \"%s\"" % (text,)
    t1 = text[1:]
    if text.startswith('::'):
      # :string-with-leading-colon
      return t1
    if t1[0].isdigit():
      # :int
      return int(t1)
    if t1[0].isupper():
      # TYPE:NAME
      if t1.find(':', 1) < 0:
        raise ValueError, "bad :TYPE:NAME \"%s\"" % (text,)
      t, name = t1.split(':', 1)
      return self.nodeByTypeName(t, name, doCreate=doCreate)
    if t1[0].islower():
      # scheme:info
      if t1.find(':', 1) < 0:
        raise ValueError, "bad :scheme:info \"%s\"" % (text,)
      scheme, info = t1.split(':', 1)
      R = self.__attr_scheme_registry.get(scheme, None)
      if R:
        return R.fromtext(info)
      raise ValueError, "unsupported :scheme:info \"%s\"" % (text,)
    if t1[0] == '+':
      # :+seq:TYPE:NAME
      # obtain foreign Node from other NodeDB
      seqnum, t, name = t1[1:].split(':', 2)
      return self.otherDB(int(seqnum))[t, name]
    raise ValueError, "unparsable text \"%s\"" % (text,)

  def default_dump_nodes(self, typenames=None):
    ''' Yield the default sequence of Nodes to dump.
    '''
    if typenames is None:
      typenames = sorted(self.types)
    for t in typenames:
      nodes = sorted(getattr(self, t+'s'), by_name)
      for N in nodes:
        yield N

  def dump(self, fp, fmt='csv', nodes=None):
    ''' Write database nodes to the file `fp`.
        If `fmt` is "csv" (the default) use the archival "vertical"
        format.
        If `fmt` is "csv_wide" use the human friendly "wide" format.
    '''
    if nodes is None:
      nodes = self.default_dump_nodes()
    if fmt == 'csv':
      return self.dump_csv(fp, nodes)
    if fmt == 'csv_wide':
      return export_csv_wide(fp, nodes)
    raise ValueError, "unsupported format '%s'" % (fmt,)

  def dump_csv(self, fp, nodes):
    from .csvdb import write_csv_file
    write_csv_file(fp, self.nodedata(nodes))
    fp.flush()

  def nodedata(self, nodes=None):
    ''' Generator to yield:
          type, name, attrmap
        ready to be written to external storage such as a CSV file
        or to be applied to another NodeDB.
    '''
    if nodes is None:
      nodes = self.default_dump_nodes()
    for N in nodes:
      attrmap = {}
      for attr, values in N.iteritems():
        attrmap[attr] = [ self.totext(value) for value in values ]
      yield N.type, N.name, attrmap

  def apply_nodedata(self, nodedata, doCreate=True):
    ''' Load `nodedata`, a sequence of:
          type, name, attrmap
        into this NodeDB.
    '''
    with Pfx(str(self)):
      debug("apply_nodedata(..,doCreate=%s)...", doCreate)
      for t, name, attrmap in nodedata:
        debug("%s:%s", t, name)
        if doCreate:
          N = self.make( (t, name) )
        else:
          N = self[t, name]
        mapping = {}
        for attr, values in attrmap.items():
          ##debug("set %s:%s.%s", t, name, attr)
          mapping[attr] = [ self.fromtext(value) for value in values ]
        N.apply(mapping)

  def nodespec(self, spec, doCreate=False):
    ''' Generator that parses a comma separated string specifying
        Nodes and yields a sequence of Nodes.
    '''
    from .text import commatext_to_tokens
    for word in commatext_to_tokens(spec):
      with Pfx(word):
        if ':' in word:
          t, n = word.split(':', 1)
        else:
          t, n = nodekey(word)
        if '*' in t or '?' in t:
          debug("fnmatch(..,%s) against %s", t, list(self.types))
          typelist = sorted([ _ for _ in self.types if fnmatch.fnmatch(_, t) ])
        else:
          debug("literal \"%s\"", t)
          typelist = (t, )
        debug("type list = %s", typelist)
        for t in typelist:
          if '*' in n or '?' in n:
            namelist = sorted([ N.name for N in self.type(t) if fnmatch.fnmatch(N.name, n) ])
          else:
            namelist = (n, )
          if not namelist:
            warning("no Nodes of type \"%s\"", t)
          for name in namelist:
            N = self.get( (t, name), doCreate=doCreate )
            if N is None:
              warning("%s:%s: skip missing node (probably has no attributes)", t, name)
            else:
              yield N

  def do_command(self, args):
    op = args.pop(0)
    with Pfx(op):
      try:
        op_func = getattr(self, "cmd_" + op)
      except AttributeError:
        raise GetoptError, "unknown operation"
      return op_func(args)

  def cmd_update(self, args, fp=None):
    ''' update otherdb
          emit set commands to update otherdb with attributes and nodes in
          this db.
    '''
    xit = 0
    if fp is None:
      fp = sys.stdout
    if len(args) == 0:
      raise GetoptError("missing dburl")
    dburl = args.pop(0)
    if len(args) > 0:
      raise GetoptError("extra arguments after dburl '%s': %s" % (dburl, " ".join(args)))
    DB2 = NodeDBFromURL(dburl, readonly=True)
    nodes1 = list(self.default_dump_nodes())
    for N in nodes1:
      t, name = N.type, N.name
      N2 = DB2.get( (t, name), {} )
      attrs = N.keys()
      attrs.sort()
      for attr in attrs:
        values = N[attr]
        ovalues = N2.get(attr, ())
        if values != ovalues:
          fp.write("set %s:%s %s=%s\n" % (t, name, attr,
                                          ",".join( [ self.totoken(V, node=N2, attr=attr) for V in values ] )))
    return xit

  def cmd_dump(self, args, fp=None):
    ''' dump nodes...
          Textdump the named nodes.
    '''
    xit = 0
    if fp is None:
      fp = sys.stdout
    if not args:
      nodes = self.default_dump_nodes()
    else:
      nodes = self.nodespec(args.pop(0))
    first = True
    for node in nodes:
      with Pfx(node):
        if not first:
          fp.write('\n')
        node.textdump(fp)
        first=False
    return xit

  def cmd_dumpwide(self, args, fp=None):
    ''' dumpwide nodes...
          CSV dump the specified nodes in "wide" mode.
    '''
    args = list(args)
    xit = 0
    if fp is None:
      fp = sys.stdout
    if not args:
      nodes = self.default_dump_nodes()
    else:
      nodes = self.nodespec(args.pop(0))
    if not args:
      attrs = None
    else:
      attrtxt = args.pop(0)
      attrs = attrtxt.split(',')
    if args:
      raise GetoptError, "extra arguments after nodes and attrs: %s" % (args,)
    export_csv_wide(fp, nodes, attrs=attrs, all_nodes=True)
    return xit

  def cmd_editnode(self, args):
    ''' editnode TYPE:key
    '''
    if len(args) != 1:
      raise GetoptError("expected a single TYPE:key")
    N = self[args[0]]
    from cs.nodedb.text import editNode
    editNode(N, doCreate=True)
    return 0

  def cmd_edit(self, args, editfile=None):
    ''' edit nodespec [attrs...]
          Edit the specified nodes as a CSV file in "wide" mode.
    '''
    args = list(args)
    xit = 0
    if not args:
      nodes = self.default_dump_nodes()
    else:
      nodes = self.nodespec(args.pop(0), doCreate=True)
    if not args:
      attrs = None
    else:
      attrs = args
    edit_csv_wide(self, nodes=nodes, attrs=attrs, all_nodes=True)
    return xit

  def cmd_httpd(self, args, DBView=None):
    ''' httpd ipaddr:port
          Run an HTTP daemon on the specified address and port.
    '''
    xit = 0
    from .httpd import serve
    if len(args) == 0:
      raise GetoptError("missing ipaddr:port")
    ipaddr, port = args.pop(0).rsplit(':', 1)
    port = int(port)
    if len(args) > 0:
      raise GetoptError("extra arguments after %s:%s" % (ipaddr, port))
    self.readonly = True
    serve(self, ipaddr, port, DBView=DBView)
    return xit

  def cmd_import(self, args):
    ''' import other-db...
          Import node data from another NodeDB.
    '''
    debug("cmd_import(%s)", args)
    xit = 0
    if not args:
      raise GetoptError("missing arguments")
    for arg in args:
      debug("import %s...", arg)
      db = NodeDBFromURL(arg)
      self.apply_nodedata(db.nodedata())
    return xit

  def cmd_list(self, args):
    ''' list TYPE:*...
          Recite the extant nodes of the specified TYPE.
    '''
    xit = 0
    if not args:
      raise GetoptError("missing arguments")
    for arg in args:
      with Pfx('"%s"' % (arg,)):
        for N in self.nodespec(arg):
          print str(N)
    return xit

  def cmd_new(self, args, doCreate=True):
    ''' new TYPE:name [attr=values...]...
          Create the specified node and set attribute values.
    '''
    if len(args) == 0:
      raise GetoptError("missing TYPE:key")
    key=args.pop(0)
    with Pfx(key):
      if ':' not in key:
        raise GetoptError("bad key")
      nodetype, name = key.split(':',1)
      if not nodetype.isupper():
        raise GetoptError("bad key type \"%s\"" % nodetype)
      N = self.newNode(nodetype, name)
      for assignment in args:
        N.assign(assignment, doCreate=doCreate)
    return 0

  def cmd_print(self, attrs):
    N = self[attrs.pop(0)]
    for attr in attrs:
      print N.get0(attr, '')
    return 0

  def cmd_report(self, args):
    if len(args) != 1:
      raise GetoptError("expected a single TYPE:key")
    with Pfx(args[0]):
      self[args[0]].report(sys.stdout)
    return 0

  def cmd_set(self, args):
    ''' set [-C] TYPE:name [attr=values...]...
          Set attribute values.
          If -C is specified, create the node if missing.
    '''
    doCreate = False
    if args and args[0] == '-C':
      args.pop(0)
      doCreate = True
    if len(args) == 0:
      raise GetoptError("missing node spec")
    nodes = args.pop(0)
    if len(args) == 0:
      raise GetoptError("missing assignment")
    for assignment in args:
      for N in self.nodespec(nodes, doCreate=doCreate):
        with Pfx(N):
          N.assign(assignment)
    return 0

  def interactive(self, prompt):
    return NodeDB.Interactive(self, prompt)

  class Interactive(Cmd):

    def __init__(self, nodedb, prompt):
      Cmd.__init__(self)
      self.prompt = prompt
      self._nodedb = nodedb

    @property
    def usage(self):
      usage="Usage:"
      for command in sorted([ command[3:] for command in set(self.get_names())
                                          if command.startswith('do_')
                            ]):
        fn = getattr(self, 'do_'+command)
        fndoc = fn.__doc__
        if fndoc:
          fndoc = fndoc.rstrip()
        else:
          fndoc = " " + command + " [?ARGS?]"
        usage += "\n" + fndoc
      return usage

    def get_names(self):
      names = Cmd.get_names(self)
      # add do_* for cmd_* names in nodedb
      names.extend( 'do_'+name[4:] for name in dir(self._nodedb) if name.startswith('cmd_') )
      return names

    def __getattr__(self, attr):
      if attr.startswith('do_'):
        op = attr[3:]
        try:
          fn = getattr(self._nodedb, 'cmd_'+op)
        except AttributeError:
          # fall back to superclass
          pass
        else:
          from .text import get_commatexts
          def do_op(argline):
            try:
              args = list(get_commatexts(argline))
            except ValueError, e:
              error(str(e))
            else:
              with Pfx(op):
                try:
                  fn(args)
                except GetoptError, e:
                  error(str(e))
                except ValueError, e:
                  exception(str(e))
            return False
          do_op.__doc__ = fn.__doc__
          return do_op
      return Cmd.__getattr__(self, attr)

    def do_exit(self, line):
      return True

    def do_quit(self, line):
      return True

_NodeDBsByURL = {}

def NodeDBFromURL(url, readonly=False, klass=None):
  ''' Factory method to return singleton NodeDB instances.
  '''
  ##print >>sys.stderr, "NodeDBFromURL: url =", url
  if klass is None:
    klass = NodeDB

  if url in _NodeDBsByURL:
    return _NodeDBsByURL[url]

  if url.startswith('/'):
    # filesystem pathname
    # recognise some extensions and recurse
    # otherwise reject
    base = os.path.basename(url)
    _, ext = os.path.splitext(base)
    if ext == '.csv':
      return NodeDBFromURL('file-csv://'+url, readonly=readonly, klass=klass)
    if ext == '.tch':
      return NodeDBFromURL('file-tch://'+url, readonly=readonly, klass=klass)
    if ext == '.sqlite':
      return NodeDBFromURL('sqlite://'+url, readonly=readonly, klass=klass)
    raise ValueError, "unsupported NodeDB URL: "+url

  markpos = url.find('://')
  if markpos > 0:
    markend = markpos + 3
    scheme = url[:markpos]
    if scheme == 'file-csv':
      from cs.nodedb.csvdb import Backend_CSVFile
      dbpath = url[markend:]
      backend = Backend_CSVFile(dbpath, readonly=readonly)
      db = klass(backend, readonly=readonly)
      _NodeDBsByURL[url] = db
      return db

    if scheme == 'file-tch':
      from cs.nodedb.tokcab import Backend_TokyoCabinet
      dbpath = url[markend:]
      backend = Backend_TokyoCabinet(dbpath, readonly=readonly)
      db = klass(backend, readonly=readonly)
      _NodeDBsByURL[url] = db
      return db

    if scheme == 'sqlite' or scheme == 'mysql':
      # TODO: direct sqlite support, skipping SQLAlchemy?
      # TODO: mysql: URLs will leak user and password - strip first for key
      ####assert not url.startswith('sqlite:///:memory:'), \
      ####       "sorry, \"%s\" isn't a singleton URL" % (url,)
      from cs.nodedb.sqla import Backend_SQLAlchemy
      dbpath = url
      backend = Backend_SQLAlchemy(dbpath, readonly=readonly)
      db = klass(backend, readonly=readonly)
      _NodeDBsByURL[url] = db
      db.url = url
      return db

  if os.path.isfile(url):
    if url.endswith('.csv'):
      return NodeDBFromURL('file-csv://'+os.path.abspath(url), readonly=readonly, klass=klass)
    if url.endswith('.tch'):
      return NodeDBFromURL('file-tch://'+os.path.abspath(url), readonly=readonly, klass=klass)

  raise ValueError, "unsupported NodeDB URL: "+url

if __name__ == '__main__':
  import cs.nodedb.node_tests
  cs.nodedb.node_tests.selftest(sys.argv)
