#!/usr/bin/env python

''' Assorted utility functions to support working with SQLAlchemy.
'''

from abc import ABC, abstractmethod
from contextlib import contextmanager
from inspect import isgeneratorfunction
import logging
import os
from os.path import abspath
from threading import current_thread, Lock
from typing import Any, Optional, Union, List, Tuple

from icontract import require
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker as sqla_sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool
from typeguard import typechecked

from cs.deco import decorator, contextdecorator
from cs.fileutils import makelockfile
from cs.lex import cutprefix
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.func import funccite, funcname
from cs.resources import MultiOpenMixin
from cs.threads import State

__version__ = '20220311-post'

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
        'cs.fileutils',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.resources',
        'cs.threads',
    ],
}

class SQLAState(State):
  ''' Thread local state for SQLAlchemy ORM and session.
  '''

  def __init__(
      self, *, orm, engine=None, session=None, sessionmaker=None, **kw
  ):
    # enforce provision of an ORM, default session=None
    super().__init__(**kw)
    self.orm = orm
    self.engine = engine
    self.session = session
    self.sessionmaker = sessionmaker

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
    with orm.arranged_session() as session:
      with session.begin_nested():
        with self(orm=orm, session=session):
          yield session

  @contextmanager
  def auto_session(self, *, orm=None):
    ''' Context manager to use the current session
        if not `None`, otherwise to make one using `orm` or `self.orm`.
    '''
    session = self.session
    if session is None or (orm is not None and orm is not self.orm):
      # new session required
      with self.new_session(orm=orm) as session:
        yield session
    else:
      with session.begin_nested():
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

    def auto_session_generator_wrapper(*a, orm=None, session=None, **kw):
      ''' Yield from the function with a session.
      '''
      with using_session(orm=orm, session=session) as active_session:
        yield from function(*a, session=active_session, **kw)

    wrapper = auto_session_generator_wrapper

  else:

    def auto_session_function_wrapper(*a, orm=None, session=None, **kw):
      ''' Call the function with a session.
      '''
      with using_session(orm=orm, session=session) as active_session:
        return function(*a, session=active_session, **kw)

    wrapper = auto_session_function_wrapper

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

