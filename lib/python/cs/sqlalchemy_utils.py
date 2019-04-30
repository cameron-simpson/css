#!/usr/bin/env python

''' Assorted utility functions to support working with SQLAlchemy.
'''

from contextlib import contextmanager
from icontract import require
from sqlalchemy.ext.declarative import declarative_base
from cs.py.func import funcname

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
        'cs.py.func',
    ],
}

@require(lambda orm, session: orm is not None or session is not None)
def with_session(func, *a, orm=None, session=None, **kw):
  ''' Call `func(*a,session=session,**kw)`, creating a session if required.

      This is the inner mechanism of `@auto_session` and
      `ORM.auto_session_method`.

      Parameters:
      * `func`: the function to call
      * `a`: the positional parameters
      * `orm`: optional ORM class with a `.session()` context manager method
      * `session`: optional existing ORM session

      One of `orm` or `session` must be not `None`; if `session`
      is `None` then one is made from `orm.session()` and used as
      a context manager. The `session` is also passed to `func` as
      the keyword parameter `session` to support nested calls.
  '''
  if session:
    # run the function nside a savepoint in the supplied session
    with session.begin_nested():
      return func(*a, session=session, **kw)
  if not orm:
    raise ValueError("no orm supplied from which to make a session")
  with orm.session() as new_session:
    return func(*a, session=new_session, **kw)

def auto_session(func):
  ''' Decorator to run a function in a session is not presupplied.
  '''

  @require(lambda orm, session: orm is not None or session is not None)
  def wrapper(*a, orm=None, session=None, **kw):
    ''' Prepare a session if one is not supplied.
    '''
    return with_session(func, *a, orm=orm, session=session, **kw)

  wrapper.__name__ = "@auto_session(%s)" % (funcname(func,),)
  wrapper.__doc__ = func.__doc__
  return wrapper

class ORM:
  ''' A convenience base class for an ORM class.

      This defines a `.Base` attribute which is a new `DeclarativeBase`
      and provides various Session related convenience methods.

      Subclasses must define their own `.Session` factory in
      their own `__init__`, for example:

          self.Session = sessionmaker(bind=engine)
  '''

  def __init__(self):
    self.Base = declarative_base()
    self.Session = None

  @contextmanager
  def session(self):
    ''' Context manager to issue a new session and shut it down.

        Note that this performs a `COMMIT` or `ROLLBACK` at the end.
    '''
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

    wrapper.__name__ = "@ORM.auto_session(%s)" % (funcname(method,),)
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
