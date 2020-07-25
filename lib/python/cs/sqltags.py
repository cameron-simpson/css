#!/usr/bin/env python3

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

from collections import namedtuple
from configparser import ConfigParser
from contextlib import contextmanager
import csv
from datetime import datetime
from getopt import getopt, GetoptError
import os
from os.path import abspath, basename, expanduser, exists as existspath
import re
import sys
import threading
from threading import RLock
import time
from icontract import require
from sqlalchemy import (
    create_engine, event, Index, Column, Integer, Float, String, JSON,
    ForeignKey
)
from sqlalchemy.sql import select
from sqlalchemy.sql.expression import or_
from sqlalchemy.orm import sessionmaker, aliased
import sqlalchemy.sql.functions as func
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.dateutils import UNIXTimeMixin, datetime2unixtime, unixtime2datetime
from cs.edit import edit_strings
from cs.fileutils import makelockfile
from cs.lex import FormatableMixin, FormatAsError, cutprefix
from cs.logutils import error, warning, ifverbose, info
from cs.pfx import Pfx, pfx_method, XP
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM, orm_method, auto_session, orm_auto_session, BasicTableMixin,
    HasIdMixin
)
from cs.tagset import TagSet, Tag, TagChoice, TagsCommandMixin, TaggedEntity
from cs.threads import locked
from cs.upd import Upd

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': ['sqltags = cs.sqltags:main'],
    },
    'install_requires':
    ['cs.logutils', 'cs.pfx', 'cs.tagset', 'cs.upd', 'icontract'],
}

# regexp for "word[,word...]:", the leading prefix for categories
# if not specified by the -c command line option
CATEGORIES_PREFIX_re = re.compile(
    r'(?P<categories>[a-z]\w*(,[a-z]\w*)*):\s*', re.I
)

DBURL_ENVVAR = 'SQLTAGS_DBURL'
DBURL_DEFAULT = '~/var/sqltags.sqlite'

FIND_OUTPUT_FORMAT_DEFAULT = '{entity.isotime} {headline} {tags}'

def main(argv=None):
  ''' Command line mode.
  '''
  return SQLTagsCommand().run(argv)

class _State(threading.local):
  ''' Per-thread default context stack.
  '''

  def __init__(self, **kw):
    threading.local.__init__(self)
    for k, v in kw.items():
      setattr(self, k, v)

state = _State(verbose=sys.stderr.isatty())

def verbose(msg, *a):
  ''' Emit message if in verbose mode.
  '''
  ifverbose(state.verbose, msg, *a)

class SQLTagsCommand(BaseCommand, TagsCommandMixin):
  ''' `sqltags` main command line utility.
  '''

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
    db_url = os.environ.get(DBURL_ENVVAR)
    if db_url is None:
      db_url = expanduser(DBURL_DEFAULT)
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
    with stackattrs(options, sqltags=sqltags):
      with sqltags:
        yield

  @classmethod
  def cmd_export(cls, argv, options):
    ''' Usage: {cmd} {{tag[=value]|-tag}}...
          Export entities matching all the constraints.
          The output format is CSV data with the following columns:
          * unixtime: the entity unixtime, a float
          * id: the entity database row id, an integer
          * name: the entity name
          * tags: a column per `Tag`
    '''
    sqltags = options.sqltags
    badopts = False
    try:
      tag_choices = cls.parse_tag_choices(argv)
    except ValueError as e:
      warning("bad tag specifications: %s", e)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    csvw = csv.writer(sys.stdout)
    with sqltags.orm.session() as session:
      for te in sqltags.find(tag_choices, session=session):
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
          output_format = sqltags.resolve_format_string(value)
        else:
          raise RuntimeError("unsupported option")
    try:
      tag_choices = cls.parse_tag_choices(argv)
    except ValueError as e:
      warning("bad tag specifications: %s", e)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    with sqltags.orm.session() as session:
      for te in sqltags.find(tag_choices, session=session):
        with Pfx(te):
          try:
            output = te.format_as(output_format, error_sep='\n  ')
          except FormatAsError as e:
            error(str(e))
            xit = 1
            continue
          print(output.replace('\n', ' '))
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
    for option, value in opts:
      with Pfx(option):
        if option == '-u' or option == '--update':
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
    options.sqltags.orm.define_schema()

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
    log_tags = cls.parse_tag_choices(argv)
    for log_tag in log_tags:
      with Pfx(log_tag):
        if not log_tag.choice:
          warning("negative tag choice")
          badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    xit = 0
    sqltags = options.sqltags
    orm = sqltags.orm
    with orm.session() as session:
      for lineno, headline in enumerate(sys.stdin if cmdline_headline ==
                                        '-' else (cmdline_headline,)):
        with Pfx(lineno):
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
          entity = orm.entities(unixtime=unixtime)
          for log_tag in log_tags:
            entity.add_tag(log_tag.tag, session=session)
          entity.add_tag('headline', headline, session=session)
          if tag_categories:
            entity.add_tag('categories', list(tag_categories), session=session)
          session.add(entity)
          session.flush()
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
      warning("bad tag specifications: %s", e)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    if name == '-':
      names = [line.rstrip('\n') for line in sys.stdin]
    else:
      names = [name]
    xit = 0
    sqltags = options.sqltags
    orm = sqltags.orm
    with orm.session() as session:
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
                  entity.add_tag(tag_choice.tag, session=session)
              else:
                if tag_choice.tag in tags:
                  entity.discard_tag(tag_choice.tag, session=session)
    return xit