# pylint: disable=too-many-instance-attributes
class ORM(MultiOpenMixin, ABC):
  ''' A convenience base class for an ORM class.

      This defines a `.Base` attribute which is a new `DeclarativeBase`
      and provides various Session related convenience methods.
      It is also a `MultiOpenMixin` subclass
      supporting nested open/close sequences and use as a context manager.
  '''

  @pfx_method
  def __init__(self, db_url, serial_sessions=None):
    ''' Initialise the ORM.

        If `serial_sessions` is true (default `False`)
        then allocate a lock to serialise session allocation.
        This might be chosen with SQL backends which do not support
        concurrent sessions such as SQLite.

        In the case of SQLite there's a small inbuilt timeout in
        an attempt to serialise transactions but it is possible to
        exceed it easily and recovery is usually infeasible.
        Instead we use the `serial_sessions` option to obtain a
        mutex before allocating a session.
    '''
    db_fspath = cutprefix(db_url, 'sqlite:///')
    if db_fspath is db_url:
      # unchanged - no leading "sqlite:///"
      if db_url.startswith(('/', './', '../')) or '://' not in db_url:
        # turn filesystenm pathnames into SQLite db URLs
        db_fspath = abspath(db_url)
        db_url = 'sqlite:///' + db_url
      else:
        # no fs path
        db_fspath = None
    else:
      # starts with sqlite:///, we have the db_fspath
      pass
    self.db_url = db_url
    self.db_fspath = db_fspath
    is_sqlite = db_url.startswith('sqlite://')
    if serial_sessions is None:
      # serial SQLite sessions
      serial_sessions = is_sqlite
    elif not serial_sessions:
      if is_sqlite:
        warning(
            "serial_sessions specified as %r, but is_sqlite=%s:"
            " this may cause trouble with multithreaded use",
            serial_sessions,
            is_sqlite,
        )
    self.serial_sessions = serial_sessions or is_sqlite
    self._lockfilepath = None
    self.Base = declarative_base()
    self.sqla_state = SQLAState(
        orm=self, engine=None, sessionmaker=None, session=None
    )
    if serial_sessions:
      self._serial_sessions_lock = Lock()
    else:
      self._engine = None
      self._sessionmaker_raw = None
    self.db_url = db_url
    self.engine_keywords = {}
    self.engine_keywords = dict(
        case_sensitive=True,
        echo=(
            bool(os.environ.get('DEBUG'))
            or 'echo' in os.environ.get('SQLTAGS_MODES', '').split(',')
        ),  # 'debug'
    )
    if is_sqlite:
      # do not pool these connects and disable the Thread check
      # because we
      self.engine_keywords.update(
          poolclass=NullPool, connect_args={'check_same_thread': False}
      )
    self.declare_schema()

  @abstractmethod
  def declare_schema(self):
    ''' Declare the database schema / ORM mapping.
        This just defines the relation types etc.
        It *does not* act on the database itself.
        It is called automatically at the end of `__init__`.

        Example:

            def declare_schema(self):
              """ Define the database schema / ORM mapping.
              """
              orm = self
              Base = self.Base
              class Entities(
              ........
              self.entities = Entities

        After this, methods can access the example `Entities` relation
        as `self.entites`.
    '''
    raise NotImplementedError("declare_schema")

  @contextmanager
  def startup_shutdown(self):
    ''' Default startup/shutdown context manager.

        This base method operates a lockfile to manage concurrent access
        by other programmes (which would also need to honour this file).
        If you actually expect this to be common
        you should try to keep the `ORM` "open" as briefly as possible.
        The lock file is only operated if `self.db_fspath`,
        current set only for filesystem SQLite database URLs.
    '''
    if self.db_fspath:
      self._lockfilepath = makelockfile(self.db_fspath, poll_interval=0.2)
    yield
    if self._lockfilepath is not None:
      pfx_call(os.remove, self._lockfilepath)
      self._lockfilepath = None

  @property
  def engine(self):
    ''' SQLAlchemy engine, made on demand.
    '''
    orm_state = self.sqla_state
    if self.serial_sessions:
      engine = orm_state.engine
    else:
      engine = self._engine
    if engine is None:
      engine = create_engine(self.db_url, **self.engine_keywords)
      self._engine = engine
      orm_state.engine = engine  # pylint: disable=attribute-defined-outside-init
    return engine

  @property
  def _sessionmaker(self):
    ''' SQLAlchemy sessionmaker for the current `Thread`.
    '''
    orm_state = self.sqla_state
    if self.serial_sessions:
      sessionmaker = orm_state.sessionmaker
    else:
      sessionmaker = self._sessionmaker_raw
    if sessionmaker is None:
      sessionmaker = sqla_sessionmaker(bind=self.engine)
      self._sessionmaker_raw = sessionmaker
      orm_state.sessionmaker = sessionmaker  # pylint: disable=attribute-defined-outside-init
    return sessionmaker

  @contextmanager
  @pfx_method(use_str=True)
  def arranged_session(self):
    ''' Arrange a new session for this `Thread`.
    '''
    orm_state = self.sqla_state
    with self:
      if self.serial_sessions:
        if orm_state.session is not None:
          T = current_thread()
          tid = "Thread:%d:%s" % (T.ident, T.name)
          raise RuntimeError(
              "%s: this Thread already has an ORM session: %s" % (
                  tid,
                  orm_state.session,
              )
          )
        with self._serial_sessions_lock:
          new_session = self._sessionmaker()
          with new_session.begin_nested():
            yield new_session
      else:
        new_session = self._sessionmaker()
        with new_session.begin_nested():
          yield new_session

  @property
  def default_session(self):
    ''' The current per-`Thread` session.
    '''
    return self.sqla_state.session

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

  DEFAULT_ID_COLUMN = 'id'

  @classmethod
  def lookup(cls, *, session, **criteria):
    ''' Return an iterable `Query` of row entities matching `criteria`.
    '''
    return session.query(cls).filter_by(**criteria)

  @classmethod
  def lookup1(cls, *, session, **criteria):
    ''' Return the row entity matching `criteria`, or `None` if no match.
    '''
    return session.query(cls).filter_by(**criteria).one_or_none()

  @classmethod
  def by_id(cls, index, *, id_column=None, session):
    ''' Index the table by its `id_column` column, default `'id'`.
    '''
    if id_column is None:
      id_column = cls.DEFAULT_ID_COLUMN
    row = cls.lookup1(session=session, **{id_column: index})
    if row is None:
      raise IndexError("%s: no row with id=%s" % (
          cls,
          index,
      ))
    return row

  __getitem__ = by_id

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

