#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Simple SQL based tagging
    and the associated `sqltags` command line script,
    supporting both tagged named objects and tagged timestamped log entries.

    Compared to `cs.fstags` and its associated `fstags` command,
    this is oriented towards large numbers of items
    not naturally associated with filesystem objects.
    My initial use case is an activity log.

    Many basic tasks can be performed with the `sqltags` command line utility,
    documented under the `SQLTagsCommand` class below.
'''

from abc import abstractmethod
from builtins import id as builtin_id
from collections import namedtuple
from contextlib import contextmanager
import csv
from datetime import datetime
from fnmatch import fnmatchcase
from getopt import getopt, GetoptError
import os
from os.path import abspath, expanduser, exists as existspath
import re
import sys
from threading import RLock
import time
from typing import List
from icontract import require
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, JSON, ForeignKey,
    UniqueConstraint
)
from sqlalchemy.orm import sessionmaker, aliased
from sqlalchemy.sql.expression import and_
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs, pushattrs, popattrs
from cs.dateutils import UNIXTimeMixin, datetime2unixtime
from cs.deco import fmtdoc
from cs.fileutils import makelockfile
from cs.lex import FormatAsError, cutprefix, get_decimal_value
from cs.logutils import error, warning, ifverbose
from cs.mappings import PrefixedMappingProxy
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method, XP
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    orm_method,
    auto_session,
    orm_auto_session,
    BasicTableMixin,
    HasIdMixin,
    _state as sqla_state,
)
from cs.tagset import (
    TagSet, Tag, TaggedEntityCriterion, TagBasedTest, TagsCommandMixin,
    TaggedEntity
)
from cs.threads import locked, State
from cs.upd import print  # pylint: disable=redefined-builtin

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
        'cs.cmdutils', 'cs.context', 'cs.dateutils', 'cs.deco', 'cs.edit',
        'cs.fileutils', 'cs.lex', 'cs.logutils', 'cs.mappings', 'cs.pfx',
        'cs.resources', 'cs.sqlalchemy_utils', 'cs.tagset',
        'cs.threads>=20201025', 'icontract', 'sqlalchemy', 'typeguard'
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
  return SQLTagsCommand().run(argv)

state = State(verbose=sys.stderr.isatty())

def verbose(msg, *a):
  ''' Emit message if in verbose mode.
  '''
  ifverbose(state.verbose, msg, *a)

def glob2like(glob: str) -> str:
  ''' Convert a filename glob to an SQL LIKE pattern.
  '''
  assert '[' not in glob
  return glob.replace('*', '%').replace('?', '_')

class SQLParameters(namedtuple(
    'SQLParameters', 'criterion table alias entity_id_column constraint')):
  ''' The parameters required for constructing queries or extending queries with JOINs.
  '''

class SQTCriterion(TaggedEntityCriterion):
  ''' Subclass of `TaggedEntityCriterion` requiring an `.sql_parameters` method
      which returns an `SQLParameters` providing the information required
      to construct an sqlalchemy query.
      It also resets `.CRITERION_PARSE_CLASSES`, which will pick up
      the SQL capable criterion classes below.
  '''

  # list of TaggedEntityCriterion classes
  # whose .parse methods are used by .parse
  CRITERION_PARSE_CLASSES = []

  @abstractmethod
  def sql_parameters(self, orm) -> SQLParameters:
    ''' Subclasses must return am `SQLParameters` instance
        parameterising the SQL queries that follow.
    '''
    raise NotImplementedError("sql_parameters")

class SQTEntityIdTest(SQTCriterion):
  ''' A test on `entity.id`.
  '''

  @typechecked
  def __init__(self, ids: List[int]):
    self.entity_ids = ids

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
        table=entities,
        alias=alias,
        entity_id_column=alias.id,
        constraint=alias.id.in_(self.entity_ids),
    )
    return sqlp

  def match_tagged_entity(self, te: TaggedEntity) -> bool:
    ''' Test the `TaggedEntity` `te` against `self.entity_ids`.
    '''
    return te.id in self.entity_ids

SQTCriterion.CRITERION_PARSE_CLASSES.append(SQTEntityIdTest)
SQTCriterion.TAG_BASED_TEST_CLASS = SQTEntityIdTest

class SQLTagBasedTest(TagBasedTest, SQTCriterion):
  ''' A `cs.tagset.TagBasedTest` extended with a `.sql_parameters` method.
  '''

  # functions returning SQL tag.value tests based on self.comparison
  SQL_TAG_VALUE_COMPARISON_FUNCS = {
      None:
      lambda alias, cmp_value: and_(
          alias.float_value is None, alias.string_value is None, alias.
          structured_value is None
      ),
      '=':
      lambda alias, cmp_value: (
          alias.float_value == cmp_value
          if isinstance(cmp_value, (int, float)) else (
              alias.string_value == cmp_value
              if isinstance(cmp_value, str) else
              (alias.structured_value == cmp_value)
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
                  isnot(alias.string_value, None),
                  alias.string_value.like(glob2like(cmp_value))
              ),
          ],
          else_=True,
      )
      ##'~/': # requires sqlalchemy 1.4
      ##lambda alias, cmp_value: alias.string_value.regexp_match(cmp_value),
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

  @pfx_method
  def sql_parameters(self, orm) -> SQLParameters:
    tag = self.tag
    if tag.name in ('name', 'unixtime'):
      table = entities = orm.entities
      alias = aliased(entities)
      entity_id_column = alias.id
      if tag.name == 'name':
        if not isinstance(tag.value, str):
          raise ValueError(
              "name comparison requires a str, got %s:%r" %
              (type(tag.value), tag.value)
          )
        constraint_fn = self.SQL_NAME_VALUE_COMPARISON_FUNCS.get(
            self.comparison
        )
        constraint = constraint_fn and constraint_fn(alias, tag.value)
      elif tag.name == 'unixtime':
        if not isinstance(tag.value, (int, float)):
          raise ValueError(
              "unixtime comparison requires a float, got %s:%r" %
              (type(tag.value), tag.value)
          )
        constraint_fn = self.SQL_UNIXTIME_VALUE_COMPARISON_FUNCS.get(
            self.comparison
        )
        constraint = constraint_fn and constraint_fn(alias, tag.value)
      else:
        raise RuntimeError("unhandled non-tag field %r" % (tag.name,))
    else:
      table = tag = self.tag
      tags = orm.tags
      alias = aliased(tags)
      entity_id_column = alias.entity_id
      constraint = alias.name == tag.name
      constraint2_fn = self.SQL_TAG_VALUE_COMPARISON_FUNCS.get(self.comparison)
      constraint2 = constraint2_fn and constraint2_fn(alias, tag.value)
      if constraint2 is not None:
        constraint = and_(constraint, constraint2)
      else:
        warning("no SQLside value test for comparison=%r", self.comparison)
    sqlp = SQLParameters(
        criterion=self,
        table=table,
        alias=alias,
        entity_id_column=entity_id_column,
        constraint=constraint if self.choice else -alias.has(constraint),
    )
    return sqlp

  def match_tagged_entity(self, te: TaggedEntity) -> bool:
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
      result = self.TE_VALUE_COMPARISON_FUNCS[self.comparison
                                              ](te.unixtime, tag_value)
    else:
      te_tag_value = te.tags.get(tag_name)
      if te_tag_value is None:
        result = False
      else:
        result = self.TE_VALUE_COMPARISON_FUNCS[self.comparison
                                                ](te_tag_value, tag_value)
    return result if self.choice else not result

SQTCriterion.CRITERION_PARSE_CLASSES.append(SQLTagBasedTest)
SQTCriterion.TAG_BASED_TEST_CLASS = SQLTagBasedTest

class SQLTagsCommand(BaseCommand, TagsCommandMixin):
  ''' `sqltags` main command line utility.
  '''

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

  @staticmethod
  def apply_defaults(options):
    ''' Set up the default values in `options`.
    '''
    db_url = SQLTags.infer_db_url()
    options.db_url = db_url
    options.sqltags = None

  @staticmethod
  def apply_opts(opts, options):
    ''' Apply command line options.
    '''
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-f':
          options.db_url = val
        else:
          raise RuntimeError("unhandled option")

  @staticmethod
  @contextmanager
  def run_context(argv, options):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    db_url = options.db_url
    sqltags = SQLTags(db_url)
    with stackattrs(options, sqltags=sqltags, verbose=True):
      with sqltags:
        with sqltags.orm.session():
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

  @classmethod
  def cmd_edit(cls, argv, options):
    ''' Usage: edit criteria...
          Edit the entities specified by criteria.
    '''
    sqltags = options.sqltags
    badopts = False
    tag_criteria, argv = cls.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("remaining unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    tes = list(sqltags.find(tag_criteria))
    changed_tes = SQLTaggedEntity.edit_entities(tes)  # verbose=state.verbose
    for te in changed_tes:
      print("changed", repr(te.name or te.id))

  @classmethod
  def cmd_export(cls, argv, options):
    ''' Usage: {cmd} {{tag[=value]|-tag}}...
          Export entities matching all the constraints.
          The output format is CSV data with the following columns:
          * `unixtime`: the entity unixtime, a float
          * `id`: the entity database row id, an integer
          * `name`: the entity name
          * `tags`: a column per `Tag`
    '''
    sqltags = options.sqltags
    badopts = False
    tag_criteria, argv = cls.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    csvw = csv.writer(sys.stdout)
    with sqltags.orm.session() as session:
      for te in sqltags.find(tag_criteria, session=session):
        with Pfx(te):
          csvw.writerow(te.csvrow)

  @classmethod
  def cmd_find(cls, argv, options):
    ''' Usage: {cmd} [-o output_format] {{tag[=value]|-tag}}...
          List entities matching all the constraints.
          -o output_format
                      Use output_format as a Python format string to lay out
                      the listing.
                      Default: {FIND_OUTPUT_FORMAT_DEFAULT}
    '''
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
    tag_criteria, argv = cls.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    with sqltags.orm.session() as session:
      for te in sqltags.find(tag_criteria, session=session):
        with Pfx(te):
          try:
            output = te.format_as(output_format, error_sep='\n  ')
          except FormatAsError as e:
            error(str(e))
            xit = 1
            continue
          print(output.replace('\n', ' '))
          for tag in sorted(te.tags):
            if tag.name != 'headline':
              print(" ", tag)
    return xit

  @staticmethod
  def cmd_import(argv, options):
    ''' Usage: {cmd} [{{-u|--update}}] {{-|srcpath}}...
          Import CSV data in the format emitted by "export".
          Each argument is a file path or "-", indicating standard input.
          -u, --update  If a named entity already exists then update its tags.
                        Otherwise this will be seen as a conflict
                        and the import aborted.

        TODO: should this be a transaction so that an import is all or nothing?
    '''
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
      with sqltags.orm.session():
        if srcpath == '-':
          with Pfx("stdin"):
            sqltags.import_csv_file(sys.stdin, update_mode=update_mode)
        else:
          with Pfx(srcpath):
            with open(srcpath) as f:
              sqltags.import_csv_file(f, update_mode=update_mode)

  @classmethod
  def cmd_init(cls, argv, options):
    ''' Usage: {cmd}
          Initialise the database.
          This includes defining the schema and making the root metanode.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options.sqltags.init()

  # pylint: disable=too-many-locals.too-many-branches.too-many-statements
  @classmethod
  def cmd_log(cls, argv, options):
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
    orm = sqltags.orm
    with orm.session() as session:
      for lineno, headline in enumerate(sys.stdin if use_stdin else (
          cmdline_headline,)):
        with Pfx(*(("%d: %s", lineno, headline) if use_stdin else (headline,))
                 ):
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
                  strptime_dt = datetime.strptime(
                      strptime_text, strptime_format
                  )
                except ValueError as e:
                  warning(
                      "cannot parse %r, using current time: %s", strptime_text,
                      e
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
          te = sqltags.add(
              None, session=session, unixtime=unixtime, tags=log_tags
          )
          te.set('headline', headline)
          if tag_categories:
            te.set('categories', list(tag_categories))
    return xit

  @staticmethod
  def cmd_ns(argv, options):
    ''' Usage: {cmd} entity-names...
          List entities and their tags.
    '''
    if not argv:
      raise GetoptError("missing entity_names")
    xit = 0
    sqltags = options.sqltags
    orm = sqltags.orm
    with orm.session() as session:
      for name in argv:
        with Pfx(name):
          try:
            index = int(name)
          except ValueError:
            index = name
          entity = sqltags.get(index, session=session)
          if entity is None:
            error("missing")
            xit = 1
            continue
          print(name)
          for tag in sorted(entity.tags(session=session)):
            print(" ", tag)
    return xit

  # pylint: disable=too-many-branches
  @classmethod
  def cmd_tag(cls, argv, options):
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
      tag_choices = cls.parse_tag_choices(argv)
    except ValueError as e:
      raise GetoptError(str(e)) from e
    if badopts:
      raise GetoptError("bad arguments")
    if name == '-':
      names = [line.rstrip('\n') for line in sys.stdin]
    else:
      names = [name]
    xit = 0
    sqltags = options.sqltags
    orm = sqltags.orm
    with orm.session():
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

SQLTagsCommand.add_usage_to_docstring()

# pylint: disable=too-many-instance-attributes
class SQLTagsORM(ORM, UNIXTimeMixin):
  ''' The ORM for an `SQLTags`.
  '''

  def __init__(self, *, db_url):
    super().__init__()
    db_path = cutprefix(db_url, 'sqlite://')
    if db_path is db_url:
      if db_url.startswith(('/', './', '../')) or '://' not in db_url:
        # turn filesystenm pathnames into SQLite db URLs
        db_path = abspath(db_url)
        db_url = 'sqlite:///' + db_url
      else:
        db_path = None
    self.db_url = db_url
    self.db_path = db_path
    self._lockfilepath = None
    engine = self.engine = create_engine(
        db_url,
        case_sensitive=True,
        echo=bool(os.environ.get('DEBUG')),  # 'debug'
    )
    meta = self.meta = self.Base.metadata
    meta.bind = engine
    self.declare_schema()
    self.Session = sessionmaker(bind=engine)
    if db_path is not None and not existspath(db_path):
      with Pfx("init %r", db_path):
        self.define_schema()
        verbose('created database')

  def startup(self):
    ''' Startup: define the tables if not present.
    '''
    if self.db_path:
      self._lockfilepath = makelockfile(self.db_path, poll_interval=0.2)

  def shutdown(self):
    ''' Stub shutdown.
    '''
    if self._lockfilepath is not None:
      with Pfx("remove(%r)", self._lockfilepath):
        os.remove(self._lockfilepath)
      self._lockfilepath = None

  @orm_method
  def define_schema(self):
    ''' Instantiate the schema and define the root metanode.
    '''
    self.meta.create_all()
    self.make_metanode()

  @property
  def metanode(self):
    ''' The metadata node, creating it if missing.
    '''
    return self.make_metanode()

  @orm_method
  @auto_session
  def make_metanode(self, *, session):
    ''' Return the metadata node, creating it if missing.
    '''
    entity = self.get_metanode(session=session)
    if entity is None:
      entity = self.entities(id=0, unixtime=time.time())
      entity.add_tag(
          'headline',
          "%s node 0: the metanode." % (type(self).__name__,),
          session=session
      )
      session.add(entity)
    return entity

  @orm_method
  @auto_session
  def get_metanode(self, *, session):
    ''' Return the metanode, the `Entities` row with `id`=`0`.
        Returns `None` if the node does not exist.

        Accessing the property `.metanode` always returns the metanode entity,
        creating it if necessary.
        Note that it is not associated with any session.
    '''
    return self.entities.lookup1(id=0, session=session)

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

      @auto_session
      def tag_rows(self, *, session):
        ''' Return a list of the `Tags` rows for this entity.
        '''
        return list(Tags.lookup(session=session, entity_id=self.id))

      @auto_session
      def tags(self, *, session):
        ''' Return an `SQLTagSet` with the `Tag`s for this entity.
        '''
        entity_tags = SQLTagSet(sqltags=None, entity_id=self.id)
        entity_tags.update(
            (
                (tagrow.name, tagrow.value)
                for tagrow in self.tag_rows(session=session)
            )
        )
        return entity_tags

      @auto_session
      def add_tag(self, name: str, value=None, *, session):
        ''' Add a tag for `(name,value)`,
            replacing any existing tag named `name`.
        '''
        tag = Tag(name, value)
        tags = orm.tags
        if self.id is None:
          # obtain the id value from the database
          session.add(self)
          session.flush()
        # TODO: upsert!
        etag = tags.lookup1(session=session, entity_id=self.id, name=tag.name)
        if etag is None:
          etag = tags(entity_id=self.id, name=tag.name)
          etag.value = tag.value
          session.add(etag)
        else:
          etag.value = value

      @auto_session
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

      @classmethod
      @pfx_method
      @auto_session
      def search(cls, criteria, *, session, mode='tagged'):
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
        entities = orm.entities
        query = session.query(entities)
        # first condition:
        #   select tags as alias where constraint
        # following:
        #   inner join tags as alias using entity_id where constraint
        # inner join entities on
        query = None
        sqlps = []
        for criterion in criteria:
          with Pfx(criterion):
            sqlp = criterion.sql_parameters(orm)
            if not query:
              query = session.query(sqlp.entity_id_column)
            else:
              previous = sqlps[-1]
              # join on the entity_id column
              query = query.join(
                  sqlp.alias,
                  sqlp.entity_id_column == previous.entity_id_column,
              )
            # apply conditions
            query = query.filter(sqlp.constraint)
            sqlps.append(sqlp)
        if query is None:
          query = session.query(entities.id)
        with Pfx("mode=%r", mode):
          if mode == 'id':
            pass
          elif mode == 'entity':
            query = session.query(
                entities.id, entities.unixtime, entities.name
            ).filter(entities.id.in_(query.distinct()))
          elif mode == 'tagged':
            tags = orm.tags
            query = session.query(
                entities.id, entities.unixtime, entities.name
            ).filter(entities.id.in_(query.distinct())).join(
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

    class Tags(Base, BasicTableMixin, HasIdMixin):
      ''' The table of tags associated with entities.
      '''

      __tablename__ = 'tags'
      entity_id = Column(
          Integer,
          ForeignKey("entities.id"),
          nullable=False,
          index=True,
          comment='entity id'
      )
      name = Column(String, comment='tag name', index=True)
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

      # one tag value per name, use structured_value for complex values
      UniqueConstraint('entity_id', 'name')

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
          new_value = self.datetime2unixtime(new_value)
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
            * `datetime`: `cls.datetime2unixtime(other_value)`
        '''
        if other_value is None:
          return None, None
        if isinstance(other_value, datetime):
          return cls.float_value, cls.datetime2unixtime(other_value)
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

class SQLTagSet(TagSet, SingletonMixin):
  ''' A singleton `TagSet` associated with a tagged entity.
  '''

  @staticmethod
  def _singleton_key(*, sqltags, entity_id, **_):
    return builtin_id(sqltags), entity_id

  def __init__(self, *a, sqltags, entity_id, **kw):
    try:
      pre_sqltags = self.sqltags
    except AttributeError:
      super().__init__(*a, **kw)
      self.sqltags = sqltags
      self.entity_id = entity_id
    else:
      assert pre_sqltags is sqltags

  @property
  def entity(self):
    ''' The `SQLTaggedEntity` associated with this `TagSet`.
    '''
    return self.sqltags[self.entity_id]

  @auto_session
  def set(self, tag_name, value=None, *, session, skip_db=False, **kw):
    ''' Add `tag_name`=`value` to this `TagSet`.
    '''
    if not skip_db:
      self.entity.add_db_tag(tag_name, value, session=session)
    super().set(tag_name, value=value, **kw)

  @auto_session
  def discard(self, tag_name, value=None, *, session, skip_db=False, **kw):
    ''' Discard `tag_name`=`value` from this `TagSet`.
    '''
    if not skip_db:
      self.entity.discard_db_tag(tag_name, value, session=session)
    super().discard(tag_name, value, **kw)

class SQLTaggedEntity(TaggedEntity, SingletonMixin):
  ''' A singleton `TaggedEntity` attached to an `SQLTags` instance.
  '''

  @staticmethod
  # pylint: disable=redefined-builtin
  def _singleton_key(*, sqltags, id, **_):
    return builtin_id(sqltags), id

  def __init__(self, *, name=None, sqltags, **kw):
    try:
      pre_sqltags = self.sqltags
    except AttributeError:
      self._name = name
      super().__init__(name=name, **kw)
      self.sqltags = sqltags
    else:
      assert pre_sqltags is sqltags

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
      e = self.sqltags.db_entity(self.id)
      e.name = new_name
      self._name = new_name

  @property
  def db_entity(self):
    ''' The database `Entities` instance for this `SQLTaggedEntity`.
    '''
    return self.sqltags.db_entity(self.id)

  @auto_session
  @pfx_method
  def add_db_tag(self, tag_name, value=None, *, session):
    ''' Add a tag to the database.
    '''
    e = self.sqltags.db_entity(self.id)
    return e.add_tag(tag_name, value, session=session)

  @auto_session
  def discard_db_tag(self, tag_name, value=None, *, session):
    ''' Discard a tag from the database.
    '''
    return self.sqltags.db_entity(self.id).discard_tag(
        tag_name, value, session=session
    )

class SQLTags(MultiOpenMixin):
  ''' A class to embodying an database and its entities and tags.
  '''

  TaggedEntityClass = SQLTaggedEntity

  def __init__(self, db_url=None):
    if not db_url:
      db_url = self.infer_db_url()
    self.db_url = db_url
    self.orm = None
    self._lock = RLock()

  def __str__(self):
    return "%s(db_url=%r)" % (type(self).__name__, self.db_url)

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

  def startup(self):
    ''' Stub for startup: prepare the ORM.
    '''
    self.orm = SQLTagsORM(db_url=self.db_url)
    self._pre_startup_attrs = pushattrs(sqla_state, orm=self.orm)

  def shutdown(self):
    ''' Stub for shutdown.
    '''
    popattrs(sqla_state, 'orm', self._pre_startup_attrs)
    self._pre_startup_attrs = None
    self.orm = None

  def init(self):
    ''' Initialise the database.
    '''
    self.orm.define_schema()

  @orm_auto_session
  def db_entity(self, index, *, session):
    ''' Return the `Entities` instance for `index` or `None`.
    '''
    entities = self.orm.entities
    if isinstance(index, int):
      return entities.lookup1(id=index, session=session)
    if isinstance(index, str):
      return entities.lookup1(name=index, session=session)
    raise TypeError(
        "expected index to be int or str, got %s:%s" % (type(index), index)
    )

  @orm_auto_session
  def add(self, name: [str, None], *, session, unixtime=None, tags=None):
    ''' Add a new `SQLTaggedEntity` named `name` (`None` for "log" entries)
        with `unixtime` (default `time.time()`
        and the supplied `tags` (optional iterable of `Tag`s).
        Return the new `SQLTaggedEntity`.
    '''
    if unixtime is None:
      unixtime = time.time()
    if tags is None:
      tags = ()
    entity = self.orm.entities(name=name, unixtime=unixtime)
    for tag in tags:
      entity.add_tag(tag.name, tag.value, session=session)
    session.add(entity)
    session.flush()
    te = self.get(entity.id, session=session)
    for tag in tags:
      te.set(tag.name, tag.value)
    return te

  @orm_auto_session
  @typechecked
  def make(self, name: str, *, session, unixtime=None):
    ''' Fetch or create an `SQLTagged
    '''
    te = None if name is None else self.get(name)
    if te is None:
      te = self.add(name, session=session, unixtime=unixtime)
    return te

  @orm_auto_session
  def get(self, index, default=None, *, session, cls=None):
    ''' Return an `SQLTaggedEntity` matching `index`, or `None` if there is no such entity.
    '''
    if isinstance(index, int):
      tes = self.find([SQTEntityIdTest([index])], session=session, cls=cls)
    elif isinstance(index, str):
      tes = self.find(
          [SQLTagBasedTest(index, True, Tag('name', index), '=')], cls=cls
      )
    else:
      raise TypeError("unsupported index: %s:%r" % (type(index), index))
    tes = list(tes)
    if not tes:
      return default
    te, = tes
    return te

  @locked
  @orm_auto_session
  def __getitem__(self, index, *, session, cls=None):
    ''' Return an `SQLTaggedEntity` for `index` (an `int` or `str`).
    '''
    te = self.get(index, session=session, cls=cls)
    if te is None:
      if isinstance(index, int):
        raise IndexError("%s[%r]" % (self, index))
      raise KeyError("%s[%r]" % (self, index))
    return te

  def __contains__(self, index):
    return self.get(index) is not None

  @orm_auto_session
  def find(self, criteria, *, session, cls=None):
    ''' Generate and run a query derived from `criteria`
        yielding `SQLTaggedEntity` instances.
    '''
    if cls is None:
      cls = self.TaggedEntityClass
    orm = self.orm
    query = orm.entities.search(
        criteria,
        session=session,
        mode='tagged',
    )
    # merge entities and tag information
    tags = self.orm.tags
    entity_map = {}
    for row in query:
      entity_id = row.id
      te = entity_map.get(entity_id)
      if not te:
        # not seen before
        te = entity_map[entity_id] = cls(
            id=entity_id,
            name=row.name,
            unixtime=row.unixtime,
            tags=SQLTagSet(sqltags=self, entity_id=entity_id),
            sqltags=self
        )
      # a None tag_name means no tags
      if row.tag_name is not None:
        # set the dict entry directly - we are loading db values,
        # not applying them to the db
        tag_value = tags.pick_value(
            row.tag_float_value, row.tag_string_value, row.tag_structured_value
        )
        te.tags.set(row.tag_name, tag_value, skip_db=True)
    for te in entity_map.values():
      if all(criterion.match_tagged_entity(te) for criterion in criteria):
        yield te

  @orm_auto_session
  def import_csv_file(self, f, *, session, update_mode=False):
    ''' Import CSV data from the file `f`.

        If `update_mode` is true
        named records which already exist will update from the data,
        otherwise the conflict will raise a `ValueError`.
    '''
    csvr = csv.reader(f)
    for csvrow in csvr:
      with Pfx(csvr.line_num):
        te = TaggedEntity.from_csvrow(csvrow)
        self.add_tagged_entity(te, session=session, update_mode=update_mode)

  @orm_auto_session
  def add_tagged_entity(self, te, *, session, update_mode=False):
    ''' Add the `TaggedEntity` `te`.

        If `update_mode` is true
        named records which already exist will update from `te`,
        otherwise the conflict will raise a `ValueError`.
    '''
    e = self[te.name
             ] if te.name else self[te.id] if te.id is not None else None
    if e and not update_mode:
      raise ValueError("entity named %r already exists" % (te.name,))
    if e is None:
      e = self.orm.entities(name=te.name or None, unixtime=te.unixtime)
      session.add(e)
    for tag in te.tags:
      with Pfx(tag):
        e.add_tag(tag, session=session)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
