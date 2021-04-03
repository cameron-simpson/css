#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Simple SQL based tagging
    and the associated `sqltags` command line script,
    supporting both tagged named objects and tagged timestamped log entries.

    Compared to `cs.fstags` and its associated `fstags` command,
    this is oriented towards large numbers of items
    not naturally associated with filesystem objects.
    My initial use case is an activity log,
    but I'm probably going to use it for ontologies as well.

    Many basic tasks can be performed with the `sqltags` command line utility,
    documented under the `SQLTagsCommand` class below.
'''

from abc import abstractmethod
from builtins import id as builtin_id
from collections import defaultdict, namedtuple
from contextlib import contextmanager
import csv
from datetime import datetime
from fnmatch import fnmatchcase
from getopt import getopt, GetoptError
import operator
import os
from os.path import expanduser, exists as existspath
import re
import sys
from subprocess import run
from threading import RLock
import time
from typing import List
from icontract import require
from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    JSON,
    ForeignKey,
)
from sqlalchemy.orm import aliased
from sqlalchemy.sql import select
from sqlalchemy.sql.expression import and_, or_, case
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.dateutils import UNIXTimeMixin, datetime2unixtime
from cs.deco import fmtdoc
from cs.lex import FormatAsError, get_decimal_value
from cs.logutils import error, warning, track, info, ifverbose
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
)
from cs.tagset import (
    TagSet, Tag, TagSetCriterion, TagBasedTest, TagsCommandMixin, TagsOntology,
    TagSets, tag_or_tag_value, as_unixtime
)
from cs.threads import locked, State as ThreadState
from cs.upd import print  # pylint: disable=redefined-builtin

__version__ = '20210404'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': ['sqltags = cs.sqltags:main'],
    },
    'install_requires': [
        'cs.cmdutils>=20210404',
        'cs.context',
        'cs.dateutils',
        'cs.deco',
        'cs.lex',
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.sqlalchemy_utils>=20210321',
        'cs.tagset',
        'cs.threads>=20201025',
        'cs.upd',
        'icontract',
        'sqlalchemy',
        'typeguard',
    ],
}

# regexp for "word[,word...]:", the leading prefix for categories
# if not specified by the -c command line option
CATEGORIES_PREFIX_re = re.compile(
    r'(?P<categories>[a-z]\w*(,[a-z]\w*)*):\s*', re.I
)

DBURL_ENVVAR = 'SQLTAGS_DBURL'
DBURL_DEFAULT = '~/var/sqltags.sqlite'

FIND_OUTPUT_FORMAT_DEFAULT = '{entity.isodatetime} {headline}'

def main(argv=None):
  ''' Command line mode.
  '''
  return SQLTagsCommand(argv).run()

state = ThreadState(verbose=sys.stderr.isatty())

def verbose(msg, *a):
  ''' Emit message if in verbose mode.
  '''
  ifverbose(state.verbose, msg, *a)

def glob2like(glob: str) -> str:
  ''' Convert a filename glob to an SQL LIKE pattern.
  '''
  assert '[' not in glob
  return glob.replace('*', '%').replace('?', '_')

def prefix2like(prefix: str, esc='\\') -> str:
  ''' Convert a prefix string to an SQL LIKE pattern.
  '''
  return prefix.replace('%', esc + '%') + '%'

class SQLParameters(namedtuple('SQLParameters',
                               'criterion alias entity_id_column constraint')):
  ''' The parameters required for constructing queries
      or extending queries with JOINs.

      Attributes:
      * `criterion`: the source criterion, usually an `SQTCriterion` subinstance
      * `alias`: an alias of the source table for use in queries
      * `entity_id_column`: the `entities` id column,
        `alias.id` if the alias is of `entities`,
        `alias.entity_id` if the alias is of `tags`
      * `constraint`: a filter query based on `alias`
  '''

class SQLTagProxy:
  ''' An object based on a `Tag` name
      which produces an `SQLParameters` when compared with some value.

      Example:

          >>> sqltags = SQLTags('sqlite://')
          >>> sqltags.init()
          >>> # make a SQLParameters for testing the tag 'name.thing'==5
          >>> sqlp = sqltags.tags.name.thing == 5
          >>> str(sqlp.constraint)
          'tags_1.name = :name_1 AND tags_1.float_value = :float_value_1'
          >>> sqlp = sqltags.tags.name.thing == 'foo'
          >>> str(sqlp.constraint)
          'tags_1.name = :name_1 AND tags_1.string_value = :string_value_1'
  '''

  def __init__(self, orm, tag_name):
    self._orm = orm
    self._tag_name = tag_name

  def __str__(self):
    return "%s(tag_name=%r)" % (type(self).__name__, self._tag_name)

  def __getattr__(self, sub_tag_name):
    ''' Magic access to dotted tag names: produce a new `SQLTagProxy` from ourself.
    '''
    return SQLTagProxy(self._orm, self._tag_name + '.' + sub_tag_name)

  def by_op_text(self, op_text, other, alias=None):
    ''' Return an `SQLParameters` based on the comparison's text representation.

        Parameters:
        * `op_text`: the comparsion operation text, one of:
          `'='`, `'<='`, `'<'`, `'>='`, `'>'`, `'~'`.
        * `other`: the other value for the comparison,
          used to infer the SQL column name
          and kept to provide the SQL value parameter
        * `alias`: optional SQLAlchemy table alias
    '''
    try:
      cmp_func = {
          '=': self.__eq__,
          '<=': self.__le__,
          '<': self.__lt__,
          '>=': self.__ge__,
          '>': self.__gt__,
          '~': self.likeglob,
      }[op_text]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise ValueError("unknown comparison operator text %r" % (op_text,))
    return cmp_func(other, alias=alias)

  # pylint: disable=too-many-arguments
  def _cmp(
      self,
      op_label,
      other,
      op,
      op_takes_alias=False,
      alias=None
  ) -> SQLParameters:
    ''' Parameterised translator from an operator to an `SQLParameters`.

        Parameters:
        * `op_label`: a text label for the operator for use in messages,
          such as `'=='` for `operator.eq`.
        * `other`: the other value to which to compare.
        * `op`: the operator callable returning an SQLAlchemy filter condition
          from an SQLAlchemy column and the derived avlue from `other`;
          usually this will be supplied as something like `operator.eq`.
        * `op_takes_alias`: a Boolean (default `False`).
          If false, `op` is handed a column from the table alias as above.
          If true, `op` is handed the table alias itself.
        * `alias`: optional SQLAlchemy table alias

        The `op_takes_alias` parameter exists to support multicolumn
        conditions such as `==None`, which needs to test that all the value
        columns are `NULL`.

        The default case (`op_takes_alias` is false)
        obtains the condition from `op(column,value)`
        where `column` and `value` are inferred from the type of `other`:
        - if `other` is an `int` or `float`
          then `column=alias.float_value` and `value=float(other)`
        - if `other` is a `str`
          then `column=alias.string_value` and `value=other`

        When `op_takes_alias` is true
        the condition is obtained from `op(alias,other)`
        where `alias` is the new table alias for `tags`
        and `other` is the value supplied.
        It is up to `op` to construct the required condition.
    '''
    pf = f'{self}{op_label}{other!r}'
    with Pfx(pf):
      tags = alias or aliased(self._orm.tags)
      if op_takes_alias:
        other_condition = op(tags, other)
      else:
        if isinstance(other, (int, float)):
          tag_value = float(other)
          column = tags.float_value
        elif isinstance(other, str):
          tag_value = other
          column = tags.string_value
        else:
          raise TypeError("not supported for type %s" % (type(other),))
        other_condition = op(column, tag_value)
      return SQLParameters(
          criterion=pf,
          alias=tags,
          entity_id_column=tags.entity_id,
          constraint=and_(tags.name == self._tag_name, other_condition),
      )

  def __eq__(self, other, alias=None) -> SQLParameters:
    ''' Return an SQL `=` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing == 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value = :string_value_1'
    '''
    if other is None:
      # special test for ==None
      return self._cmp(
          "==",
          other,
          lambda alias, value: and_(
              alias.float_value is None,
              alias.stringvalue is None,
              alias.structured_value is None,
          ),
          op_takes_alias=True,
          alias=alias,
      )
    return self._cmp("==", other, operator.eq)

  def __ne__(self, other, alias=None) -> SQLParameters:
    ''' Return an SQL `<>` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing != 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value != :string_value_1'
    '''
    if other is None:
      # special test for ==None
      return self._cmp(
          "==",
          other,
          lambda alias, value: or_(
              alias.float_value is not None,
              alias.stringvalue is not None,
              alias.structured_value is not None,
          ),
          op_takes_alias=True,
          alias=alias,
      )
    return self._cmp("!=", other, operator.ne)

  def __lt__(self, other):
    ''' Return an SQL `<` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing < 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value < :string_value_1'
    '''
    return self._cmp("<", other, operator.lt)

  def __le__(self, other):
    ''' Return an SQL `<=` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing <= 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value <= :string_value_1'
    '''
    return self._cmp("<=", other, operator.le)

  def __gt__(self, other):
    ''' Return an SQL `>` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing > 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value > :string_value_1'
    '''
    return self._cmp(">", other, operator.gt)

  def __ge__(self, other):
    ''' Return an SQL `>=` test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing >= 'foo'
            >>> str(sqlp.constraint)
            'tags_1.name = :name_1 AND tags_1.string_value >= :string_value_1'
    '''
    return self._cmp(">=", other, operator.ge)

  def startswith(self, prefix: str) -> SQLParameters:
    ''' Return an SQL LIKE prefix test `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing.startswith('foo')
            >>> str(sqlp.constraint)
            "tags_1.name = :name_1 AND tags_1.string_value LIKE :string_value_1 ESCAPE '\\\\'"
    '''
    esc = '\\'
    return self._cmp(
        "startswith", prefix,
        lambda column, prefix: column.like(prefix2like(prefix, esc), esc)
    )

  def likeglob(self, globptn: str) -> SQLParameters:
    ''' Return an SQL LIKE test approximating a glob as an `SQLParameters`.

        Example:

            >>> sqlp = SQLTags('sqlite://').tags.name.thing.likeglob('foo*')
            >>> str(sqlp.constraint)
            "tags_1.name = :name_1 AND tags_1.string_value LIKE :string_value_1 ESCAPE '\\\\'"
    '''
    esc = '\\'
    return self._cmp(
        "likeglob", globptn, lambda column, globptn: column.
        like(globptn.replace('%', esc + '%').replace('*', '%'), esc)
    )

# pylint: disable=too-few-public-methods
class SQLTagProxies:
  ''' A proxy for the tags supporting Python comparison => `SQLParameters`.

      Example:

          sqltags.tags.dotted.name.here == 'foo'
  '''

  def __init__(self, orm):
    self.orm = orm

  def __getattr__(self, tag_name):
    return SQLTagProxy(self.orm, tag_name)

class SQTCriterion(TagSetCriterion):
  ''' Subclass of `TagSetCriterion` requiring an `.sql_parameters` method
      which returns an `SQLParameters` providing the information required
      to construct an sqlalchemy query.
      It also resets `.CRITERION_PARSE_CLASSES`, which will pick up
      the SQL capable criterion classes below.
  '''

  # require the match_tagged_entity method to confirm selection if False,
  # no need if true
  SQL_COMPLETE = False

  # list of TagSetCriterion classes
  # whose .parse methods are used by .parse
  CRITERION_PARSE_CLASSES = []

  @abstractmethod
  def sql_parameters(self, orm) -> SQLParameters:
    ''' Subclasses must return am `SQLParameters` instance
        parameterising the SQL queries that follow.
    '''
    raise NotImplementedError("sql_parameters")

  @abstractmethod
  def match_tagged_entity(self, te: TagSet) -> bool:
    ''' Perform the criterion test on the Python object directly.
        This is used at the end of a query to implement tests which
        cannot be sufficiently implemented in SQL.
        If `self.SQL_COMPLETE` it is not necessary to call this method.
    '''
    raise NotImplementedError("sql_parameters")

class SQTEntityIdTest(SQTCriterion):
  ''' A test on `entity.id`.
  '''

  SQL_COMPLETE = True

  @typechecked
  def __init__(self, ids: List[int]):
    self.entity_ids = ids

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.entity_ids)

  @classmethod
  def parse(cls, s, offset=0, delim=None):
    ''' Parse a decimal entity id from `s`.
    '''
    value, offset = get_decimal_value(s, offset=offset)
    return cls([value]), offset

  @pfx_method
  def sql_parameters(self, orm) -> SQLParameters:
    entities = orm.entities
    alias = aliased(entities)
    sqlp = SQLParameters(
        criterion=self,
        alias=alias,
        entity_id_column=alias.id,
        constraint=alias.id.in_(self.entity_ids),
    )
    return sqlp

  def match_tagged_entity(self, te: TagSet) -> bool:
    ''' Test the `TagSet` `te` against `self.entity_ids`.
    '''
    return te.id in self.entity_ids

SQTCriterion.CRITERION_PARSE_CLASSES.append(SQTEntityIdTest)
SQTCriterion.TAG_BASED_TEST_CLASS = SQTEntityIdTest

class SQLTagBasedTest(TagBasedTest, SQTCriterion):
  ''' A `cs.tagset.TagBasedTest` extended with a `.sql_parameters` method.
  '''

  SQL_COMPLETE = True

  # TODO: REMOVE SQL_TAG_VALUE_COMPARISON_FUNCS, unused
  # functions returning SQL tag.value tests based on self.comparison
  # pylint: disable=singleton-comparison
  SQL_TAG_VALUE_COMPARISON_FUNCS = {
      None:
      lambda alias, cmp_value: and_(
          alias.float_value is None, alias.string_value is None, alias.
          structured_value is None
      ),
      '=':
      lambda alias, cmp_value: (
          or_(
              alias.float_value != None, alias.string_value != None, alias.
              structured_value != None
          ) if cmp_value is None else (
              alias.float_value == cmp_value
              if isinstance(cmp_value, (int, float)) else (
                  alias.string_value == cmp_value
                  if isinstance(cmp_value, str) else
                  (alias.structured_value == cmp_value)
              )
          )
      ),
      '<=':
      lambda alias, cmp_value: (
          alias.float_value <= cmp_value
          if isinstance(cmp_value, (int, float)) else (
              alias.string_value <= cmp_value
              if isinstance(cmp_value, str) else
              (alias.structured_value <= cmp_value)
          )
      ),
      '<':
      lambda alias, cmp_value: (
          alias.float_value < cmp_value
          if isinstance(cmp_value, (int, float)) else (
              alias.string_value < cmp_value if isinstance(cmp_value, str) else
              (alias.structured_value < cmp_value)
          )
      ),
      '>=':
      lambda alias, cmp_value: (
          alias.float_value >= cmp_value
          if isinstance(cmp_value, (int, float)) else (
              alias.string_value >= cmp_value
              if isinstance(cmp_value, str) else
              (alias.structured_value >= cmp_value)
          )
      ),
      '>':
      lambda alias, cmp_value: (
          alias.float_value > cmp_value
          if isinstance(cmp_value, (int, float)) else (
              alias.string_value > cmp_value if isinstance(cmp_value, str) else
              (alias.structured_value > cmp_value)
          )
      ),
      '~':
      lambda alias, cmp_value: case(
          [
              (
                  alias.string_value.isnot(None),
                  alias.string_value.like(glob2like(cmp_value))
              ),
          ],
          else_=True,
      )
      ##'~/': # requires sqlalchemy 1.4
      ##lambda alias, cmp_value: alias.string_value.regexp_match(cmp_value),
  }

  SQL_ID_VALUE_COMPARISON_FUNCS = {
      None: lambda entity, id_value: entity.id is not None,
      '=': lambda entity, id_value: entity.id == id_value,
      '<=': lambda entity, id_value: entity.id <= id_value,
      '<': lambda entity, id_value: entity.id < id_value,
      '>=': lambda entity, id_value: entity.id >= id_value,
      '>': lambda entity, id_value: entity.id > id_value,
      '~': lambda entity, id_value: entity.id in id_value,
  }

  SQL_NAME_VALUE_COMPARISON_FUNCS = {
      None: lambda entity, name_value: entity.name is not None,
      '=': lambda entity, name_value: entity.name == name_value,
      '<=': lambda entity, name_value: entity.name <= name_value,
      '<': lambda entity, name_value: entity.name < name_value,
      '>=': lambda entity, name_value: entity.name >= name_value,
      '>': lambda entity, name_value: entity.name > name_value,
      '~': lambda entity, name_value: entity.name.like(glob2like(name_value)),
      ##'~/': lambda entity, name_value: re.search(name_value, entity.name),
  }

  SQL_UNIXTIME_VALUE_COMPARISON_FUNCS = {
      None: lambda entity, unixtime_value: entity.unixtime is not None,
      '=': lambda entity, unixtime_value: entity.unixtime == unixtime_value,
      '<=': lambda entity, unixtime_value: entity.unixtime <= unixtime_value,
      '<': lambda entity, unixtime_value: entity.unixtime < unixtime_value,
      '>=': lambda entity, unixtime_value: entity.unixtime >= unixtime_value,
      '>': lambda entity, unixtime_value: entity.unixtime > unixtime_value,
  }

  TE_VALUE_COMPARISON_FUNCS = {
      '=':
      lambda te_value, cmp_value: te_value == cmp_value,
      '<=':
      lambda te_value, cmp_value: te_value <= cmp_value,
      '<':
      lambda te_value, cmp_value: te_value < cmp_value,
      '>=':
      lambda te_value, cmp_value: te_value >= cmp_value,
      '>':
      lambda te_value, cmp_value: te_value > cmp_value,
      '~':
      lambda te_value, cmp_value: (
          fnmatchcase(te_value, cmp_value) if isinstance(te_value, str) else
          any(map(lambda value: fnmatchcase(value, cmp_value), te_value))
      ),
      '~/':
      lambda te_value, cmp_value:
      (isinstance(te_value, str) and re.search(cmp_value, te_value)),
  }

  # TODO: handle tag named "id" specially as well
  @pfx_method
  def sql_parameters(self, orm, alias=None) -> SQLParameters:
    tag = self.tag
    tag_name = tag.name
    tag_value = tag.value
    if tag_name in ('name', 'unixtime'):
      entities = orm.entities
      if alias is None:
        alias = aliased(entities)
      entity_id_column = alias.id
      if tag_name == 'name':
        if not isinstance(tag_value, str):
          raise ValueError(
              "name comparison requires a str, got %s:%r" %
              (type(tag_value), tag_value)
          )
        constraint_fn = self.SQL_NAME_VALUE_COMPARISON_FUNCS.get(
            self.comparison
        )
        constraint = constraint_fn and constraint_fn(alias, tag_value)
      elif tag_name == 'unixtime':
        timestamp = as_unixtime(tag_value)
        constraint_fn = self.SQL_UNIXTIME_VALUE_COMPARISON_FUNCS.get(
            self.comparison
        )
        constraint = constraint_fn and constraint_fn(alias, timestamp)
      else:
        raise RuntimeError("unhandled non-tag field %r" % (tag_name,))
      sqlp = SQLParameters(
          criterion=self,
          alias=alias,
          entity_id_column=entity_id_column,
          constraint=constraint if self.choice else -alias.has(constraint),
      )
    else:
      # general tag_name
      sqlp = SQLTagProxy(orm, tag.self.tag_name).by_op_text(
          self.comparison, tag_value, alias=alias
      )
    return sqlp

  def match_tagged_entity(self, te: TagSet) -> bool:
    ''' Match this criterion against `te`.
    '''
    tag = self.tag
    tag_name = tag.name
    tag_value = tag.value
    if tag_name == 'name':
      if tag_value is None:
        # does this entity have a name?
        result = te.name is not None
      else:
        result = self.TE_VALUE_COMPARISON_FUNCS[self.comparison
                                                ](te.name, tag_value)
    elif tag_name == 'unixtime':
      result = self.TE_VALUE_COMPARISON_FUNCS[
          self.comparison](te.unixtime, as_unixtime(tag_value))
    else:
      if tag_value is None:
        result = tag_name in te
      else:
        te_tag_value = te.get(tag_name)
        if te_tag_value is None:
          result = False
        else:
          result = self.TE_VALUE_COMPARISON_FUNCS[self.comparison
                                                  ](te_tag_value, tag_value)
    return result if self.choice else not result

SQTCriterion.CRITERION_PARSE_CLASSES.append(SQLTagBasedTest)
SQTCriterion.TAG_BASED_TEST_CLASS = SQLTagBasedTest

# pylint: disable=too-many-instance-attributes
class SQLTagsORM(ORM, UNIXTimeMixin):
  ''' The ORM for an `SQLTags`.
  '''

  def __init__(self, *, db_url):
    super().__init__(db_url)
    self.engine_keywords.update(
        case_sensitive=True,
        echo=(
            self.engine_keywords.get('echo', False)
            or 'echo' in os.environ.get('SQLTAGS_MODES', '').split(',')
        ),
    )
    db_fspath = self.db_fspath
    if db_fspath is not None and not existspath(db_fspath):
      track("create and init %r", db_fspath)
      with Pfx("init %r", db_fspath):
        self.define_schema()
        info('created database')

  def define_schema(self):
    ''' Instantiate the schema and define the root metanode.
    '''
    self.Base.metadata.create_all(bind=self.engine)
    with self.session() as session:
      self.prepare_metanode(session=session)

  def prepare_metanode(self, *, session):
    ''' Ensure row id 0, the metanode, exists.
    '''
    entities = self.entities
    entity = entities.lookup1(session=session, id=0)
    if entity is None:
      # force creation of the desired row id
      entity = entities(id=0, unixtime=time.time())
      entity.add_tag(
          'headline',
          "%s node 0: the metanode." % (type(self).__name__,),
          session=session,
      )
      session.add(entity)
    return entity

  # pylint: disable=too-many-statements
  def declare_schema(self):
    ''' Define the database schema / ORM mapping.
    '''
    orm = self
    Base = self.Base

    class Entities(
        Base,
        BasicTableMixin,
        HasIdMixin,
        UNIXTimeMixin,
    ):
      ''' An entity.
      '''

      __tablename__ = 'entities'
      name = Column(
          String,
          nullable=True,
          index=True,
          unique=True,
          default=None,
          comment='optional entity name'
      )
      unixtime = Column(
          Float,
          nullable=True,
          index=True,
          default=None,
          comment='optional time'
      )

      def __str__(self):
        return (
            "%s:%s(when=%s,name=%r)" % (
                type(self).__name__, self.id, self.datetime.isoformat(),
                self.name
            )
        )

      def add_tag(self, name: str, value=None, *, session):
        ''' Add a tag for `(name,value)`,
            replacing any existing tag named `name`.
        '''
        tags = orm.tags
        if self.id is None:
          # obtain the id value from the database
          session.add(self)
          session.flush()
        # TODO: upsert!
        etag = tags.lookup1(session=session, entity_id=self.id, name=name)
        if etag is None:
          etag = tags(entity_id=self.id, name=name)
          etag.value = value
          session.add(etag)
        else:
          etag.value = value

      def discard_tag(self, name, value=None, *, session):
        ''' Discard the tag matching `(name,value)`.
            Return the tag row discarded or `None` if no match.
        '''
        tag = Tag(name, value)
        tags = orm.tags
        etag = tags.lookup1(session=session, entity_id=self.id, name=name)
        if etag is not None:
          if tag.value is None or tag.value == etag.value:
            session.delete(etag)
        return etag

    class Tags(Base, BasicTableMixin):
      ''' The table of tags associated with entities.
      '''

      __tablename__ = 'tags'
      entity_id = Column(
          Integer,
          ForeignKey("entities.id"),
          nullable=False,
          index=True,
          primary_key=True,
          comment='entity id'
      )
      name = Column(String, comment='tag name', index=True, primary_key=True)
      float_value = Column(
          Float,
          nullable=True,
          default=None,
          index=True,
          comment='tag value in numeric form'
      )
      string_value = Column(
          String,
          nullable=True,
          default=None,
          index=True,
          comment='tag value in string form'
      )
      structured_value = Column(
          JSON, nullable=True, default=None, comment='tag value in JSON form'
      )

      @staticmethod
      @require(
          lambda float_value: float_value is None or
          isinstance(float_value, float)
      )
      @require(
          lambda string_value: string_value is None or
          isinstance(string_value, str)
      )
      @require(
          lambda structured_value: structured_value is None or
          not isinstance(structured_value, (float, str))
      )
      @require(
          lambda float_value, string_value, structured_value: sum(
              map(
                  lambda value: value is not None,
                  [float_value, string_value, structured_value]
              )
          ) < 2
      )
      def pick_value(float_value, string_value, structured_value):
        ''' Chose amongst the values available.
        '''
        if float_value is None:
          if string_value is None:
            return structured_value
          return string_value
        i = int(float_value)
        return i if i == float_value else float_value

      @property
      def value(self):
        ''' Return the value for this `Tag`.
        '''
        return self.pick_value(
            self.float_value, self.string_value, self.structured_value
        )

      @value.setter
      def value(self, new_value):
        new_values = None, None, new_value
        if isinstance(new_value, datetime):
          # store datetime as unixtime
          new_values = datetime2unixtime(new_value), None, None
        elif isinstance(new_value, float):
          new_values = new_value, None, None
        elif isinstance(new_value, int):
          f = float(new_value)
          if f == new_value:
            new_values = f, None, None
          else:
            new_values = None, None, new_value
        elif isinstance(new_value, str):
          new_values = None, new_value, None
        self.set_all(*new_values)

      @classmethod
      def value_test(cls, other_value):
        ''' Return `(column,test_value)` for constructing tests against
            `other_value` where `column` if the appropriate SQLAlchemy column
            and `test_value` is the comparison value for testing.

            For most `other_value`s the `test_value`
            will just be `other_value`,
            but for certain types the `test_value` will be:
            * `NoneType`: `None`, and the column will also be `None`
            * `datetime`: `datetime2unixtime(other_value)`
        '''
        if other_value is None:
          return None, None
        if isinstance(other_value, datetime):
          return cls.float_value, datetime2unixtime(other_value)
        if isinstance(other_value, float):
          return cls.float_value, other_value
        if isinstance(other_value, int):
          f = float(other_value)
          if f == other_value:
            return cls.float_value, f
        if isinstance(other_value, str):
          return cls.string_value, other_value
        return cls.structured_value, other_value

      @require(
          lambda float_value: float_value is None or
          isinstance(float_value, float)
      )
      @require(
          lambda string_value: string_value is None or
          isinstance(string_value, str)
      )
      @require(
          lambda structured_value: structured_value is None or
          not isinstance(structured_value, (float, str))
      )
      @require(
          lambda float_value, string_value, structured_value: sum(
              map(
                  lambda value: value is not None,
                  [float_value, string_value, structured_value]
              )
          ) < 2
      )
      def set_all(self, float_value, string_value, structured_value):
        ''' Set all the value fields.
        '''
        self.float_value, self.string_value, self.structured_value = (
            float_value, string_value, structured_value
        )

      @property
      def unixtime(self):
        ''' The UNIX timestamp is stored as a float.
        '''
        return self.float_value

      @unixtime.setter
      @require(lambda timestamp: isinstance(timestamp, float))
      def unixtime(self, timestamp):
        self.set_all(timestamp, None, None)

    self.tags = Tags
    self.entities = Entities

  # pylint: disable=too-many-branches,too-many-locals
  @pfx_method
  def search(self, criteria, *, session, mode='tagged'):
    ''' Construct a query to match `Entity` rows
        matching the supplied `criteria` iterable.
        Return an SQLAlchemy `Query`.

        The `mode` parameter has the following values:
        * `'id'`: the query only yields entity ids
        * `'entity'`: (default) the query yields entities without tags
        * `'tagged'`: (default) the query yields entities left
        outer joined with their matching tags

        Note that the `'tagged'` result produces multiple rows for any
        entity with multiple tags, and that this requires the caller to
        fold entities with multiple tags together.

        *Note*:
        due to implementation limitations
        the SQL query itself may not apply all the criteria,
        so every criterion must still be applied
        to the results
        using its `.match_entity` method.

        If `name` is omitted or `None` the query will match log entities
        otherwise the entity with the specified `name`.

        The `criteria` should be an iterable of `SQTCriterion` instances
        used to construct the query.
    '''
    entities = self.entities
    tags = self.tags
    # first condition:
    #   select tags as alias where constraint
    # following:
    #   inner join tags as alias using entity_id where constraint
    # inner join entities on
    sqlps = []
    entity_tests = []
    per_tag_aliases = {}
    per_tag_tests = defaultdict(list)  # tag_name=>[tests...]
    sqlps = []
    for criterion in criteria:
      with Pfx(criterion):
        assert isinstance(criterion, SQTCriterion), (
            "not an SQTCriterion: %s:%r" %
            (type(criterion).__name__, criterion)
        )
        if isinstance(criterion, TagBasedTest):
          # we know how to treat these efficiently
          # by mergeing conditions on the same tag name
          tag = criterion.tag
          tag_name = tag.name
          tag_value = tag.value
          if tag_name == 'id':
            alias = entities
            entity_tests.append(
                criterion.SQL_ID_VALUE_COMPARISON_FUNCS[criterion.comparison]
                (entities, tag_value)
            )
          elif tag_name == 'name':
            alias = entities
            entity_tests.append(
                criterion.SQL_NAME_VALUE_COMPARISON_FUNCS[criterion.comparison]
                (entities, tag_value)
            )
          elif tag_name == 'unixtime':
            alias = entities
            entity_tests.append(
                criterion.SQL_UNIXTIME_VALUE_COMPARISON_FUNCS[
                    criterion.comparison](entities, tag_value)
            )
          else:
            tag_tests = per_tag_tests[tag_name]
            if tag_tests:
              # reuse existing alias - same tag name
              alias = per_tag_aliases[tag_name]
            else:
              # first test for this tag - make an alias
              per_tag_aliases[tag_name] = aliased(tags)
            tag_tests.append(
                criterion.SQL_TAG_VALUE_COMPARISON_FUNCS[criterion.comparison]
                (per_tag_aliases[tag_name], tag_value)
            )
        else:
          try:
            sqlp = criterion.sql_parameters(self)
          except ValueError:
            warning("SKIP, cannot compute sql_parameters")
            continue
          sqlps.append(sqlp)
    query = session.query(entities.id, entities.unixtime, entities.name)
    prev_entity_id_column = entities.id
    if entity_tests:
      query = query.filter(*entity_tests)
    for tag_name, tag_tests in per_tag_tests.items():
      alias = per_tag_aliases[tag_name]
      alias_entity_id_column = alias.entity_id
      query = query.join(
          alias, alias_entity_id_column == prev_entity_id_column
      ).filter(alias.name == tag_name, *tag_tests)
      prev_entity_id_column = alias_entity_id_column
    # further JOINs on less direct SQL tests
    for sqlp in sqlps:
      sqlp_entity_id_column = sqlp.entity_id_column
      query = query.join(
          sqlp.alias,
          sqlp_entity_id_column == prev_entity_id_column,
      )
      query = query.filter(sqlp.constraint)
      prev_entity_id_column = sqlp_entity_id_column
    with Pfx("mode=%r", mode):
      if mode == 'id':
        pass
      elif mode == 'entity':
        query = session.query(entities.id, entities.unixtime,
                              entities.name).filter(
                                  entities.id.in_(query.distinct())
                              )
      elif mode == 'tagged':
        query = query.join(
            tags, isouter=True
        ).filter(entities.id is not None).add_columns(
            tags.name.label('tag_name'),
            tags.float_value.label('tag_float_value'),
            tags.string_value.label('tag_string_value'),
            tags.structured_value.label('tag_structured_value'),
        )
      else:
        raise ValueError("unrecognised mode")
    return query

class SQLTagSet(SingletonMixin, TagSet):
  ''' A singleton `TagSet` attached to an `SQLTags` instance.
  '''

  @staticmethod
  # pylint: disable=redefined-builtin
  def _singleton_key(*, sqltags, _id, **_):
    return builtin_id(sqltags), _id

  def _singleton_also_indexmap(self):
    ''' Return the map of secondary key names and their values.
    '''
    d = super()._singleton_also_indexmap()
    assert self.id is not None
    d.update(id=self.id)
    name = self.name
    if name is not None:
      d.update(name=name)
    return d

  @typechecked
  def __init__(self, *, sqltags, name=None, _id: int, unixtime=None, **kw):
    try:
      pre_sqltags = self.__dict__['sqltags']
    except KeyError:
      super().__init__(_id=_id, **kw)
      # pylint: disable=unexpected-keyword-arg
      self.__dict__.update(_name=name, _unixtime=unixtime, sqltags=sqltags)
      self._singleton_also_index()
    else:
      assert pre_sqltags is sqltags, "pre_sqltags is not sqltags: %s vs %s" % (
          pre_sqltags, sqltags
      )

  def __str__(self):
    return "id=%r:%s(%s)" % (self.id, self.name, super().__str__())

  def __hash__(self):
    return id(self)

  @property
  def name(self):
    ''' Return the `.name`.
    '''
    return self._name

  @name.setter
  def name(self, new_name):
    ''' Set the `.name`.
    '''
    if new_name != self._name:
      e = self._get_db_entity()
      e.name = new_name
      self._name = new_name
      self._singleton_also_index()

  @property
  def unixtime(self):
    return self._unixtime

  @contextmanager
  def db_session(self, *, session=None):
    ''' Context manager to obtain a new session if required,
        just a shim for `self.sqltags.db_session`.
    '''
    with self.sqltags.db_session(session=session) as session2:
      yield session2

  def _get_db_entity(self):
    ''' Return database `Entities` instance for this `SQLTagSet`.
    '''
    e = self.sqltags.db_entity(self.id)
    assert e is not None, "no sqltags.db_entity(id=%r)" % self.id
    return e

  # pylint: disable=arguments-differ
  @tag_or_tag_value
  def set(self, tag_name, value, *, skip_db=False, verbose=None):
    if tag_name == 'id':
      raise ValueError("may not set pseudoTag %r" % (tag_name,))
    with self.db_session():
      if tag_name in ('name', 'unixtime'):
        setattr(self, '_' + tag_name, value)
        if not skip_db:
          ifverbose(verbose, "+ %s", Tag(tag_name, value))
          setattr(self._get_db_entity(), tag_name, value)
      else:
        super().set(tag_name, value, verbose=verbose)
        if not skip_db:
          self.add_db_tag(tag_name, value)

  @pfx_method
  def add_db_tag(self, tag_name, value=None):
    ''' Add a tag to the database.
    '''
    with self.db_session() as session:
      e = self._get_db_entity()
      return e.add_tag(tag_name, value, session=session)

  # pylint: disable=arguments-differ
  @tag_or_tag_value
  def discard(self, tag_name, value, *, skip_db=False, verbose=None):
    if tag_name == 'id':
      raise ValueError("may not discard pseudoTag %r" % (tag_name,))
    if tag_name in ('name', 'unixtime'):
      if value is None or getattr(self, tag_name) == value:
        setattr(self, '_' + tag_name, None)
        if not skip_db:
          ifverbose(verbose, "- %s", Tag(tag_name, value))
          setattr(self._get_db_entity(), tag_name, None)
    else:
      super().discard(tag_name, value, verbose=verbose)
      if not skip_db:
        self.discard_db_tag(tag_name, value)

  def discard_db_tag(self, tag_name, value=None):
    ''' Discard a tag from the database.
    '''
    with self.db_session() as session:
      return self._get_db_entity().discard_tag(
          tag_name, value, session=session
      )

  def parent_tagset(self, tag_name='parent'):
    ''' Return the parent `TagSet` as defined by a `Tag`,
        by default the `Tag` named `'parent'`.
    '''
    return self.sqltags[self[tag_name]]

  def child_tagsets(self, tag_name='parent'):
    ''' Return the child `TagSet`s as defined by their parent `Tag`,
        by default the `Tag` named `'parent'`.
    '''
    children = set(
        self.sqltags.find([SQLTagBasedTest.by_tag_value(tag_name, self.id)])
    )
    if self.name:
      children += set(
          self.sqltags.find(
              [SQLTagBasedTest.by_tag_value(tag_name, self.name)]
          )
      )
    return children

class SQLTags(TagSets):
  ''' A class using an SQL database to store its `TagSet`s.
  '''

  TagSetClass = SQLTagSet

  # pylint: disable=super-init-not-called
  @require(
      lambda ontology: ontology is None or isinstance(ontology, TagsOntology)
  )
  def __init__(self, db_url=None, ontology=None):
    if not db_url:
      db_url = self.infer_db_url()
    self.__tstate = ThreadState()
    self.orm = SQLTagsORM(db_url=db_url)
    if ontology is None:
      ontology = TagsOntology(self)
    self.db_url = db_url
    self.ontology = ontology
    self._lock = RLock()
    self.tags = SQLTagProxies(self.orm)

  def __str__(self):
    return "%s(db_url=%r)" % (
        type(self).__name__, getattr(self, 'db_url', None)
    )

  @contextmanager
  def db_session(self, *, new=False, session=None):
    ''' Context manager to obtain a db session if required,
        just a shim for `self.orm.session()`.
    '''
    with self.orm.session(new=new, session=session) as session2:
      yield session2

  @property
  def default_db_session(self):
    ''' The current per-`Thread` SQLAlchemy Session.
    '''
    session = self.orm.sqla_state.session
    if session is None:
      raise RuntimeError("no default db session")
    return session

  def flush(self):
    ''' Flush the current session state to the database.
    '''
    self.default_db_session.flush()

  @typechecked
  def default_factory(self, name: [str, None], *, unixtime=None, tags=None):
    ''' Fetch or create an `SQLTagSet` for `name`.

        Note that `name` may be `None` to create a new "log" entry.
    '''
    if tags is None:
      tags = ()
    te = None if name is None else self.get(name)
    if te is None:
      if unixtime is None:
        unixtime = time.time()
      with self.db_session() as session:
        entity = self.orm.entities(name=name, unixtime=unixtime)
        session.add(entity)
        for tag in tags:
          entity.add_tag(tag.name, tag.value, session=session)
        session.flush()
        te = self.get(entity.id)
      assert te is not None
    else:
      if unixtime is not None:
        te.unixtime = unixtime
      for tag in tags:
        te.set(tag.name, tag.value)
    return te

  # pylint: disable=arguments-differ
  def get(self, index, default=None):
    ''' Return an `SQLTagSet` matching `index`, or `None` if there is no such entity.
    '''
    if isinstance(index, int):
      te = self.TagSetClass.singleton_also_by('id', index)
      if te is not None:
        return te
      tes = self.find([SQTEntityIdTest([index])])
    elif isinstance(index, str):
      te = self.TagSetClass.singleton_also_by('name', index)
      if te is not None:
        return te
      tes = self.find([SQLTagBasedTest(index, True, Tag('name', index), '=')])
    else:
      raise TypeError("unsupported index: %s:%r" % (type(index), index))
    tes = list(tes)
    if not tes:
      return default
    te, = tes
    return te

  @locked
  def __getitem__(self, index):
    ''' Return an `SQLTagSet` for `index` (an `int` or `str`).
    '''
    with self.db_session(new=True):
      te = self.get(index)
      if te is None:
        if isinstance(index, int):
          raise IndexError(index)
        te = self.default_factory(index)
    return te

  @locked
  def __setitem__(self, index, te):
    ''' Dummy `__setitem__` which checks `te` against the db by type
        because the factory inserts it into the database.
    '''
    assert isinstance(te, SQLTagSet)
    assert te.sqltags is self

  def keys(self, *, prefix=None):
    ''' Yield all the nonNULL names.

        Constrain the names to those starting with `prefix`
        if not `None`.
    '''
    entities = self.orm.entities
    entities_table = entities.__table__  # pylint: disable=no-member
    name_column = entities_table.c.name
    q = select([name_column]).where(name_column.isnot(None))
    if prefix is not None:
      q = q.where(name_column.like(prefix2like(prefix, '\\'), '\\'))
    conn = self.orm.engine.connect()
    result = conn.execute(q)
    for row in result:
      name = row.name
      if prefix is None or name.startswith(prefix):
        yield name
    conn.close()

  def items(self, *, prefix=None):
    ''' Return an iterable of `(tagset_name,TagSet)`.
        Excludes unnamed `TagSet`s.

        Constrain the names to those starting with `prefix`
        if not `None`.
    '''
    return map(lambda te: (te.name, te), self.values(prefix=prefix))

  def values(self, *, prefix=None):
    ''' Return an iterable of the named `TagSet`s.
        Excludes unnamed `TagSet`s.

        Constrain the names to those starting with `prefix`
        if not `None`.
    '''
    if prefix is None:
      criterion = "name"
    else:
      criterion = f"name~{prefix}*"
    return self.find(criterion)

  @staticmethod
  @fmtdoc
  def infer_db_url(envvar=None, default_path=None):
    ''' Infer the database URL.

        Parameters:
        * `envvar`: environment variable to specify a default,
          default from `DBURL_ENVVAR` (`{DBURL_ENVVAR}`).
    '''
    if envvar is None:
      envvar = DBURL_ENVVAR
    if default_path is None:
      default_path = DBURL_DEFAULT
    db_url = os.environ.get(envvar)
    if not db_url:
      db_url = expanduser(default_path)
    return db_url

  def init(self):
    ''' Initialise the database.
    '''
    self.orm.define_schema()

  def db_entity(self, index):
    ''' Return the `Entities` instance for `index` or `None`.
    '''
    # require a session for use with the entity
    session = self.default_db_session
    entities = self.orm.entities
    if isinstance(index, int):
      return entities.lookup1(id=index, session=session)
    if isinstance(index, str):
      return entities.lookup1(name=index, session=session)
    raise TypeError(
        "expected index to be int or str, got %s:%s" % (type(index), index)
    )

  @property
  def metanode(self):
    ''' The metadata node.
    '''
    return self[0]

  def find(self, criteria):
    ''' Generate and run a query derived from `criteria`
        yielding `SQLTagSet` instances.

        Parameters:
        * `criteria`: an iterable of search criteria
          which should be `SQTCriterion`s
          or a `str` suitable for `SQTCriterion.from_str`.
          A string may also be supplied, suitable for `SQTCriterion.from_str`.
    '''
    if isinstance(criteria, str):
      criteria = [criteria]
    else:
      criteria = list(criteria)
    post_criteria = []
    for i, criterion in enumerate(criteria):
      with Pfx(str(criterion)):
        if isinstance(criterion, str):
          criterion = criteria[i] = SQTCriterion.from_str(criterion)
        if not criterion.SQL_COMPLETE:
          post_criteria.append(criterion)
    with self.db_session() as session:
      orm = self.orm
      query = orm.search(
          criteria,
          mode='tagged',
          session=session,
      )
      # merge entities and tag information
      tags = self.orm.tags
      entity_map = {}
      for row in query:
        entity_id = row.id
        te = entity_map.get(entity_id)
        if not te:
          # not seen before
          te = entity_map[entity_id] = self.TagSetClass(
              _id=entity_id,
              _ontology=self.ontology,
              name=row.name,
              unixtime=row.unixtime,
              sqltags=self
          )
        # a None tag_name means no tags
        if row.tag_name is not None:
          # set the dict entry directly - we are loading db values,
          # not applying them to the db
          tag_value = tags.pick_value(
              row.tag_float_value, row.tag_string_value,
              row.tag_structured_value
          )
          te.set(row.tag_name, tag_value, skip_db=True)
    if not post_criteria:
      yield from entity_map.values()
    else:
      # verify all the entities for criteria which do not express well as SQL
      for te in entity_map.values():
        if all(criterion.match_tagged_entity(te)
               for criterion in post_criteria):
          yield te

  def import_csv_file(self, f, *, update_mode=False):
    ''' Import CSV data from the file `f`.

        If `update_mode` is true
        named records which already exist will update from the data,
        otherwise the conflict will raise a `ValueError`.
    '''
    csvr = csv.reader(f)
    for csvrow in csvr:
      with Pfx(csvr.line_num):
        te = TagSet.from_csvrow(csvrow)
        self.import_tagged_entity(te, update_mode=update_mode)

  def import_tagged_entity(self, te, *, update_mode=False) -> None:
    ''' Import the `TagSet` `te`.

        This updates the database with the contents of the supplied `TagSet`,
        which has no inherent relationship to the database.

        If `update_mode` is true
        named records which already exist will update from `te`,
        otherwise the conflict will raise a `ValueError`.
    '''
    with self.db_session() as session:
      entities = self.orm.entities
      if te.name is None:
        # new log entry
        e = entities(name=None, unixtime=te.unixtime)
        session.add(e)
      else:
        e = entities.lookup1(name=te.name, session=session)
        if e:
          if not update_mode:
            raise ValueError("entity named %r already exists" % (te.name,))
        else:
          # new named entry
          e = entities(name=te.name, unixtime=te.unixtime)
          session.add(e)
      # update the db entry
      for tag in te.tags:
        with Pfx(tag):
          e.add_tag(tag)

class BaseSQLTagsCommand(BaseCommand, TagsCommandMixin):
  ''' Common features for commands oriented around an `SQLTags` database.
  '''

  TAGSETS_CLASS = SQLTags

  TAGSET_CRITERION_CLASS = SQTCriterion

  TAG_BASED_TEST_CLASS = SQLTagBasedTest

  GETOPT_SPEC = 'f:'

  # TODO:
  # export_csv [criteria...] >csv_data
  #   Export selected items to CSV data.
  # import_csv <csv_data
  #   Import CSV data.
  # init
  #   Initialise the database.

  USAGE_FORMAT = '''Usage: {cmd} [-f db_url] subcommand [...]
  -f db_url SQLAlchemy database URL or filename.
            Default from ${DBURL_ENVVAR} (default '{DBURL_DEFAULT}').'''

  USAGE_KEYWORDS = {
      'DBURL_DEFAULT': DBURL_DEFAULT,
      'DBURL_ENVVAR': DBURL_ENVVAR,
  }

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    options = self.options
    db_url = self.TAGSETS_CLASS.infer_db_url()
    options.db_url = db_url
    options.sqltags = None

  def apply_opt(self, opt, val):
    ''' Apply a command line option.
    '''
    options = self.options
    if opt == '-f':
      options.db_url = val
    else:
      super().apply_opt(opt, val)

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    options = self.options
    db_url = options.db_url
    sqltags = self.TAGSETS_CLASS(db_url)
    with sqltags:
      with stackattrs(options, sqltags=sqltags, verbose=True):
        yield

  @classmethod
  def parse_tagset_criterion(cls, arg, tag_based_test_class=None):
    ''' Parse tag criteria from `argv`.

        The criteria may be either:
        * an integer specifying a `Tag` id
        * a sequence of tag criteria
    '''
    # try a single int argument
    try:
      index = int(arg)
    except ValueError:
      return super().parse_tagset_criterion(
          arg, tag_based_test_class=tag_based_test_class
      )
    else:
      return SQTEntityIdTest([index])

  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Start an interactive database shell.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    orm = self.options.sqltags.orm
    db_url = orm.db_url
    if db_url.startswith("sqlite://"):
      db_fspath = orm.db_fspath
      print("sqlite3", db_fspath)
      run(['sqlite3', db_fspath], check=True)
      return 0
    error("I do not know how to get a db shell for %r", db_url)
    return 1

  def cmd_edit(self, argv):
    ''' Usage: edit criteria...
          Edit the entities specified by criteria.
    '''
    options = self.options
    sqltags = options.sqltags
    badopts = False
    tag_criteria, argv = self.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("remaining unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    tes = list(sqltags.find(tag_criteria))
    changed_tes = SQLTagSet.edit_entities(tes)  # verbose=state.verbose
    for te in changed_tes:
      print("changed", repr(te.name or te.id))

  def cmd_export(self, argv):
    ''' Usage: {cmd} {{tag[=value]|-tag}}...
          Export entities matching all the constraints.
          The output format is CSV data with the following columns:
          * `unixtime`: the entity unixtime, a float
          * `id`: the entity database row id, an integer
          * `name`: the entity name
          * `tags`: a column per `Tag`
    '''
    options = self.options
    sqltags = options.sqltags
    badopts = False
    tag_criteria, argv = self.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    csvw = csv.writer(sys.stdout)
    for te in sqltags.find(tag_criteria):
      with Pfx(te):
        csvw.writerow(te.csvrow)

  # pylint: disable=too-many-locals
  def cmd_find(self, argv):
    ''' Usage: {cmd} [-o output_format] {{tag[=value]|-tag}}...
          List entities matching all the constraints.
          -o output_format
                      Use output_format as a Python format string to lay out
                      the listing.
                      Default: {FIND_OUTPUT_FORMAT_DEFAULT}
    '''
    options = self.options
    sqltags = options.sqltags
    badopts = False
    output_format = FIND_OUTPUT_FORMAT_DEFAULT
    opts, argv = getopt(argv, 'o:')
    for option, value in opts:
      with Pfx(option):
        if option == '-o':
          ## TODO: indirects through the config file
          ## output_format = sqltags.resolve_format_string(value)
          output_format = value
        else:
          raise RuntimeError("unsupported option")
    tag_criteria, argv = self.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    for te in sqltags.find(tag_criteria):
      with Pfx(te):
        try:
          output = te.format_as(output_format, error_sep='\n  ')
        except FormatAsError as e:
          error(str(e))
          xit = 1
          continue
        print(output.replace('\n', ' '))
        for tag in sorted(te):
          if tag.name != 'headline':
            print(" ", tag)
    return xit

  def cmd_import(self, argv):
    ''' Usage: {cmd} [{{-u|--update}}] {{-|srcpath}}...
          Import CSV data in the format emitted by "export".
          Each argument is a file path or "-", indicating standard input.
          -u, --update  If a named entity already exists then update its tags.
                        Otherwise this will be seen as a conflict
                        and the import aborted.

        TODO: should this be a transaction so that an import is all or nothing?
    '''
    options = self.options
    sqltags = options.sqltags
    badopts = False
    update_mode = False
    opts, argv = getopt(argv, 'u')
    for option, _ in opts:
      with Pfx(option):
        if option in ('-u', '--update'):
          update_mode = True
        else:
          raise RuntimeError("unsupported option")
    if not argv:
      warning("missing srcpaths")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    for srcpath in argv:
      if srcpath == '-':
        with Pfx("stdin"):
          sqltags.import_csv_file(sys.stdin, update_mode=update_mode)
      else:
        with Pfx(srcpath):
          with open(srcpath) as f:
            sqltags.import_csv_file(f, update_mode=update_mode)

  def cmd_init(self, argv):
    ''' Usage: {cmd}
          Initialise the database.
          This includes defining the schema and making the root metanode.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    self.options.sqltags.init()

  # pylint: disable=too-many-locals.too-many-branches.too-many-statements
  def cmd_log(self, argv):
    ''' Record a log entry.

        Usage: {cmd} [-c category,...] [-d when] [-D strptime] {{-|headline}} [tags...]
          Record entries into the database.
          If headline is '-', read headlines from standard input.
          -c categories
            Specify the categories for this log entry.
            The default is to recognise a leading CAT,CAT,...: prefix.
          -d when
            Use when, an ISO8601 date, as the log entry timestamp.
          -D strptime
            Read the time from the start of the headline
            according to the provided strptime specification.
    '''
    options = self.options
    categories = None
    dt = None
    strptime_format = None
    badopts = False
    opts, argv = getopt(argv, 'c:d:D:', '')
    for opt, val in opts:
      with Pfx(opt if val is None else f"{opt} {val!r}"):
        if opt == '-c':
          categories = map(str.lower, filter(None, val.split(',')))
        elif opt == '-d':
          try:
            dt = datetime.fromisoformat(val)
          except ValueError as e:
            warning("unhandled ISO format date: %s", e)
            badopts = True
          if dt.tzinfo is None:
            # create a nonnaive datetime in the local zone
            dt = dt.astimezone()
        elif opt == '-D':
          strptime_format = val
        else:
          raise RuntimeError("unhandled option")
    if dt is not None and strptime_format is not None:
      warning("-d and -D are mutually exclusive")
      badopts = True
    if strptime_format is not None:
      with Pfx("strptime format %r", strptime_format):
        if '%' not in strptime_format:
          warning("no time fields!")
          badopts = True
        else:
          # normalise the format and count the words
          strptime_format = strptime_format.strip()
          strptime_words = strptime_format.split()
          strptime_nwords = len(strptime_words)
          strptime_format = ' '.join(strptime_words)
    if not argv:
      argv = ['-']
      if sys.stdin.isatty():
        warning("reading log lines from stdin...")
    cmdline_headline = argv.pop(0)
    log_tags = []
    while argv:
      tag_s = argv.pop(0)
      with Pfx("tag %r", tag_s):
        try:
          tag = Tag.from_str(tag_s)
        except ValueError:
          argv.insert(0, tag_s)
          break
        else:
          if tag.value is None:
            argv.insert(0, tag_s)
            break
        log_tags.append(tag)
    if argv:
      warning(
          "extra arguments after %d tags: %s", len(log_tags), ' '.join(argv)
      )
      badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    xit = 0
    use_stdin = cmdline_headline == '-'
    sqltags = options.sqltags
    for lineno, headline in enumerate((sys.stdin if use_stdin else
                                       (cmdline_headline,))):
      with Pfx(*(("%d: %s", lineno, headline) if use_stdin else (headline,))):
        headline = headline.rstrip('\n')
        unixtime = None
        if strptime_format:
          with Pfx("strptime %r", strptime_format):
            headparts = headline.split(None, strptime_nwords)
            if len(headparts) < strptime_nwords:
              warning(
                  "not enough fields in headline, using current time: %r",
                  headline
              )
              xit = 1
            else:
              strptime_text = ' '.join(headparts[:strptime_nwords])
              try:
                strptime_dt = datetime.strptime(strptime_text, strptime_format)
              except ValueError as e:
                warning(
                    "cannot parse %r, using current time: %s", strptime_text, e
                )
                xit = 1
              else:
                unixtime = datetime2unixtime(strptime_dt)
                headline = ' '.join(headparts[strptime_nwords:])
        if unixtime is None:
          unixtime = time.time() if dt is None else dt.timestamp()
        if categories is None:
          # infer categories from leading "FOO,BAH:" text
          m = CATEGORIES_PREFIX_re.match(headline)
          if m:
            tag_categories = map(
                str.lower, filter(None,
                                  m.group('categories').split(','))
            )
            headline = headline[len(m.group()):]
          else:
            tag_categories = ()
        else:
          tag_categories = categories
        log_tags.append(Tag('headline', headline))
        if tag_categories:
          log_tags.append(Tag('categories', list(tag_categories)))
        sqltags.default_factory(None, unixtime=unixtime, tags=log_tags)
    return xit

  # pylint: disable=too-many-branches
  def cmd_tag(self, argv):
    ''' Usage: {cmd} {{-|entity-name}} {{tag[=value]|-tag}}...
          Tag an entity with multiple tags.
          With the form "-tag", remove that tag from the direct tags.
          A entity-name named "-" indicates that entity-names should
          be read from the standard input.
    '''
    badopts = False
    if not argv:
      raise GetoptError("missing entity-name")
    name = argv.pop(0)
    if not argv:
      raise GetoptError("missing tags")
    try:
      tag_choices = self.parse_tag_choices(argv)
    except ValueError as e:
      raise GetoptError(str(e)) from e
    if badopts:
      raise GetoptError("bad arguments")
    if name == '-':
      names = [line.rstrip('\n') for line in sys.stdin]
    else:
      names = [name]
    xit = 0
    options = self.options
    sqltags = options.sqltags
    with stackattrs(state, verbose=True):
      for name in names:
        with Pfx(name):
          try:
            index = int(name)
          except ValueError:
            index = name
          te = sqltags.get(index)
          if te is None:
            error("missing")
            xit = 1
            continue
          tags = te.tags
          for tag_choice in tag_choices:
            if tag_choice.choice:
              if tag_choice.tag not in tags:
                te.set(tag_choice.tag)
            else:
              if tag_choice.tag in tags:
                te.discard(tag_choice.tag)
    return xit

class SQLTagsCommand(BaseSQLTagsCommand):
  ''' `sqltags` main command line utility.
  '''

  def cmd_ns(self, argv):
    ''' Usage: {cmd} entity-names...
          List entities and their tags.
    '''
    if not argv:
      raise GetoptError("missing entity_names")
    xit = 0
    options = self.options
    sqltags = options.sqltags
    for name in argv:
      with Pfx(name):
        try:
          index = int(name)
        except ValueError:
          index = name
        te = sqltags.get(index)
        if te is None:
          error("missing")
          xit = 1
          continue
        print(name)
        for tag in sorted(te.tags()):
          print(" ", tag)
    return xit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