def RelationProxy(
    relation,
    columns: Union[str, Tuple[str], List[str]],
    *,
    id_column: Optional[str] = None
):
  ''' Construct a proxy for a row from a relation.

      Parameters:
      * `relation`: an ORM relation for which this will be a proxy
      * `columns`: a list of the column names to cache,
        or a space separated string of the column names
      * `id_column`: options primary key column name,
        default from `BasicTableMixin.DEFAULT_ID_COLUMN`: `'id'`

      This is something of a workaround for applications which dip
      briefly into the database to obtain information instead of
      doing single long running transactions or sessions.
      Instead of keeping the row instance around, which might want
      to load related data on demand after its source session is
      expired, we keep a proxy for the row with cached values
      and refetch the row at need if further information is requried.

      Typical use is to construct this proxy class as part
      of the `__init__` of a larger class which accesses the database
      as part of its working, example based on `cs.ebooks.calibre.CalibreTree`:

          def __init__(self, calibrepath):
            super().__init__(calibrepath)
            # define the proxy classes
            class CalibreBook(RelationProxy(self.db.books, [
                'author',
                'title',
            ])):
              """ A reference to a book in a Calibre library.
              """
              @typechecked
              def __init__(self, tree: CalibreTree, dbid: int, db_book=None):
                self.tree = tree
                self.dbid = dbid
              ... various other CalibreBook methods ...
            self.CalibreBook = CalibreBook

          def __getitem__(self, dbid):
            return self.CalibreBook(self, dbid, db_book=db_book)


  '''
  if isinstance(columns, str):
    columns = [column for column in columns.split() if column]
  if id_column is None:
    id_column = BasicTableMixin.DEFAULT_ID_COLUMN

  class RelProxy:
    ''' The relation proxy base class,
        defined in the factory because it uses the relation via a closure.
    '''

    @typechecked
    def __init__(self, id: Any, *, id_column='id', db_row=None):  # pylint: disable=redefined-builtin
      self.id = id
      self.id_column = id_column
      self.__fields = {}
      if db_row is not None:
        self.refresh_from_db(db_row)

    def refresh_from_db(self, db_row=None):
      ''' Update the cached values from the database.
      '''
      with using_session(orm=relation.orm) as session:
        if db_row is None:
          db_row = relation.lookup1(**{id_column: self.id, 'session': session})
        self.refresh_from_db_row(db_row, self.__fields, session=session)

    # pylint: disable=unused-argument
    @classmethod
    def refresh_from_db_row(cls, db_row, fields, *, session):
      ''' Class method to update a field cache from a database row `db_row`.

          This method should be overridden by subclasses to add
          addition cached values.
      '''
      for column in columns:
        fields[column] = getattr(db_row, column)

    def __getitem__(self, field, force=False):
      ''' Fetch a field from the cache.
          If not present, refresh the cache and retry.
      '''
      if not force:
        try:
          return self.__fields[field]
        except KeyError:
          pass
      with using_session(orm=relation.orm) as session:
        db_row = relation.by_id(self.id, session=session)
        self.refresh_from_db(db_row)
      return self.__fields[field]

    def __getattr__(self, attr):
      ''' Attribute access fetches from the cache via `__getitem__`
          or falls back to attribute access on the database row.
      '''
      if attr.startswith('_'):
        warning("__getattr__(%r)", attr)
      with Pfx("%s.%s", type(self).__name__, attr):
        if attr in ('id', '_RelProxy__fields'):
          raise RuntimeError
        try:
          return self[attr]
        except KeyError as e:
          with using_session(orm=relation.orm) as session:
            db_row = relation.by_id(self.id, session=session)
            self.__fields[attr] = getattr(db_row, attr)
          raise AttributeError(
              "%s: no cached field .%s" % (type(self).__name__, attr)
          ) from e

    @contextmanager
    def db_row_and_session(self):
      ''' Context manager yielding `(db_row,session)` for deriving data from the row.

          This is expected to be used from on demand proxy properties.
      '''
      with using_session(orm=relation.orm) as session:
        db_row = relation.lookup1(**{id_column: self.id, 'session': session})
        yield db_row, session

  RelProxy.__name__ = relation.__name__ + '_' + RelProxy.__name__
  return RelProxy

@decorator
def proxy_on_demand_field(field_func, field_name=None):
  ''' A decorator to provide a field value on demand
      via a function `field_func(self,db_row,session=session)`.

      Example:

          @property
          @proxy_on_demand_field
          def formats(self,db_row,*,session):
              """ A mapping of Calibre format keys to format paths
                  computed on demand.
              """
              return {
                  fmt.format:
                  joinpath(db_row.path, f'{fmt.name}.{fmt.format.lower()}')
                  for fmt in db_row.formats
              }
  '''
  if field_name is None:
    field_name = field_func.__name__

  def field_func_wrapper(self):
    fields = self._RelProxy__fields
    try:
      field_value = fields[field_name]
    except KeyError:
      with self.db_row_and_session() as (db_row, session):
        field_value = fields[field_name] = field_func(
            self, db_row, session=session
        )
    return field_value

  return field_func_wrapper
