#!/usr/bin/env python

''' Assorted utility functions to support working with SQLAlchemy.
'''

from contextlib import contextmanager
from inspect import isgeneratorfunction
import logging
from sqlalchemy import Column, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.attributes import flag_modified
from icontract import require
from cs.deco import decorator, contextdecorator
from cs.py.func import funccite, funcname
from cs.resources import MultiOpenMixin
from cs.threads import State

__version__ = '20210306-post'

DISTINFO = {
    'description':
    'Assorted utility functions to support working with SQLAlchemy.',
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Database",
    ],
    'install_requires': [
        'icontract',
        'sqlalchemy',
        'cs.deco',
        'cs.py.func',
        'cs.resources',
        'cs.threads',
    ],
}

class SQLAState(State):
  ''' Thread local state for SQLAlchemy ORM and session.
  '''

  def __init__(self, *, orm, session=None, **kw):
    # enforce provision of an ORM, default session=None
    super().__init__(orm=orm, session=session, **kw)

  @contextmanager
  def new_session(self, *, orm=None):
    ''' Context manager to create a new session from `orm` or `self.orm`.
    '''
    if orm is None:
      orm = self.orm
      if orm is None:
        raise ValueError(
            "%s.new_session: no orm supplied and no self.orm" %
            (type(self).__name__,)
        )
    with orm.session() as session:
      with self(orm=orm, session=session):
        yield session

  @contextmanager
  def auto_session(self, *, orm=None):
    ''' Context manager to use the current session
        if not `None`, otherwise to make one using `orm` or `self.orm`.
    '''
    session = self.session
    if session is None:
      with self.new_session(orm=orm) as session:
        yield session
    else:
      yield session

# global state, not tied to any specific ORM or session
state = SQLAState(orm=None, session=None)

def with_orm(function, *a, orm=None, **kw):
  ''' Call `function` with the supplied `orm` in the shared state.
  '''
  if orm is None:
    orm = state.orm
    if orm is None:
      raise RuntimeError("no ORM supplied and no state.orm")
  with state(orm=orm):
    return function(*a, orm=orm, **kw)

@contextmanager
def using_session(orm=None, session=None):
  ''' A context manager to prepare an SQLAlchemy session
      for use by a suite.

      Parameters:
      * `orm`: optional reference ORM,
        an object with a `.session()` method for creating a new session.
        Default: if needed, obtained from the global `state.orm`.
      * `session`: optional existing session.
        Default: the global `state.session` if not `None`,
        otherwise created by `orm.session()`.

      If a new session is created, the new session and reference ORM
      are pushed onto the globals `state.session` and `state.orm`
      respectively.

      If an existing session is reused,
      the suite runs within a savepoint from `session.begin_nested()`.
  '''
  # use the shared state session if no session is supplied
  if session is None:
    session = state.session
  # we have a session, push to the global context
  if session is not None:
    with state(session=session):
      yield session
  else:
    # no session, we need to create one
    if orm is None:
      # use the shared state ORM if no orm is supplied
      orm = state.orm
      if orm is None:
        raise ValueError(
            "no orm supplied from which to make a session,"
            " and no shared state orm"
        )
    # create a new session and run the function within it
    with orm.session() as new_session:
      with state(orm=orm, session=new_session):
        yield new_session

def with_session(function, *a, orm=None, session=None, **kw):
  ''' Call `function(*a,session=session,**kw)`, creating a session if required.
      The function `function` runs within a transaction,
      nested if the session already exists.
      If a new session is created
      it is set as the default session in the shared state.

      This is the inner mechanism of `@auto_session` and
      `ORM.auto_session`.

      Parameters:
      * `function`: the function to call
      * `a`: the positional parameters
      * `orm`: optional ORM class with a `.session()` context manager method
        such as the `ORM` base class supplied by this module.
      * `session`: optional existing ORM session
      * `kw`: other keyword arguments, passed to `function`

      One of `orm` or `session` must be not `None`; if `session`
      is `None` then one is made from `orm.session()` and used as
      a context manager.

      The `session` is also passed to `function` as
      the keyword parameter `session` to support nested calls.
  '''
  with using_session(orm=orm, session=session):
    return function(*a, **kw)

def auto_session(function):
  ''' Decorator to run a function in a session if one is not presupplied.
      The function `function` runs within a transaction,
      nested if the session already exists.

      See `with_session` for details.
  '''

  if isgeneratorfunction(function):

    def wrapper(*a, orm=None, session=None, **kw):
      ''' Yield from the function with a session.
      '''
      with using_session(orm=orm, session=session) as active_session:
        yield from function(*a, session=active_session, **kw)
  else:

    def wrapper(*a, orm=None, session=None, **kw):
      ''' Call the function with a session.
      '''
      with using_session(orm=orm, session=session) as active_session:
        return function(*a, session=active_session, **kw)

  wrapper.__name__ = "@auto_session(%s)" % (funccite(function,),)
  wrapper.__doc__ = function.__doc__
  wrapper.__module__ = getattr(function, '__module__', None)
  return wrapper

