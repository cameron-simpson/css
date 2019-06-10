#!/usr/bin/python -tt

''' Facilities for mappings and objects associated with mappings.

    In particular `named_column_tuple(column_names)`,
    a function returning a factory
    for namedtuples subclasses derived from the supplied column names,
    and `named_column_tuples(rows)`,
    a function returning a namedtuple factory and an iterable of instances
    containing the row data.
    These are used by the `csv_import` and `xl_import` functions
    from `cs.csvutils`.
'''

from collections import defaultdict, namedtuple
from contextlib import contextmanager
from functools import partial
import re
from cs.sharedfile import SharedAppendLines
from cs.lex import isUC_, parseUC_sAttr
from cs.logutils import warning
from cs.py3 import StringTypes
from cs.seq import the

DISTINFO = {
    'description':
    "Facilities for mappings and objects associated with mappings.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires':
    ['cs.sharedfile', 'cs.lex', 'cs.logutils', 'cs.py3', 'cs.seq'],
}

def named_row_tuple(*column_names, **kw):
  ''' Return a namedtuple subclass factory derived from `column_names`.

      Parameters:
      * `column_names`: an iterable of `str`, such as the heading columns
        of a CSV export
      * `class_name`: optional keyword parameter specifying the class name
      * `computed`: optional keyword parameter providing a mapping
        of `str` to functions of `self`; these strings are available
        via `__getitem__`
      * `mixin`: an optional mixin class for the generated namedtuple subclass
        to provide extra methods or properties

      The tuple's attributes are computed by converting all runs
      of nonalphanumerics
      (as defined by the `re` module's "\\W" sequence)
      to an underscore, lowercasing and then stripping
      leading and trailing underscores.

      In addition to the normal numeric indices, the tuple may
      also be indexed by the attribute names or the column names.

      The new class has the following additional attributes:
      * `attributes_`: the attribute names of each tuple in order
      * `names_`: the originating name strings
      * `name_attributes_`: the computed attribute names corresponding to the
        `names`; there may be empty strings in this list
      * `attr_of_`: a mapping of column name to attribute name
      * `name_of_`: a mapping of attribute name to column name
      * `index_of_`: a mapping of column names and attributes their tuple indices

      Examples:

          >>> T = named_row_tuple('Column 1', '', 'Column 3', ' Column 4', 'Column 5 ', '', '', class_name='Example')
          >>> T.attributes_
          ['column_1', 'column_3', 'column_4', 'column_5']
          >>> row = T('val1', 'dropped', 'val3', 4, 5, 6, 7)
          >>> row
          Example(column_1='val1', column_3='val3', column_4=4, column_5=5)
  '''
  class_name = kw.pop('class_name', None)
  computed = kw.pop('computed', None)
  mixin = kw.pop('mixin', None)
  if kw:
    raise ValueError("unexpected keyword arguments: %r" % (kw,))
  if class_name is None:
    class_name = 'NamedRow'
  column_names = list(column_names)
  if computed is None:
    computed = {}
  if mixin is None:
    mixin = object
  # compute candidate tuple attributes from the column names
  name_attributes = [
      re.sub(r'\W+', '_', name).strip('_').lower() for name in column_names
  ]
  # final tuple attributes are the nonempty name_attributes_
  attributes = [attr for attr in name_attributes if attr]
  if len(attributes) == len(name_attributes):
    attributes = name_attributes

  _NamedRow = namedtuple(class_name, attributes)

  class NamedRow(_NamedRow, mixin):
    ''' A namedtuple to store row data.

        In addition to the normal numeric indices, the tuple may
        also be indexed by the attribute names or the column names.

        The class has the following attributes:
        * `attributes_`: the attribute names of each tuple in order
        * `computed_`: a mapping of str to functions of `self`; these
          values are also available via `__getitem__`
        * `names_`: the originating name strings
        * `name_attributes_`: the computed attribute names corresponding to the
          `names`; there may be empty strings in this list
        * `attr_of_`: a mapping of column name to attribute name
        * `name_of_`: a mapping of attribute name to column name
        * `index_of_`: a mapping of column names and attributes their tuple indices
    '''

    attributes_ = attributes
    computed_ = computed
    names_ = column_names
    name_attributes_ = name_attributes
    attr_of_ = {}  # map name to attr, omits those with empty/missing attrs
    name_of_ = {}  # map attr to name
    index_of_ = {}  # map name or attr to index
    i = 0
    for name, attr in zip(names_, name_attributes_):
      if attr:
        attr_of_[name] = attr
        name_of_[attr] = name
        index_of_[name] = i
        i += 1
    del i, name, attr
    index_of_.update((s, i) for i, s in enumerate(attributes_))

    def __getitem__(self, key):
      if isinstance(key, int):
        i = key
      elif isinstance(key, str):
        try:
          i = self.index_of_[key]
        except KeyError:
          try:
            method = getattr(self, key)
          except AttributeError:
            func = self.computed_.get(key)
            if func is not None:
              return func(self)
            raise RuntimeError("no method or func for key %r" % (key,))
          else:
            return method()
      else:
        raise TypeError("expected int or str, got %s" % (type(key),))
      return _NamedRow.__getitem__(self, i)

  NamedRow.__name__ = class_name

  # make a factory to avoid tromping the namedtuple __new__/__init__
  def factory(*row):
    ''' Factory function to create a NamedRow from a raw row.
    '''
    if attributes is not name_attributes:
      row = [item for item, attr in zip(row, name_attributes) if attr]
    return NamedRow(*row)

  # pretty up the factory for external use
  factory.__name__ = 'factory(%s)' % (NamedRow.__name__,)
  factory.attributes_ = NamedRow.attributes_
  factory.names_ = NamedRow.names_
  factory.name_attributes_ = NamedRow.name_attributes_
  factory.attr_of_ = NamedRow.attr_of_
  factory.name_of_ = NamedRow.name_of_
  factory.index_of_ = NamedRow.index_of_
  return factory

