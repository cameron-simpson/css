#!/usr/bin/python -tt
#
# pylint: disable=too-many-lines

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

from abc import ABC, abstractmethod
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from functools import partial
import json
import re
from threading import RLock
from uuid import UUID, uuid4

from cs.deco import strable
from cs.lex import isUC_, parseUC_sAttr, cutprefix, r, snakecase
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method
from cs.seq import Seq
from cs.sharedfile import SharedAppendLines

__version__ = '20220626'

DISTINFO = {
    'description':
    "Facilities for mappings and objects associated with mappings.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.seq',
        'cs.sharedfile>=20211208',
    ],
}

def column_name_to_identifier(column_name, snake_case=False):
  ''' The default function used to convert raw column names in
      `named_row_tuple`, for example from a CSV file, into Python
      indentifiers.

      If `snake_case` is true (default `False`) produce snake cased
      identifiers instead of merely lowercased identifiers.
      This means that something like 'redLines' will become `red_lines`
      instead of `redlines`.
  '''
  name = re.sub(r'\W+', '_', column_name).strip('_')
  if snake_case:
    name = snakecase(name)
  else:
    name = name.lower()
  return name

# pylint: disable=too-many-statements
def named_row_tuple(
    *column_names,
    class_name=None,
    computed=None,
    column_map=None,
    snake_case=False,
    mixin=None,
):
  ''' Return a `namedtuple` subclass factory derived from `column_names`.
      The primary use case is using the header row of a spreadsheet
      to key the data from the subsequent rows.

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
  if class_name is None:
    class_name = 'NamedRow'
  column_names = list(column_names)
  if computed is None:
    computed = {}
  if column_map is None:
    column_map = partial(column_name_to_identifier, snake_case=snake_case)
  elif not callable(column_map):
    attr_seq = Seq(start=1)
    mapping = column_map

    def column_map(raw_column_name):
      ''' Function to map raw column names to the values in the
          supplied mapping.
      '''
      attr_name = mapping.get(raw_column_name, None)
      if attr_name is None:
        attr_name = '_' + str(attr_seq())
      return attr_name

  if mixin is None:
    mixin = object
  # compute candidate tuple attributes from the column names
  name_attributes = [
      None if name is None else column_map(name) for name in column_names
  ]
  # final tuple attributes are the nonempty name_attributes_
  attributes = [attr for attr in name_attributes if attr]
  if len(attributes) == len(name_attributes):
    attributes = name_attributes

  _NamedRow = namedtuple(class_name, attributes)

  # pylint: disable=too-few-public-methods
  class NamedRow(_NamedRow, mixin):
    ''' A namedtuple to store row data.

        In addition to the normal numeric indices, the tuple may
        also be indexed by the attribute names or the column names.

        The class has the following attributes:
        * `attributes_`: the attribute names of each tuple in order
        * `computed_`: a mapping of `str` to functions of `self`; these
          values are also available via `__getitem__`
        * `names_`: the originating name strings
        * `name_attributes_`: the computed attribute names corresponding to the
          `names_`; there may be empty strings in this list
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
    del i, name, attr  # pylint: disable=undefined-loop-variable
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
            # pylint: disable=raise-missing-from
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