SQLTagsCommand.add_usage_to_docstring()

class SQLTagsORM(ORM, UNIXTimeMixin):
  ''' The ORM for an `SQLTags`.
  '''

  def __init__(self, *, db_url):
    super().__init__()
    db_path = cutprefix(db_url, 'sqlite://')
    if db_path is db_url:
      if db_url.startswith(('/', './', '../')) or '://' not in db_url:
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
        ''' Return a `TagSet` withg the `Tag`s for this entity.

            *Note*: this is a copy. Modifying this `TagSet`
            will not affect the database tags.
        '''
        entity_tags = TagSet()
        entity_tags.update(
            (
                (tagrow.name, tagrow.value)
                for tagrow in self.tag_rows(session=session)
            )
        )
        return entity_tags

      @auto_session
      def add_tag(self, name, value=None, *, session):
        ''' Add a tag for `(name,value)`,
            replacing any existing tag named `name`.
        '''
        tag = Tag(name, value)
        tags = orm.tags
        if self.id is None:
          # obtain the id value from the database
          session.add(self)
          session.flush()
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
      def by_name(cls, name, *, session, query=None):
        ''' Return a query to select `Entity` rows by `name`.

            If `name` is `None`, log entities are returned (`name IS NULL`),
            otherwise the entity with the specific name will be matched.
        '''
        entities = orm.entities
        if query is None:
          query = session.query(entities)
        query = query.filter_by(name=name)
        return query

      @classmethod
      @pfx_method
      @auto_session
      def by_tags(
          cls,
          tag_criteria,
          *,
          session,
          with_tags=False,
          name=None,
          query=None
      ):
        ''' Construct a query to match `Entity` rows
            matching `name` and the supplied `tag_criteria`.

            If `name` is omitted or `None` the query will match log entities
            otherwise the entity with the specified `name`.

            An existing query may be supplied,
            in which case it will be extended.

            The `tag_criteria` should be an iterable
            yielding any of the following types:
            * `TagChoice`: used as a positive or negative test
            * `Tag`: an object with a `.name` and `.value`
              is equivalent to a positive `TagChoice`
            * `(name,value)`: a 2 element sequence
              is equivalent to a positive `TagChoice`
            * `str`: a string tests for the presence
              of a tag with that name;
              if the string commences with a `'-'` (minus)
              a negative test is made
        '''
        entities = orm.entities
        tags = orm.tags
        if query is None:
          query = cls.by_name(name=name, session=session)
        elif name is not None:
          raise ValueError("cannot supply both a query and a name")
        for taggy in tag_criteria:
          with Pfx(taggy):
            if isinstance(taggy, str):
              tag_choice = TagChoice.from_str(taggy)
            elif hasattr(taggy, 'choice'):
              tag_choice = taggy
            elif hasattr(taggy, 'name'):
              tag_choice = TagChoice(None, True, taggy)
            else:
              name, value = taggy
              tag_choice = TagChoice(None, True, Tag(name, value))
            choice = tag_choice.choice
            tag = tag_choice.tag
            tags_alias = aliased(tags)
            tag_column, tag_test_value = tags_alias.value_test(tag.value)
            match = tags_alias.name == tag.name,
            isouter = False
            if choice:
              # positive test
              if tag_test_value is None:
                # just test for presence
                pass
              else:
                # test for presence and value
                match = *match, tag_column == tag_test_value
            else:
              # negative test
              isouter = True
              if tag_column is None:
                # just test for absence
                match = *match, tags_alias.id is None
              else:
                # test for absence or incorrect value
                match = *match, or_(
                    tags_alias.id is None, tag_column != tag_test_value
                )
            query = query.join(tags_alias, isouter=isouter).filter(*match)
        return query

      @classmethod
      @pfx_method
      def with_tags(cls, query):
        ''' Extend `query` to `RIGHT JOIN` against the `Tags`,
            adding `(tag_name,float_value,string_value,structured_value)`
            to the columns returned.
        '''
        entities = orm.entities
        tags = orm.tags
        tags_alias = aliased(tags)
        query = query.join(
            tags_alias, isouter=True
        ).filter(entities.id is not None).add_columns(
            tags_alias.name, tags_alias.float_value, tags_alias.string_value,
            tags_alias.structured_value
        )
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
        return float_value

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
        ''' Return `(column,test_value)` for testing `other_value`
            where `column` if the appropriate SQLAlchemy column
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

class SQLTags(MultiOpenMixin):
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, db_url):
    MultiOpenMixin.__init__(self)
    self.db_url = db_url
    self.orm = None
    self._lock = RLock()

  def __str__(self):
    return "%s(db_url=%r)" % (type(self).__name__, self.db_url)

  def startup(self):
    ''' Stub for startup: prepare the ORM.
    '''
    self.orm = SQLTagsORM(db_url=self.db_url)

  def shutdown(self):
    ''' Stub for shutdown.
    '''
    self.orm = None

  @locked
  @orm_auto_session
  def __getitem__(self, index, *, session):
    ''' Return a `TaggedEntity` for `index` (an `int` or `str`).
    '''
    te = self.get(index, session=session)
    if te is None:
      if isinstance(index, int):
        raise IndexError("%s[%r]" % (self, index))
      raise KeyError("%s[%r]" % (self, index))
    return te

  @orm_auto_session
  def get(self, index, *, session):
    ''' Return a `TaggedEntity` matching `index`, or `None` if there is no such entity.
    '''
    if not isinstance(index, (int, str)):
      raise TypeError(
          "%s.get: index must be int or str, not %s",
          type(self).__name__, type(index)
      )
    entities = self.orm.entities
    if isinstance(index, int):
      query = entities.query(session).filter_by(id=index)
    else:
      query = entities.by_name(index)
    matches = list(self._run_query(query, session=session))
    if not matches:
      return None
    match, = matches
    return match

  @orm_auto_session
  def _run_query(self, query, *, session, without_tags=False):
    ''' Run a query derived from `self.orm.entities.query(session)`
        yielding `TaggedEntity` instances.

        If the optional `without_tags` parameter (default `False`)
        is true, this function does not JOIN against the `tags` table,
        resulting in empty `.tags` `TagSet`s
        in the resulting `TaggedEntity` instances.
    '''
    entities = self.orm.entities
    if without_tags:
      # do not bother fetching tags
      results = session.execute(query)
      for entity_id, entity_name, unixtime in results:
        yield TaggedEntity(
            id=entity_id, name=entity_name, unixtime=unixtime, tags=TagSet()
        )
      return
    # entities and tag information which must be merged
    query = entities.with_tags(query)
    results = session.execute(query)
    entity_map = {}
    for (entity_id, entity_name, unixtime, tag_name, tag_float_value,
         tag_string_value, tag_structured_value) in results:
      e = entity_map.get(entity_id)
      if not e:
        e = entity_map[entity_id] = TaggedEntity(
            id=entity_id, name=entity_name, unixtime=unixtime, tags=TagSet()
        )
      if tag_name is not None:
        value = self.orm.tags.pick_value(
            tag_float_value, tag_string_value, tag_structured_value
        )
        e.tags.add(tag_name, value)
    yield from entity_map.values()

  @orm_auto_session
  def find(self, tag_choices, *, session):
    ''' Generator yielding `TaggedEntity` instances
        for the the `Entity` rows matching `tag_choices`.
    '''
    entities = self.orm.entities
    query = entities.by_tags(tag_choices, session=session)
    yield from self._run_query(query, session=session)

  @orm_auto_session
  def import_csv_file(self, f, *, session, update_mode=False):
    ''' Import CSV data from the file `f`.

        If `update_mode` is true
        named records which already exist will update from the data,
        otherwise the conflict will raise a `ValueError`.
    '''
    csvr = csv.reader(f)
    orm = self.orm
    for csvrow in csvr:
      with Pfx(csvr.line_num):
        te = TaggedEntity.from_csvrow(csvrow)
        self.add_tagged_entity(te)

  @orm_auto_session
  def add_tagged_entity(self, te, *, session, update_mode=False):
    ''' Add the `TaggedEntity` `te`.

        If `update_mode` is true
        named records which already exist will update from `te`,
        otherwise the conflict will raise a `ValueError`.
    '''
    e = self[te.name] if te.name else None
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