# Context class for preprocessing rows.
# Its attributes have the following meanings:
#
#   .cls        attribute with the generated namedtuple subclass; this
#               is useful for obtaining things like the column named or column
#               indices; this is None when preprocessing the header row, if any
#
#   .index      attribute with the row's enumeration, which counts
#               from 0
#
#   .previous   the previously accepted row's namedtuple, or None
#               if there is no previous row
#
_nct_Context = namedtuple('Context', 'cls index previous')

def named_column_tuples(
    rows,
    class_name=None,
    column_names=None,
    computed=None,
    preprocess=None,
    mixin=None
):
  ''' Process an iterable of data rows, usually with the first row being
      column names.
      Return a generated namedtuple factory and an iterable
      of instances of the namedtuples for each row.

      Parameters:
      * `rows`: an iterable of rows, each an iterable of data values.
      * `class_name`: option class name for the namedtuple class
      * `column_names`: optional iterable of column names used as the basis for
        the namedtuple. If this is not provided then the first row from
        `rows` is taken to be the column names.
      * `computed`: optional mapping of str to functions of `self`
      * `preprocess`: optional callable to modify CSV rows before
        they are converted into the namedtuple.  It receives a context
        object an the data row.
        It should return the row (possibly modified), or None to drop the
        row.
      * `mixin`: an optional mixin class for the generated namedtuple subclass
        to provide extra methods or properties

      The context object passed to `preprocess` has the following attributes:
      * `.cls`: attribute with the generated namedtuple subclass;
        this is useful for obtaining things like the column names
        or column indices;
        this is `None` when preprocessing the header row, if any
      * `.index`: attribute with the row's enumeration, which counts from 0
      * `.previous`: the previously accepted row's namedtuple,
        or `None` if there is no previous row

      Rows may be flat iterables in the same order as the column
      names or mappings keyed on the column names.

      If the column names contain empty strings they are dropped
      and the corresponding data row entries are also dropped. This
      is very common with spreadsheet exports with unused padding
      columns.

      Typical human readable column headings, also common in
      speadsheet exports, are lowercased and have runs of whitespace
      or punctuation turned into single underscores; trailing
      underscores then get dropped.

      Basic example:

          >>> data1 = [
          ...   ('a', 'b', 'c'),
          ...   (1, 11, "one"),
          ...   (2, 22, "two"),
          ... ]
          >>> cls, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(a=1, b=11, c='one'), NamedRow(a=2, b=22, c='two')]

      Human readable column headings:

          >>> data1 = [
          ...   ('Index', 'Value Found', 'Descriptive Text'),
          ...   (1, 11, "one"),
          ...   (2, 22, "two"),
          ... ]
          >>> cls, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(index=1, value_found=11, descriptive_text='one'), NamedRow(index=2, value_found=22, descriptive_text='two')]

      Rows which are mappings:

          >>> data1 = [
          ...   ('a', 'b', 'c'),
          ...   (1, 11, "one"),
          ...   {'a': 2, 'c': "two", 'b': 22},
          ... ]
          >>> cls, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(a=1, b=11, c='one'), NamedRow(a=2, b=22, c='two')]

      CSV export with unused padding columns:

          >>> data1 = [
          ...   ('a', 'b', 'c', '', ''),
          ...   (1, 11, "one"),
          ...   {'a': 2, 'c': "two", 'b': 22},
          ...   [3, 11, "three", '', 'dropped'],
          ... ]
          >>> cls, rows = named_column_tuples(data1, 'CSV_Row')
          >>> print(list(rows))
          [CSV_Row(a=1, b=11, c='one'), CSV_Row(a=2, b=22, c='two'), CSV_Row(a=3, b=11, c='three')]

      A mixin class providing a `test1` method and a `test2` property:

          >>> class Mixin(object):
          ...   def test1(self):
          ...     return "test1"
          ...   @property
          ...   def test2(self):
          ...     return "test2"
          >>> data1 = [
          ...   ('a', 'b', 'c'),
          ...   (1, 11, "one"),
          ...   {'a': 2, 'c': "two", 'b': 22},
          ... ]
          >>> cls, rows = named_column_tuples(data1, mixin=Mixin)
          >>> rows = list(rows)
          >>> rows[0].test1()
          'test1'
          >>> rows[0].test2
          'test2'

  '''
  gen = _named_column_tuples(
      rows,
      class_name=class_name,
      column_names=column_names,
      computed=computed,
      preprocess=preprocess,
      mixin=mixin
  )
  cls = next(gen)
  return cls, gen

