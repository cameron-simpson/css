#!/usr/bin/env python

''' Assorted utility functions to support working with SQLAlchemy.
'''

from contextlib import contextmanager
import logging
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.attributes import flag_modified
from icontract import require
from cs.deco import decorator
from cs.py.func import funccite, funcname
from cs.resources import MultiOpenMixin

DISTINFO = {
    'description':
    'Assorted utility functions to support working with SQLAlchemy.',
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Database",
    ],
    'install_requires': [
        'icontract',
        'sqlalchemy',
        'cs.deco',
        'cs.py.func',
        'cs.resources',
    ],
}

@require(lambda orm, session: orm is not None or session is not None)
def with_session(func, *a, orm=None, session=None, **kw):
  ''' Call `func(*a,session=session,**kw)`, creating a session if required.
      The function `func` runs within a transaction,
      nested if the session already exists.

      This is the inner mechanism of `@auto_session` and
      `ORM.auto_session_method`.

      Parameters:
      * `func`: the function to call
      * `a`: the positional parameters
      * `orm`: optional ORM class with a `.session()` context manager method
        such as the `ORM` base class supplied by this module.
      * `session`: optional existing ORM session

      One of `orm` or `session` must be not `None`; if `session`
      is `None` then one is made from `orm.session()` and used as
      a context manager.

      The `session` is also passed to `func` as
      the keyword parameter `session` to support nested calls.
  '''
  if session:
    # run the function inside a savepoint in the supplied session
    with session.begin_nested():
      return func(*a, session=session, **kw)
  if not orm:
    raise ValueError("no orm supplied from which to make a session")
  with orm.session() as new_session:
    return func(*a, session=new_session, **kw)

def auto_session(func):
  ''' Decorator to run a function in a session if one is not presupplied.
      The function `func` runs within a transaction,
      nested if the session already exists.
  '''

  @require(lambda orm, session: orm is not None or session is not None)
  def wrapper(*a, orm=None, session=None, **kw):
    ''' Prepare a session if one is not supplied.
    '''
    return with_session(func, *a, orm=orm, session=session, **kw)

  wrapper.__name__ = "@auto_session(%s)" % (funccite(func,),)
  wrapper.__doc__ = func.__doc__
  return wrapper

@contextmanager
def push_log_level(level):
  ''' Temporarily set the level of the default SQLAlchemy logger to `level`.
      Yields the logger.

      *NOTE*: this is not MT safe - competing Threads can mix log levels up.
  '''
  logger = logging.getLogger('sqlalchemy.engine')
  old_level =logger.level
  logger.setLevel(level)
  yield logger
  logger.setLevel(old_level)

@decorator
def log_level(func, level=None):
  ''' Decorator to run `func` at the specified logging `level`, default `logging.DEBUG`.
  '''
  if level is None:
    level = logging.DEBUG
  def wrapper(*a,**kw):
    ''' Push the desired log level and run the function.
    '''
    with push_log_level(level):
      return func(*a,**kw)
  wrapper.__name__="@log_level(%s,%s)" %(func,level)
  return wrapper

class ORM(MultiOpenMixin):
  ''' A convenience base class for an ORM class.

      This defines a `.Base` attribute which is a new `DeclarativeBase`
      and provides various Session related convenience methods.
      It is also a `MultiOpenMixin` subclass
      supporting nested open/close sequences and use as a context manager.

      Subclasses must define the following:
      * `.Session`: a factory in their own `__init__`, typically
        `self.Session=sessionmaker(bind=engine)`
      * `.startup` and `.shutdown` methods to support the `MultiOpenMixin`,
        even if these just `pass`
  '''

  def __init__(self):
    self.Base = declarative_base()
    self.Session = None
    MultiOpenMixin.__init__(self)

  @contextmanager
  def session(self):
    ''' Context manager to issue a new session and close it down.

        Note that this performs a `COMMIT` or `ROLLBACK` at the end.
    '''
    with self:
      new_session = self.Session()
      try:
        yield new_session
        new_session.commit()
      except:
        new_session.rollback()
        raise
      finally:
        new_session.close()

  @staticmethod
  def auto_session(method):
    ''' Decorator to run a method in a session derived from this ORM
        if a session is not presupplied.
    '''

    def wrapper(self, *a, session=None, **kw):
      ''' Prepare a session if one is not supplied.
      '''
      return with_session(method, self, *a, session=session, orm=self, **kw)

    wrapper.__name__ = "@ORM.auto_session(%s)" % (funcname(method),)
    wrapper.__doc__ = method.__doc__
    return wrapper

def orm_auto_session(method):
  ''' Decorator to run a method in a session derived from `self.orm`
      if a session is not presupplied.
      Intended to assist classes with a `.orm` attribute.
  '''

  def wrapper(self, *a, session=None, **kw):
    ''' Prepare a session if one is not supplied.
    '''
    return with_session(method, self, *a, session=session, orm=self.orm, **kw)

  wrapper.__name__ = "@orm_auto_session(%s)" % (funcname(method),)
  wrapper.__doc__ = method.__doc__
  return wrapper