# pylint: disable=too-many-arguments
def named_column_tuples(
    rows,
    class_name=None,
    column_names=None,
    computed=None,
    preprocess=None,
    mixin=None,
    snake_case=False,
):
  ''' Process an iterable of data rows, usually with the first row being
      column names.
      Return a generated `namedtuple` factory (the row class)
      and an iterable of instances of the namedtuples for each row.

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
        It should return the row (possibly modified), or `None` to drop the
        row.
      * `mixin`: an optional mixin class for the generated `namedtuple` subclass
        to provide extra methods or properties

      The context object passed to `preprocess` has the following attributes:
      * `.cls`: the generated namedtuple subclass;
        this is useful for obtaining things like the column names
        or column indices;
        this is `None` when preprocessing the header row, if any
      * `.index`: attribute with the row's enumeration, which counts from `0`
      * `.previous`: the previously accepted row's `namedtuple`,
        or `None` if there is no previous row;
        this is useful for differencing

      Rows may be flat iterables in the same order as the column names
      or mappings keyed on the column names.

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
          >>> rowtype, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(a=1, b=11, c='one'), NamedRow(a=2, b=22, c='two')]

      Human readable column headings:

          >>> data1 = [
          ...   ('Index', 'Value Found', 'Descriptive Text'),
          ...   (1, 11, "one"),
          ...   (2, 22, "two"),
          ... ]
          >>> rowtype, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(index=1, value_found=11, descriptive_text='one'), NamedRow(index=2, value_found=22, descriptive_text='two')]

      Rows which are mappings:

          >>> data1 = [
          ...   ('a', 'b', 'c'),
          ...   (1, 11, "one"),
          ...   {'a': 2, 'c': "two", 'b': 22},
          ... ]
          >>> rowtype, rows = named_column_tuples(data1)
          >>> print(list(rows))
          [NamedRow(a=1, b=11, c='one'), NamedRow(a=2, b=22, c='two')]

      CSV export with unused padding columns:

          >>> data1 = [
          ...   ('a', 'b', 'c', '', ''),
          ...   (1, 11, "one"),
          ...   {'a': 2, 'c': "two", 'b': 22},
          ...   [3, 11, "three", '', 'dropped'],
          ... ]
          >>> rowtype, rows = named_column_tuples(data1, 'CSV_Row')
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
          >>> rowtype, rows = named_column_tuples(data1, mixin=Mixin)
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
      mixin=mixin,
      snake_case=snake_case,
  )
  rowtype = next(gen)
  return rowtype, gen

# pylint: disable=too-many-arguments
def _named_column_tuples(
    rows,
    class_name=None,
    column_names=None,
    computed=None,
    preprocess=None,
    mixin=None,
    snake_case=False,
):
  if column_names is None:
    rowtype = None
  else:
    rowtype = named_row_tuple(
        *column_names,
        class_name=class_name,
        computed=computed,
        mixin=mixin,
        snake_case=snake_case,
    )
    yield rowtype
    tuple_attributes = rowtype.attributes_
    name_attributes = rowtype.name_attributes_
  previous = None
  for index, row in enumerate(rows):
    if preprocess:
      row = preprocess(_nct_Context(rowtype, index, previous), row)
      if row is None:
        continue
    if rowtype is None:
      column_names = row
      rowtype = named_row_tuple(
          *column_names,
          class_name=class_name,
          computed=computed,
          mixin=mixin,
          snake_case=snake_case,
      )
      yield rowtype
      tuple_attributes = rowtype.attributes_
      name_attributes = rowtype.name_attributes_
      continue
    if callable(getattr(row, 'get', None)):
      # flatten a mapping into a list ordered by column_names
      row = [row.get(k) for k in column_names]
    if tuple_attributes is not name_attributes:
      # drop items from columns with empty names
      row = [item for item, attr in zip(row, name_attributes) if attr]
    named_row = rowtype(*row)
    yield named_row
    previous = named_row

def dicts_to_namedtuples(dicts, class_name, keys=None):
  ''' Scan an iterable of `dict`s,
      yield a sequence of `namedtuple`s derived from them.

      Parameters:
      * `dicts`: the `dict`s to scan and convert, an iterable
      * `class_name`: the name for the new `namedtuple` class
      * `keys`: optional iterable of `dict` keys of interest;
        if omitted then the `dicts` are scanned in order to learn the keys

      Note that if `keys` is not specified
      this generator prescans the `dicts` in order to learn their keys.
      As a consequence, all the `dicts` will be kept in memory
      and no `namedtuple`s will be yielded until after that prescan completes.
  '''
  if keys is None:
    keys = set()
    ds = []
    for d in dicts:
      ds.append(d)
      keys.update(d.keys())
    keys = sorted(keys)
  else:
    ds = dicts
    keys = list(keys)
  factory = named_row_tuple(*keys, class_name=class_name)
  for d in ds:
    yield factory(*[d.get(dk) for dk in keys])

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

  def __repr__(self):
    return "%s(%r, keepEmpty=%s)" % (
        type(self).__name__, self.__M, self.keepEmpty
    )

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
      return self.__M.get(k, ())
    try:
      value, = self.__M[k]
    except (ValueError, KeyError):
      raise AttributeError("%s.%s" % (type(self).__name__, k))
    return value

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      self.__dict__[attr] = value
      return
    if plural:
      if isinstance(type, str):
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
      raise AttributeError(attr)
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
        Raise `KeyError` if the key in not found in any mapping.
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

      *DEPRECATED*: I now recommend my `cs.context.stackattrs` context
      manager for most uses; it may be applied to any object instead of
      requiring use of this class.

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
          >>> with S.stack(x=4):
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
    ''' Mapping method returning a list of the names.
    '''
    values = self._values
    return list(k for k in values.keys() if values[k])

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
        # pylint: disable=raise-missing-from
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
        # pylint: disable=raise-missing-from
        raise KeyError(key)
      with Pfx("%s._fallback(%r)", type(self).__name__, key):
        try:
          v = fallback_func(key)
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
      # pylint: disable=raise-missing-from
      raise KeyError(key)
    if not vs:
      del self._values[key]
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
    for k in kw:
      ovs.append((k, self.push(k, kw[k])))
    return dict(reversed(ovs))

  @contextmanager
  def stack(self, *a, **kw):
    ''' Context manager which saves and restores the current state.
        Any parameters are passed to `update()` after the save
        but before the yield.
    '''
    old_values = self._values
    self._values = defaultdict(
        list, ((k, list(v)) for k, v in self._values.items())
    )
    self.update(*a, **kw)
    try:
      yield
    finally:
      self._values = old_values

# pylint: disable=too-few-public-methods
class AttrableMappingMixin(object):
  ''' Provides a `__getattr__` which accesses the mapping value.
  '''

  def __getattr__(self, attr):
    ''' Unknown attributes are obtained from the mapping entries.

        Note that this first consults `self.__dict__`.
        For many classes that is redundant, but subclasses of
        `dict` at least seem not to consult that with attribute
        lookup, likely because a pure `dict` has no `__dict__`.
    '''
    # try self.__dict__ first - this is because it appears that
    # getattr(dict,...) does not consult __dict__
    try:
      _d = self.__dict__
    except AttributeError:
      # no __dict__? skip this step
      pass
    else:
      try:
        return _d[attr]
      except KeyError:
        pass
    try:
      return self[attr]
    except KeyError:
      try:
        return self.ATTRABLE_MAPPING_DEFAULT
      except AttributeError:
        names_msgs = []
        ks = list(self.keys())
        if ks:
          names_msgs.append('keys=' + ','.join(sorted(ks)))
        dks = self.__dict__.keys()
        if dks:
          names_msgs.append('__dict__=' + ','.join(sorted(dks)))
        # pylint: disable=raise-missing-from
        raise AttributeError(
            "%s.%s (attrs=%s)" % (
                type(self).__name__,
                attr,
                ','.join(names_msgs),
            )
        )

class JSONableMappingMixin:
  ''' Provide `.from_json()`, `.as_json()` and `.append_ndjson()` methods,
      and `__str__=as_json` and a `__repr__`.
  '''

  @classmethod
  def from_json(cls, js):
    ''' Prepare a `dict` from JSON text.

      If the class has `json_object_hook` or `json_object_pairs_hook`
      attributes these are used as the `object_hook` and
      `object_pairs_hook` parameters respectively of the `json.loads()` call.
    '''
    d = cls()
    d.update(
        json.loads(
            js,
            object_hook=getattr(cls, 'json_object_hook', None),
            object_pairs_hook=getattr(cls, 'json_object_pairs_hook', None)
        )
    )
    return d

  def as_json(self):
    ''' Return the `dict` transcribed as JSON.

        If the instance's class has `json_default` or `json_separators` these
        are used for the `default` and `separators` parameters of the `json.dumps()`
        call.
        Note that the default value of `separators` is `(',',':')`
        which produces the most compact JSON form.
    '''
    cls = type(self)
    return json.dumps(
        self,
        default=getattr(cls, 'json_default', None),
        separators=getattr(cls, 'json_separators', (',', ':'))
    )

  @strable(open_func=lambda filename: open(filename, 'a'))
  def append_ndjson(self, f):
    ''' Append this object to `f`, a file or filename, as NDJSON.
    '''
    f.write(self.as_json())
    f.write('\n')

  __str__ = as_json

  def __repr__(self):
    return type(self).__name__ + str(self)

class IndexedSetMixin(ABC):
  ''' A base mixin to provide `.by_`* attributes
      which index records from an autoloaded backing store,
      which might be a file or might be another related data structure.
      The records are themselves key->value mappings, such as `dict`s.

      The primary key name is provided by the `.IndexedSetMixin__pk`
      class attribute, to be provided by subclasses.

      Note that this mixin keeps the entire loadable mapping in memory.

      Note that this does not see subsequent changes to loaded records
      i.e. changing the value of some record[k]
      does not update the index associated with the .by_k attribute.

      Subclasses must provide the following attributes and methods:
      * `IndexedSetMixin__pk`: the name of the primary key;
        it is an error for multiple records to have the same primary key
      * `scan`: a generator method to scan the backing store
        and yield records, used for the inital load of the mapping
      * `add_backend(record)`: add a new record to the backing store;
        this is called from the `.add(record)` method
        after indexing to persist the record in the backing store

      See `UUIDNDJSONMapping` and `UUIDedDict` for an example subclass
      indexing records from a newline delimited JSON file.
  '''

  IndexedSetMixin__pk = ''

  @abstractmethod
  def scan(self):
    ''' Scan the mapping records (themselves mappings) from the backing store,
        which might be a file or another related data structure.
        Yield each record as scanned.
    '''
    raise NotImplementedError("scan")

  def add(self, record, exists_ok=False):
    ''' Add a record to the mapping.

        This indexes the record against the various `by_`* indices
        and then calls `self.add_backend(record)`
        to save the record to the backing store.
    '''
    pk_name = self.IndexedSetMixin__pk
    assert pk_name, "empty .IndexedSetMixin__pk"
    # ensure the primary mapping is loaded
    pk_mapping = getattr(self, 'by_' + pk_name)
    with self._lock:
      if not exists_ok and record[pk_name] in pk_mapping:
        raise KeyError(
            "self.by_%s: key %r already present" % (pk_name, record[pk_name])
        )
      for map_name in self.__indexed:
        try:
          k = record[map_name]
        except KeyError:
          pass
        else:
          by_map = getattr(self, 'by_' + map_name)
          by_map[k] = record
      self.add_backend(record)

  def __getattr__(self, attr):
    field_name = cutprefix(attr, 'by_')
    if field_name is not attr:
      with Pfx("%s.%s", type(self).__name__, attr):
        pk_name = self.IndexedSetMixin__pk
        assert pk_name, "empty .IndexedSetMixin__pk"
        by_pk = 'by_' + pk_name
        indexed = self.__indexed
        with self._lock:
          if field_name in indexed:
            return self.__dict__[attr]
          by_map = {}
          if field_name == pk_name:
            records = self.scan()
          else:
            records = getattr(self, by_pk).values()
          # load the
          ##warned = set()
          i = 0
          for i, record in enumerate(records, 1):
            try:
              field_value = record[field_name]
            except KeyError:
              if field_name == pk_name:
                warning("no primary key %r: %r", field_name, record)
              continue
            ##if field_value in by_map:
            ##  if field_value not in warned:
            ##    warning("multiple records for %r", field_value)
            ##    warned.add(field_value)
            by_map[field_value] = record
          setattr(self, attr, by_map)
          indexed.add(field_name)
          if field_name == pk_name:
            self.__scan_length = i
      return by_map
    if attr == '_IndexedSetMixin__indexed':
      # .__indexed
      indexed = self.__indexed = set()
      return indexed
    try:
      supergetattr = super().__getattr__
    except AttributeError:
      return getattr(type(self), attr)
    else:
      return supergetattr(attr)

  def __len__(self):
    ''' The length of the primary key mapping.
    '''
    return len(getattr(self, 'by_' + self.IndexedSetMixin__pk))

  @property
  def scan_length(self):
    ''' The number of records encountered during the backend scan.
    '''
    # ensure the mapping has been scanned
    getattr(self, 'by_' + self.IndexedSetMixin__pk)
    # return the length of the scan
    return self.__scan_length

  @scan_length.setter
  def scan_length(self, length):
    ''' Set the scan length, called by `UUIDNDJSONMapping.rewrite_backend`.
    '''
    self.__scan_length = length

class IndexedMapping(IndexedSetMixin):
  ''' Interface to a mapping with `IndexedSetMixin` style `.by_*` attributes.
  '''

  def __init__(self, mapping=None, pk='id'):
    ''' Initialise the `IndexedMapping`.

        Parameters:
        * `mapping`: the mapping to wrap; a new `dict` will be made if not specified
        * `pk`: the primary key of the mapping, default `'id'`
    '''
    if mapping is None:
      mapping = {}
    self.mapping = mapping
    self.IndexedSetMixin__pk = pk
    self._lock = RLock()

  def scan(self):
    ''' The records from the mapping.
    '''
    return self.mapping.values()

  def add_backend(self, record):
    ''' Save `record` in the mapping.
    '''
    self.mapping[record[self.IndexedSetMixin__pk]] = record

class AttrableMapping(dict, AttrableMappingMixin):
  ''' A `dict` subclass using `AttrableMappingMixin`.
  '''

class UUIDedDict(dict, JSONableMappingMixin, AttrableMappingMixin):
  ''' A handy `dict` subtype providing the basis for mapping classes
      indexed by `UUID`s.

      The `'uuid'` attribute is always a `UUID` instance.
  '''

  json_object_pairs_hook = lambda k, v: UUID(v) if k == 'uuid' else v
  json_default = lambda v: str(v) if isinstance(v, UUID) else v

  def __init__(self, _d=None, **kw):
    ''' Initialise the `UUIDedDict`,
        generating a `'uuid'` key value if omitted.
    '''
    if _d is None:
      dict.__init__(self)
    else:
      dict.__init__(self, _d)
    self.update(**kw)
    try:
      uu = self['uuid']
    except KeyError:
      self['uuid'] = uuid4()
    else:
      # force .uuid to be a UUID
      if isinstance(uu, str):
        self['uuid'] = UUID(uu)
      else:
        assert isinstance(uu, UUID)

  @property
  def uuid(self):
    ''' A UUID from `self['uuid']`.

        This does a sanity check that the stored value is a `UUID`,
        but primarily exists to support the setter,
        which promotes `str` to `UUID`, thus also validating UUID strings.
    '''
    uu = self['uuid']
    assert isinstance(uu, UUID)
    return uu

  @uuid.setter
  def uuid(self, new_uuid):
    ''' Set the UUID for the dict.

        The `new_uuid` should be either a `UUID` or a valid UUID string,
        which is converted into a `UUID`.
    '''
    uu = new_uuid if isinstance(new_uuid, UUID) else UUID(new_uuid)
    self['uuid'] = uu

class RemappedMappingProxy:
  ''' A proxy for another mapping
      with translation functions between the external keys
      and the keys used inside the other mapping.

      Example:

          >>> proxy = RemappedMappingProxy(
          ...   {},
          ...   lambda key: 'prefix.' + key,
          ...   lambda subkey: cutprefix('prefix.', subkey))
          >>> proxy['key'] = 1
          >>> proxy['key']
          1
          >>> proxy.mapping
          {'prefix.key': 1}
          >>> list(proxy.keys())
          ['key']
          >>> proxy.subkey('key')
          'prefix.key'
          >>> proxy.key('prefix.key')
          'key'
  '''

  def __init__(self, mapping, to_subkey, from_subkey):
    self.mapping = mapping
    self._to_subkey = to_subkey
    self._from_subkey = from_subkey
    self._mapped_keys = {}
    self._mapped_subkeys = {}

  def _self_check(self):
    assert len(self._mapped_keys) == len(self._mapped_subkeys)
    assert set(self._mapped_keys.values()) == set(self._mapped_subkeys.keys())
    assert set(self._mapped_keys.keys()) == set(self._mapped_subkeys.values())
    for subk, k in self._mapped_subkeys.items():
      with Pfx("subkey %r vs key %r", subk, k):
        assert self._mapped_keys[k] == subk, (
            "subkey %r => %r: self._mapped_keys[key]:%r != subkey:%r" %
            (subk, k, self._mapped_keys[k], subk)
        )
    return True

  @pfx_method
  def subkey(self, key):
    ''' Return the internal key for `key`.
    '''
    try:
      subk = self._mapped_keys[key]
    except KeyError:
      subk = self._to_subkey(key)
      if subk in self._mapped_subkeys:
        warning(
            "no self._mapped_keys[key=%r], but we do have self._mapped_subkeys[subk=%r] => %r",
            key, subk, self._mapped_subkeys[subk]
        )
        assert self._mapped_subkeys[subk] == key, \
            "self._mapped_subkeys[subk=%r]:%r != key:%r" % (subk,self._mapped_subkeys[subk],key)
      self._mapped_keys[key] = subk
      self._mapped_subkeys[subk] = key
    return subk

  @pfx_method
  def key(self, subk):
    ''' Return the external key for `subk`.
    '''
    try:
      k = self._mapped_subkeys[subk]
    except KeyError:
      k = self._from_subkey(subk)
      assert k not in self._mapped_keys
      self._mapped_keys[k] = subk
      self._mapped_subkeys[subk] = k
    return k

  def keys(self, select_key=None):
    ''' Yield the external keys.
    '''
    subkey_iter = self.mapping.keys()
    if select_key is not None:
      subkey_iter = filter(
          lambda subkey: select_key(self.key(subkey)), subkey_iter
      )
    return map(self.key, subkey_iter)

  def __contains__(self, key):
    return self.subkey(key) in self.mapping

  def __getitem__(self, key):
    return self.mapping[self.subkey(key)]

  def get(self, key, default=None):
    ''' Return the value for key `key` or `default`.
    '''
    try:
      return self[key]
    except KeyError:
      return default

  def __setitem__(self, key, v):
    self.mapping[self.subkey(key)] = v

  def __delitem__(self, key):
    del self.mapping[self.subkey(key)]

class PrefixedMappingProxy(RemappedMappingProxy):
  ''' A proxy for another mapping
      operating on keys commencing with a prefix.
  '''

  def __init__(self, mapping, prefix):
    # precompute function references
    unprefixify = self.unprefixify_key
    prefixify = self.prefixify_subkey
    super().__init__(
        mapping,
        to_subkey=lambda key: prefixify(key, prefix),
        from_subkey=lambda subk: unprefixify(subk, prefix),
    )
    self.prefix = prefix

  def __str__(self):
    return "%s[%r](prefix=%r,mapping=%s)" % (
        type(self).__name__, type(self).__mro__, self.prefix, r(self.mapping)
    )

  @staticmethod
  def prefixify_subkey(subk, prefix):
    ''' Return the external (prefixed) key from a subkey `subk`.
    '''
    assert not subk.startswith(prefix)
    return prefix + subk

  @staticmethod
  def unprefixify_key(key, prefix):
    ''' Return the internal subkey (unprefixed) from the external `key`.
    '''
    assert key.startswith(prefix), \
        "key:%r does not start with prefix:%r" % (key, prefix)
    return cutprefix(key, prefix)

  # pylint: disable=arguments-differ
  def keys(self):
    ''' Yield the post-prefix suffix of the keys in `self.mapping`.
    '''
    return super().keys(
        select_key=lambda subkey: subkey.startswith(self.prefix)
    )