def _named_column_tuples(
    rows,
    class_name=None,
    column_names=None,
    computed=None,
    preprocess=None,
    mixin=None
):
  if column_names is None:
    cls = None
  else:
    cls = named_row_tuple(
        *column_names, class_name=class_name, computed=computed, mixin=mixin
    )
    yield cls
    tuple_attributes = cls.attributes_
    name_attributes = cls.name_attributes_
  previous = None
  for index, row in enumerate(rows):
    if preprocess:
      row = preprocess(_nct_Context(cls, index, previous), row)
      if row is None:
        continue
    if cls is None:
      column_names = row
      cls = named_row_tuple(
          *column_names, class_name=class_name, computed=computed, mixin=mixin
      )
      yield cls
      tuple_attributes = cls.attributes_
      name_attributes = cls.name_attributes_
      continue
    if callable(getattr(row, 'get', None)):
      # flatten a mapping into a list ordered by column_names
      row = [row.get(k) for k in column_names]
    if tuple_attributes is not name_attributes:
      # drop items from columns with empty names
      row = [item for item, attr in zip(row, name_attributes) if attr]
    named_row = cls(*row)
    yield named_row
    previous = named_row

class SeqMapUC_Attrs(object):
  ''' A wrapper for a mapping from keys
      (matching the regular expression `^[A-Z][A-Z_0-9]*$`)
      to tuples.

      Attributes matching such a key return the first element
      of the sequence (and requires the sequence to have exactly on element).
      An attribute `FOOs` or `FOOes`
      (ending in a literal 's' or 'es', a plural)
      returns the sequence (`FOO` must be a key of the mapping).
  '''

  def __init__(self, M, keepEmpty=False):
    self.__M = M
    self.keepEmpty = keepEmpty

  def __str__(self):
    kv = []
    for k, value in self.__M.items():
      if isUC_(k):
        if len(value) != 1:
          k = k + 's'
        else:
          value = value[0]
      kv.append((k, value))
    return '{%s}' % (", ".join(["%s: %r" % (k, value) for k, value in kv]))

  def __hasattr__(self, attr):
    k, _ = parseUC_sAttr(attr)
    if k is None:
      return k in self.__dict__
    return k in self.__M

  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return self.__dict__[k]
    if plural:
      if k not in self.__M:
        return ()
      return self.__M[k]
    return the(self.__M[k])

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      self.__dict__[attr] = value
      return
    if plural:
      if isinstance(type, StringTypes):
        raise ValueError(
            "invalid string %r assigned to plural attribute %r" %
            (value, attr)
        )
      T = tuple(value)
      if len(T) == 0 and not self.keepEmpty:
        if k in self.__M:
          del self.__M[k]
      else:
        self.__M[k] = T
    else:
      self.__M[k] = (value,)

  def __delattr__(self, attr):
    k, _ = parseUC_sAttr(attr)
    if k is None:
      del self.__dict__[k]
    else:
      del self.__M[k]