@contextdecorator
def log_level(func, a, kw, level=None):  # pylint: disable=unused-argument
  ''' Temporarily set the level of the default SQLAlchemy logger to `level`.
      Yields the logger.

      *NOTE*: this is not MT safe - competing Threads can mix log levels up.
  '''
  if level is None:
    level = logging.DEBUG
  logger = logging.getLogger('sqlalchemy.engine')
  old_level = logger.level
  logger.setLevel(level)
  try:
    yield logger
  finally:
    logger.setLevel(old_level)

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
    self.sqla_state = SQLAState(orm=self)

  @contextmanager
  def session(self, *a, **kw):
    ''' Context manager to issue a new session and close it down.

        Note that this performs a `COMMIT` or `ROLLBACK` at the end.
    '''
    with self:
      new_session = self.Session(*a, **kw)
      with using_session(orm=self, session=new_session):
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

        See `with_session` for details.
    '''

    if isgeneratorfunction(method):

      def wrapper(self, *a, session=None, **kw):
        ''' Prepare a session if one is not supplied.
        '''
        with using_session(session=session, orm=self):
          yield from method(self, *a, session=session, **kw)
    else:

      def wrapper(self, *a, session=None, **kw):
        ''' Prepare a session if one is not supplied.
        '''
        with using_session(session=session, orm=self):
          return method(self, *a, session=session, **kw)

    wrapper.__name__ = "@ORM.auto_session(%s)" % (funcname(method),)
    wrapper.__doc__ = method.__doc__
    wrapper.__module__ = getattr(method, '__module__', None)
    return wrapper

  @staticmethod
  def orm_method(method):
    ''' Decorator for ORM subclass methods
        to set the shared state `orm` to `self`.
    '''

    if isgeneratorfunction(method):

      def wrapper(self, *a, **kw):
        ''' Call `method` with its ORM as the shared state `orm`.
        '''
        with state(orm=self):
          yield from method(self, *a, **kw)
    else:

      def wrapper(self, *a, **kw):
        ''' Call `method` with its ORM as the shared state `orm`.
        '''
        with state(orm=self):
          return method(self, *a, **kw)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper

orm_method = ORM.orm_method

def orm_auto_session(method):
  ''' Decorator to run a method in a session derived from `self.orm`
      if a session is not presupplied.
      Intended to assist classes with a `.orm` attribute.

      See `with_session` for details.
  '''

  if isgeneratorfunction(method):

    def orm_auto_session_wrapper(self, *a, session=None, **kw):
      ''' Yield from the method with a session.
      '''
      with using_session(orm=self.orm, session=session) as active_session:
        yield from method(self, *a, session=active_session, **kw)
  else:

    def orm_auto_session_wrapper(self, *a, session=None, **kw):
      ''' Call the method with a session.
      '''
      with using_session(orm=self.orm, session=session) as active_session:
        return method(self, *a, session=active_session, **kw)

  orm_auto_session_wrapper.__name__ = "@orm_auto_session(%s)" % (
      funcname(method),
  )
  orm_auto_session_wrapper.__doc__ = method.__doc__
  orm_auto_session_wrapper.__module__ = getattr(method, '__module__', None)
  return orm_auto_session_wrapper

class BasicTableMixin:
  ''' Useful methods for most tables.
  '''

  @classmethod
  def lookup(cls, *, session, **criteria):
    ''' Return iterable of row entities matching `criteria`.
    '''
    return session.query(cls).filter_by(**criteria)

  @classmethod
  def lookup1(cls, *, session, **criteria):
    ''' Return the row entity matching `criteria`, or `None` if no match.
    '''
    return session.query(cls).filter_by(**criteria).one_or_none()

  @classmethod
  def __getitem__(cls, index):
    row = cls.lookup1(id=index, session=state.session)
    if row is None:
      raise IndexError("%s: no row with id=%s" % (
          cls,
          index,
      ))
    return row

# pylint: disable=too-few-public-methods
class HasIdMixin:
  ''' Include an "id" `Column` as the primary key.
  '''
  id = Column(Integer, primary_key=True)

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
      accessing or modifying the associated JSON column
      (in this instance, the column `info`,
      accessing `info['json']['field']['name']`).
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