@require(
    lambda field_name: field_name and not field_name.startswith('.') and
    not field_name.endswith('.') and '..' not in field_name
)
def find_json_field(column_value, field_name, *, infill=False):
  ''' Descend a JSONable Python object `column_value`
      to `field_name`.
      Return `column_value` (possibly infilled), `final_field`, `final_field_name`.

      This supports database row columns which are JSON columns.

      Parameters:
      * `column_value`: the original value of the column
      * `field_name`: the field within the column to locate
      * `infill`: optional keyword parameter, default `False`.
        If true,
        `column_value` and its innards will be filled in as `dict`s
        to allow deferencing the `field_name`.

      The `field_name` is a `str`
      consisting of a period (`'.'`) separated sequence of field parts.
      Each field part becomes a key to index the column mapping.
      These keys are split into the leading field parts
      and the final field part,
      which is returned as `final_field_name` above.

      The `final_field` return value above
      is the mapping within which `final_field_value` may lie
      and where `final_field_value` may be set.
      Note: it may not be present.

      If a leading key is missing and `infill` is true
      the corresponding part of the `column_value` is set to an empty dictionary
      in order to allow deferencing the leading key.
      This includes the case when `column_value` itself is `None`,
      which is why the `column_value` is part of the return.

      If a leading key is missing and `infill` is false
      this function will raise a `KeyError`
      for the portion of the `field_name` which failed.

      Examples:

          >>> find_json_field({'a':{'b':{}}}, 'a.b')
          ({'a': {'b': {}}}, {'b': {}}, 'b')
          >>> find_json_field({'a':{}}, 'a.b')
          ({'a': {}}, {}, 'b')
          >>> find_json_field({'a':{'b':{}}}, 'a.b.c.d')
          Traceback (most recent call last):
              ...
          KeyError: 'a.b.c'
          >>> find_json_field({'a':{'b':{}}}, 'a.b.c.d', infill=True)
          ({'a': {'b': {'c': {}}}}, {}, 'd')
          >>> find_json_field(None, 'a.b.c.d')
          Traceback (most recent call last):
              ...
          KeyError: 'a'
          >>> find_json_field(None, 'a.b.c.d', infill=True)
          ({'a': {'b': {'c': {}}}}, {}, 'd')
  '''
  field_parts = field_name.split('.')
  if column_value is None:
    if infill:
      column_value = {}
    else:
      raise KeyError(field_parts[0])
  final_field = column_value
  leading_keys = []
  while len(field_parts) > 1:
    field_part = field_parts.pop(0)
    leading_keys.append(field_part)
    if field_part not in final_field and infill:
      final_field[field_part] = {}
    try:
      final_field = final_field[field_part]
    except KeyError as e:
      raise KeyError('.'.join(leading_keys)) from e
  return column_value, final_field, field_parts[0]

def get_json_field(column_value, field_name, *, default=None):
  ''' Return the value of `field_name` from `column_value`
      or a defaault if the field is not present.

      Parameters:
      * `column_value`: the original value of the column
      * `field_name`: the field within the column to locate
      * `default`: default value to return if the field is not present,
        default: `None`

      Examples:

          >>> get_json_field({'a': 1}, 'a')
          1
          >>> get_json_field({'b': 1}, 'a')
          >>> get_json_field({'a': {}}, 'a.b')
          >>> get_json_field({'a': {'b': 2}}, 'a.b')
          2
  '''
  try:
    _, final_field, final_field_name = find_json_field(
        column_value, field_name
    )
  except KeyError:
    return None
  else:
    return final_field.get(final_field_name, default)

def set_json_field(column_value, field_name, value, *, infill=False):
  ''' Set a new `value` for `field_name` of `column_value`.
      Return the new `column_value`.

      Parameters:
      * `column_value`: the original value of the column
      * `field_name`: the field within the column to locate
      * `value`: the value to store as `field_name`
      * `infill`: optional keyword parameter, default `False`.
        If true,
        `column_value` and its innards will be filled in as `dict`s
        to allow deferencing the `field_name`.

      As with `find_json_field`,
      a true `infill` may modify `column_value` to provide `field_name`
      which is why this function returns the new `column_value`.

      Examples:

          >>> set_json_field({'a': 2}, 'a', 3)
          {'a': 3}
          >>> set_json_field({'a': 2, 'b': {'c': 5}}, 'b.c', 4)
          {'a': 2, 'b': {'c': 4}}
          >>> set_json_field({'a': 2}, 'b.c', 4)
          Traceback (most recent call last):
              ...
          KeyError: 'b'
          >>> set_json_field({'a': 2}, 'b.c', 4, infill=True)
          {'a': 2, 'b': {'c': 4}}
          >>> set_json_field(None, 'b.c', 4, infill=True)
          {'b': {'c': 4}}
  '''
  column_value, final_field, final_field_name = find_json_field(
      column_value, field_name, infill=infill
  )
  final_field[final_field_name] = value
  return column_value

@decorator
def json_column(
    cls, attr, json_field_name=None, *, json_column_name='info', default=None
):
  ''' Class decorator to declare a virtual column name on a table
      where the value resides inside a JSON column of the table.

      Parameters:
      * `cls`: the class to annotate
      * `attr`: the virtual column name to present as a row attribute
      * `json_field_name`: the field within the JSON column
        used to store this value,
        default the same as `attr`
      * `json_column_name`: the name of the associated JSON column,
        default `'info'`
      * `default`: the default value returned by the getter
        if the field is not present,
        default `None`

      Example use:

          Base = declarative_base()
          ...
          @json_column('virtual_name', 'json.field.name')
          class TableClass(Base):
            ...

      This annotates the class with a `.virtual_name` property
      which can be accessed or set,
      accessing or modifying the associated JSON column.
  '''
  if json_field_name is None:
    json_field_name = attr

  def get_col(row):
    column_value = getattr(row, json_column_name)
    return get_json_field(column_value, json_field_name, default=default)

  getter = property(get_col)

  def set_col(row, value):
    column_value = getattr(row, json_column_name)
    column_value = set_json_field(
        column_value, json_field_name, value, infill=True
    )
    setattr(row, json_column_name, column_value)
    flag_modified(row, json_column_name)

  setattr(cls, attr, getter)
  setattr(cls, attr, getter.setter(set_col))
  return cls