class UC_Sequence(list):
  ''' A tuple-of-nodes on which `.ATTRs` indirection can be done,
      yielding another tuple-of-nodes or tuple-of-values.
  '''

  def __init__(self, Ns):
    ''' Initialise from an iterable sequence.
    '''
    list.__init__(self, Ns)

  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k is None or not plural:
      return list.__getattr__(self, attr)
    values = tuple(self.__attrvals(attr))
    if values and not isNode(values[0]):
      return values
    return _Nodes(values)

  def __attrvals(self, attr):
    for N in self.__nodes:
      for v in getattr(N, attr):
        yield v

class AttributableList(list):
  ''' An `AttributableList` maps unimplemented attributes
      onto the list members and returns you a new `AttributableList`
      with the results, ready for a further dereference.

      Example:

          >>> class C(object):
          ...   def __init__(self, i):
          ...     self.i = i
          >>> Cs = [ C(1), C(2), C(3) ]
          >>> AL = AttributableList( Cs )
          >>> print(AL.i)
          [1, 2, 3]
  '''

  def __init__(self, initlist=None, strict=False):
    ''' Initialise the list.

        The optional parameter `initlist` initialises the list
        as for a normal list.

        The optional parameter `strict`, if true, causes list elements
        lacking the attribute to raise an AttributeError. If false,
        list elements without the attribute are omitted from the results.
    '''
    if initlist:
      list.__init__(self, initlist)
    else:
      list.__init__(self)
    self.strict = strict

  def __getattr__(self, attr):
    if self.strict:
      result = [getattr(item, attr) for item in self]
    else:
      result = []
      for item in self:
        try:
          r = getattr(item, attr)
        except AttributeError:
          pass
        else:
          result.append(r)
    return AttributableList(result)

class MethodicalList(AttributableList):
  ''' A MethodicalList subclasses a list and maps unimplemented attributes
      into a callable which calls the corresponding method on each list members
      and returns you a new `MethodicalList` with the results, ready for a
      further dereference.

      Example:

          >>> n = 1
          >>> class C(object):
          ...   def __init__(self):
          ...     global n
          ...     self.n = n
          ...     n += 1
          ...   def x(self):
          ...     return self.n
          ...
          >>> Cs=[ C(), C(), C() ]
          >>> ML = MethodicalList( Cs )
          >>> print(ML.x())
          [1, 2, 3]
  '''

  def __init__(self, initlist=None, strict=False):
    ''' Initialise the list.

        The optional parameter `initlist` initialises the list
        as for a normal list.

        The optional parameter `strict`, if true, causes list elements
        lacking the attribute to raise an AttributeError. If false,
        list elements without the attribute are omitted from the results.
    '''
    AttributableList.__init__(self, initlist=initlist, strict=strict)

  def __getattr__(self, attr):
    return partial(self.__call_attr, attr)

  def __call_attr(self, attr):
    if self.strict:
      submethods = [getattr(item, attr) for item in self]
    else:
      submethods = []
      for item in self:
        try:
          submethod = getattr(item, attr)
        except AttributeError:
          pass
        else:
          submethods.append(submethod)
    return MethodicalList(method() for method in submethods)

class FallbackDict(defaultdict):
  ''' A dictlike object that inherits from another dictlike object;
      this is a convenience subclass of `defaultdict`.
  '''

  def __init__(self, otherdict):
    '''
    '''
    defaultdict.__init__(self)
    self.__otherdict = otherdict

  def __missing__(self, key):
    if key not in self:
      return self.__otherdict[key]
    raise KeyError(key)

class MappingChain(object):
  ''' A mapping interface to a sequence of mappings.

      It does not support `__setitem__` at present;
      that is expected to be managed via the backing mappings.
  '''

  def __init__(self, mappings=None, get_mappings=None):
    ''' Initialise the MappingChain.

        Parameters:
        * `mappings`: initial sequence of mappings, default None.
        * `get_mappings`: callable to obtain the initial sequence of

        Exactly one of `mappings` or `get_mappings` must be provided.
    '''
    if mappings is not None:
      if get_mappings is None:
        mappings = list(mappings)
        self.get_mappings = lambda: mappings
      else:
        raise ValueError(
            "cannot supply both mappings (%r) and get_mappings (%r)" %
            (mappings, get_mappings)
        )
    else:
      if get_mappings is not None:
        self.get_mappings = get_mappings
      else:
        raise ValueError("one of mappings or get_mappings must be specified")

  def __getitem__(self, key):
    ''' Return the first value for `key` found in the mappings.
        Raise KeyError if the key in not found in any mapping.
    '''
    for mapping in self.get_mappings():
      try:
        value = mapping[key]
      except KeyError:
        continue
      return value
    raise KeyError(key)

  def get(self, key, default=None):
    ''' Get the value associated with `key`, return `default` if missing.
    '''
    try:
      return self[key]
    except KeyError:
      return default

  def __contains__(self, key):
    try:
      _ = self[key]
    except KeyError:
      return False
    return True

  def keys(self):
    ''' Return the union of the keys in the mappings.
    '''
    ks = set()
    for mapping in self.get_mappings():
      ks.update(mapping.keys())
    return ks

class SeenSet(object):
  ''' A set-like collection with optional backing store file.
  '''

  def __init__(self, name, backing_path=None):
    self.name = name
    self.backing_path = backing_path
    self.set = set()
    if backing_path is not None:
      # create file if missing, also tests access permission
      with open(backing_path, "a"):
        pass
      self._backing_file = SharedAppendLines(
          backing_path, importer=self._add_foreign_line
      )
      self._backing_file.ready()

  def _add_foreign_line(self, line):
    # EOF markers, discard
    if line is None:
      return
    if not line.endswith('\n'):
      warning("%s: adding unterminated line: %s", self, line)
    s = line.rstrip()
    self.add(s, foreign=True)

  def add(self, s, foreign=False):
    ''' Add the value `s` to the set.

        Parameters:
        * `s`: the value to add
        * `foreign`: default `False`:
          whether the value came from an outside source,
          usually a third party addition to the backing file;
          this prevents appending the value to the backing file.
    '''
    # avoid needlessly extending the backing file
    if s in self.set:
      return
    self.set.add(s)
    if not foreign and self.backing_path:
      self._backing_file.put(s)

  def __contains__(self, item):
    return item in self.set

class StackableValues(object):
  ''' A collection of named stackable values with the latest value
      available as an attribute.

      Note that names conflicting with methods are not available
      as attributes and must be accessed via `__getitem__`.
      As a matter of practice, in addition to the mapping methods,
      avoid names which are verbs or which begin with an underscore.

      Example:

          >>> S = StackableValues()
          >>> print(S)
          StackableValues()
          >>> S.push('x', 1)
          >>> print(S)
          StackableValues(x=1)
          >>> print(S.x)
          1
          >>> S.push('x', 2)
          1
          >>> print(S.x)
          2
          >>> S.x = 3
          >>> print(S.x)
          3
          >>> S.pop('x')
          3
          >>> print(S.x)
          1
          >>> with S.stack('x', 4):
          ...   print(S.x)
          ...
          4
          >>> print(S.x)
          1
          >>> S.update(x=5)
          {'x': 1}
  '''

  def __init__(self, *ms, **kw):
    self._values = defaultdict(list)
    if ms or kw:
      self.update(*ms, **kw)

  def __str__(self):
    return (
        "%s(%s)" % (
            type(self).__name__,
            ','.join("%s=%s" % (k, v) for k, v in sorted(self.items()))
        )
    )

  def __repr__(self):
    return (
        "%s(%s)" %
        (type(self), ','.join("%r=%r" % (k, v) for k, v in self.items()))
    )

  def keys(self):
    ''' Mapping method returning an iterable of the names.
    '''
    return self._values.keys()

  def values(self):
    ''' Mapping method returning an iterable of the values.
    '''
    for key in self.keys():
      try:
        v = self[key]
      except KeyError:
        pass
      else:
        yield v

  def items(self):
    ''' Mapping method returning an iterable of (name, value) tuples.
    '''
    for key in self.keys():
      try:
        v = self[key]
      except KeyError:
        pass
      else:
        yield key, v

  def __getattr__(self, attr):
    ''' Convenience: present the top value of key `attr` as an attribute.

        Note that attributes `push`, `pop` and the mapping method names
        are shadowed by the instance methods
        and should be accessed with the traditional `[]` key dereference.
    '''
    if attr.startswith('_'):
      raise AttributeError(attr)
    try:
      v = self[attr]
    except KeyError as e:
      raise AttributeError(attr) from e
    return v

  def __setattr__(self, attr, value):
    ''' For nonunderscore attributes, replace the top element of the stack.
    '''
    if attr.startswith('_'):
      self.__dict__[attr] = value
    else:
      try:
        vs = self._values[attr]
      except KeyError:
        raise AttributeError(attr)
      else:
        if vs:
          vs[-1] = value
        else:
          vs.append(value)

  def __getitem__(self, key):
    ''' Return the top value for `key` or raise `KeyError`.
    '''
    vs = self._values[key]
    if vs:
      v = vs[-1]
    else:
      try:
        fallback_func = self._fallback
      except AttributeError:
        # no fallback function
        raise KeyError(key)
      try:
        return fallback_func(key)
      except Exception as e:
        raise KeyError("fallback for %r fails: %s" % (key, e)) from e
    return v

  def get(self, key, default=None):
    ''' Get the top value for `key`, or `default`.
    '''
    try:
      v = self[key]
    except KeyError:
      v = default
    return v

  def push(self, key, value):
    ''' Push a new `value` for `key`.
        Return the previous value
        or `None` if this is the first value for `key`.
    '''
    vs = self._values.get(key, [])
    v = vs[-1] if vs else None
    self._values[key].append(value)
    return v

  def pop(self, key):
    ''' Pop and return the latest value for `key`.
    '''
    vs = self._values[key]
    try:
      v = vs.pop()
    except IndexError:
      raise KeyError(key)
    return v

  def update(self, *ms, **kw):
    ''' Update the mapping like `dict.update` method.
        Return a mapping with the preupdate values
        of the updated keys.
    '''
    ovs = []
    for m in ms:
      try:
        mkeys = m.keys
      except AttributeError:
        for k, v in m:
          ovs.append((k, self.push(k, v)))
      else:
        for k in mkeys():
          ovs.append((k, self.push(k, m[k])))
    for k, v in kw.items():
      ovs.append((k, self.push(k, kw[k])))
    return dict(reversed(ovs))

  @contextmanager
  def stack(self, key, value):
    ''' Context manager which pushes and pops a new `value` for `key`.
    '''
    self.push(key, value)
    try:
      yield
    finally:
      self.pop(key)
