#!/usr/bin/python
#
# Command line stuff. - Cameron Simpson <cs@cskk.id.au> 03sep2015
#
# pylint: disable=too-many-lines

''' Convenience functions for working with the Cmd module,
    the BaseCommand class for constructing command line programmes,
    and other command line related stuff.
'''

from __future__ import print_function, absolute_import
from collections import namedtuple
from contextlib import contextmanager
from getopt import getopt, GetoptError
from inspect import isclass, ismethod
from os.path import basename
from signal import SIGINT, SIGTERM
import sys
from types import SimpleNamespace
from typing import List

from cs.context import stackattrs
from cs.gimmicks import nullcontext
from cs.lex import (
    cutprefix,
    cutsuffix,
    format_escape,
    is_identifier,
    r,
    stripped_dedent,
)
from cs.logutils import setup_logging, warning, exception
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.doc import obj_docstring
from cs.resources import RunState

__version__ = '20220429-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.gimmicks',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.doc',
        'cs.resources',
    ],
}

def docmd(dofunc):
  ''' Decorator for `cmd.Cmd` subclass methods
      to supply some basic quality of service.

      This decorator:
      - wraps the function call in a `cs.pfx.Pfx` for context
      - intercepts `getopt.GetoptError`s, issues a `warning`
        and runs `self.do_help` with the method name,
        then returns `None`
      - intercepts other `Exception`s,
        issues an `exception` log message
        and returns `None`

      The intended use is to decorate `cmd.Cmd` `do_`* methods:

          from cmd import Cmd
          from cs.cmdutils import docmd
          ...
          class MyCmd(Cmd):
              @docmd
              def do_something(...):
                  ... do something ...
  '''
  funcname = dofunc.__name__

  def docmd_wrapper(self, *a, **kw):
    ''' Run a `Cmd` "do" method with some context and handling.
    '''
    if not funcname.startswith('do_'):
      raise ValueError("function does not start with 'do_': %s" % (funcname,))
    argv0 = funcname[3:]
    with Pfx(argv0):
      try:
        return dofunc(self, *a, **kw)
      except GetoptError as e:
        warning("%s", e)
        self.do_help(argv0)
        return None
      except Exception as e:  # pylint: disable=broad-except
        exception("%s", e)
        return None

  docmd_wrapper.__name__ = '@docmd(%s)' % (funcname,)
  docmd_wrapper.__doc__ = dofunc.__doc__
  return docmd_wrapper

class _BaseSubCommand:

  def __init__(self, cmd, method, *, usage_mapping=None):
    self.cmd = cmd
    self.method = method
    self.usage_mapping = usage_mapping or {}

  def __str__(self):
    return "%s(cmd=%r,method=%s,..)" % (
        type(self).__name__, self.cmd, self.method
    )

  @staticmethod
  def from_class(command_cls):
    ''' Return a mapping of subcommand names to subcommand specifications
        for class attributes which commence with
        `command_cls.SUBCOMMAND_METHOD_PREFIX`,
        by default `'cmd_'`.
    '''
    prefix = command_cls.SUBCOMMAND_METHOD_PREFIX
    subcommands_map = {}
    for attr in dir(command_cls):
      if attr.startswith(prefix):
        subcmd = cutprefix(attr, prefix)
        method = getattr(command_cls, attr)
        subcommands_map[subcmd] = (
            _ClassSubCommand(
                subcmd,
                method,
                usage_mapping=dict(getattr(method, 'USAGE_KEYWORDS', ()))
            ) if isclass(method) else _MethodSubCommand(
                subcmd,
                method,
                usage_mapping=dict(getattr(command_cls, 'USAGE_KEYWORDS', ()))
            )
        )
    return subcommands_map

  def usage_text(self, short, usage_format_mapping=None):
    ''' Return the filled out usage text for this subcommand.
    '''
    usage_format_mapping = usage_format_mapping or {}
    subusage_format = self.usage_format()  # pylint: disable=no-member
    if subusage_format:
      if short:
        subusage_format, *_ = subusage_format.split('\n', 1)
      mapping = {
          k: v
          for k, v in sys.modules[self.method.__module__].__dict__.items()
          if k and not k.startswith('_')
      }
      if usage_format_mapping:
        mapping.update(usage_format_mapping)
      if self.usage_mapping:
        mapping.update(self.usage_mapping)
      mapping.update(cmd=self.cmd)
      with Pfx("format %r using %r", subusage_format, mapping):
        subusage = subusage_format.format_map(mapping)
    return subusage or None

class _MethodSubCommand(_BaseSubCommand):
  ''' A class to represent a subcommand implemented with a method.
  '''

  def __call__(self, subcmd, command, argv):
    with Pfx(subcmd):
      method = self.method
      if ismethod(method):
        # already bound
        return method(argv)
      # unbound - supply the instance
      return method(command, argv)

  def usage_format(self):
    ''' Return the usage format string from the method docstring.
    '''
    doc = obj_docstring(self.method)
    if doc:
      if 'Usage:' in doc:
        # extract the Usage: paragraph
        pre_usage, post_usage = doc.split('Usage:', 1)
        pre_usage = pre_usage.strip()
        post_usage_format, *_ = post_usage.split('\n\n', 1)
        subusage_format = stripped_dedent(post_usage_format)
      else:
        # extract the first paragraph
        lines = ['{cmd} ...']
        doc_p1 = stripped_dedent(doc.split('\n\n', 1)[0])
        if doc_p1:
          lines.extend(map(format_escape, doc_p1.split('\n')))
        subusage_format = "\n  ".join(lines)
    else:
      # default usage text
      subusage_format = '{cmd} ...'
    return subusage_format

class _ClassSubCommand(_BaseSubCommand):
  ''' A class to represent a subcommand implemented with a `BaseCommand` subclass.
  '''

  def __call__(self, cmd, command, argv):
    return self.method(argv, cmd=cmd, **command.options.__dict__).run()

  def usage_format(self):
    ''' Return the usage format string from the class.
    '''
    doc = self.method.usage_text(cmd=self.cmd)
    subusage_format, *_ = cutprefix(doc, 'Usage:').lstrip().split("\n\n", 1)
    return subusage_format

# pylint: disable=too-few-public-methods
class BaseCommandOptions(SimpleNamespace):
  ''' A base class for the `BaseCommand` `options` object.

      This is the default class for the `self.options` object
      available during `BaseCommand.run()`.

      It comes prefilled with:
      * `.dry_run=False`
      * `.force=False`
      * `.quiet=False`
      * `.verbose=False`
      and a `.doit` property which is the inverse of `.dry_run`.
  '''

  def __init__(self, **kw):
    kw.setdefault('dry_run', False)
    kw.setdefault('force', False)
    kw.setdefault('quiet', False)
    kw.setdefault('verbose', False)
    super().__init__(**kw)

  @property
  def doit(self):
    ''' I usually use a `doit` flag,
        the inverse of `dry_run`.
    '''
    return not self.dry_run

  @doit.setter
  def doit(self, new_doit):
    ''' Set `dry_run` to the inverse of `new_doit`.
    '''
    self.dry_run = not new_doit

  def popopts(self, argv, **opt_specs):
    ''' Convenience method to appply `BaseCommand.popopts` to the options.
    '''
    return BaseCommand.popopts(argv, self, **opt_specs)

class BaseCommand:
  ''' A base class for handling nestable command lines.

      This class provides the basic parse and dispatch mechanisms
      for command lines.
      To implement a command line
      one instantiates a subclass of `BaseCommand`:

          class MyCommand(BaseCommand):
              GETOPT_SPEC = 'ab:c'
              USAGE_FORMAT = r"""Usage: {cmd} [-a] [-b bvalue] [-c] [--] arguments...
                -a    Do it all.
                -b    But using bvalue.
                -c    The 'c' option!
              """
              ...

      and provides either a `main` method if the command has no subcommands
      or a suite of `cmd_`*subcommand* methods, one per subcommand.

      Running a command is done by:

          MyCommand(argv).run()

      Modules which implement a command line mode generally look like this:

          ... imports etc ...
          def main(argv=None):
              """ The command line mode.
              """
              return MyCommand(argv).run()
          ... other code ...
          class MyCommand(BaseCommand):
          ... other code ...
          if __name__ == '__main__':
              sys.exit(main(sys.argv))

      Instances have a `self.options` attribute on which optional
      modes are set,
      avoiding conflict with the attributes of `self`.

      Subclasses with no subcommands
      generally just implement a `main(argv)` method.

      Subclasses with subcommands
      should implement a `cmd_`*subcommand*`(argv)` instance method
      for each subcommand.
      If a subcommand is itself implemented using `BaseCommand`
      then it can be a simple attribute:

          cmd_subthing = SubThingCommand

      Returning to methods, if there is a paragraph in the method docstring
      commencing with `Usage:`
      then that paragraph is incorporated automatically
      into the main usage message.
      Example:

          def cmd_ls(self, argv):
              """ Usage: {cmd} [paths...]
                    Emit a listing for the named paths.

                  Further docstring non-usage information here.
              """
              ... do the "ls" subcommand ...

      The subclass is customised by overriding the following methods:
      * `apply_defaults()`:
        prepare the initial state of `self.options`
        before any command line options are applied.
      * `apply_opt(opt,val)`:
        apply an individual getopt global command line option
        to `self.options`.
      * `apply_opts(opts)`:
        apply the `opts` to `self.options`.
        `opts` is an `(option,value)` sequence
        as returned by `getopot.getopt`.
        The default implementation iterates over these and calls `apply_opt`.
      * `cmd_`*subcmd*`(argv)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then the method `cmd_`*subcmd*`(subcmd_argv)`
        will be called where `subcmd_argv` contains the command line arguments
        following *subcmd*.
      * `main(argv)`:
        if there are no command line arguments after the options
        or the first argument does not have a corresponding
        `cmd_`*subcmd* method
        then method `main(argv)`
        will be called where `argv` contains the command line arguments.
      * `run_context()`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively,
        such as to open and close a database.

      Editorial: why not arparse?
      Primarily because when incorrectly invoked
      an argparse command line prints the help/usage messgae
      and aborts the whole programme with `SystemExit`.
  '''

  SUBCOMMAND_METHOD_PREFIX = 'cmd_'
  GETOPT_SPEC = ''
  SUBCOMMAND_ARGV_DEFAULT = None
  OPTIONS_CLASS = BaseCommandOptions

  def __init_subclass__(cls):
    ''' Update subclasses of `BaseCommand`.

        Appends the usage message to the class docstring.
    '''
    usage_message = cls.usage_text()
    usage_doc = (
        'Command line usage:\n\n    ' + usage_message.replace('\n', '\n    ')
    )
    cls_doc = obj_docstring(cls)
    cls_doc = cls_doc + '\n\n' + usage_doc if cls_doc else usage_doc
    cls.__doc__ = cls_doc

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def __init__(self, argv=None, *, cmd=None, options=None, **kw_options):
    ''' Initialise the command line.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          optional command line arguments
          including the main command name if `cmd` is not specified.
          The default is `sys.argv`.
          The contents of `argv` are copied,
          permitting desctructive parsing of `argv`.
        * `cmd`:
          optional keyword specifying the command name for context;
          if this is not specified it is taken from `argv.pop(0)`.
        * `options`:
          an optional keyword providing object for command state and context.
          If not specified a new `self.OPTIONS_CLASS` instance
          is allocated for use as `options`.
          The default `OPTIONS_CLASS` is `BaseCommandOptions`,
          a `SimpleNamespace` with some prefilled attributes and properties
          to aid use later.
          These can be further updated by the `.apply_default()` method.
        Other keyword arguments are applied to `self.options`
        as attributes.

        The command line arguments are parsed according to
        the optional `GETOPT_SPEC` class attribute (default `''`).
        If `getopt_spec` is not empty
        then `apply_opts(opts)` is called
        to apply the supplied options to the state
        where `opts` is the return from `getopt.getopt(argv,getopt_spec)`.

        After the option parse,
        if the first command line argument *foo*
        has a corresponding method `cmd_`*foo*
        then that argument is removed from the start of `argv`
        and `self.cmd_`*foo*`(argv,options,cmd=`*foo*`)` is called
        and its value returned.
        Otherwise `self.main(argv,options)` is called
        and its value returned.

        If the command implementation requires some setup or teardown
        then this may be provided by the `run_context`
        context manager method,
        called with `cmd=`*subcmd* for subcommands
        and with `cmd=None` for `main`.
    '''
    subcmds = self.subcommands()
    has_subcmds = subcmds and list(subcmds) != ['help']
    options = self.options = self.OPTIONS_CLASS()
    options.runstate_signals = (SIGINT, SIGTERM)
    if argv is None:
      argv = list(sys.argv)
      if cmd is not None:
        # consume the first argument anyway
        argv.pop(0)
    else:
      argv = list(argv)
    if cmd is None:
      cmd = basename(argv.pop(0))
    log_level = getattr(options, 'log_level', None)
    loginfo = setup_logging(cmd, level=log_level)
    # post: argv is list of arguments after the command name
    self.cmd = cmd
    self.loginfo = loginfo
    self.apply_defaults()
    # override the default options
    for option, value in kw_options.items():
      setattr(options, option, value)
    self._argv = argv
    self._run = lambda subcmd, command, argv: 2
    self._subcmd = None
    self._printed_usage = False
    # we catch GetoptError from this suite...
    subcmd = None  # default: no subcmd specific usage available
    short_usage = False
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # catch bare -h or --help if no 'h' in the getopt_spec
      if ('h' not in getopt_spec and len(argv) == 1
          and argv[0] in ('-h', '-help', '--help')):
        argv = ['help']
      else:
        # we do this regardless in order to honour '--'
        try:
          opts, argv = getopt(argv, getopt_spec, '')
        except GetoptError:
          short_usage = True
          raise
        self.apply_opts(opts)
        # we do this regardless so that subclasses can do some presubcommand parsing
        # after any command line options
        argv = self._argv = self.apply_preargv(argv)

      # now prepare self._run, a callable
      if not has_subcmds:
        # no subcommands, just use the main() method
        try:
          main = self.main
        except AttributeError:
          # pylint: disable=raise-missing-from
          raise GetoptError("no main method and no subcommand methods")
        self._run = _MethodSubCommand(None, main)
      else:
        # expect a subcommand on the command line
        if not argv:
          default_argv = getattr(self, 'SUBCOMMAND_ARGV_DEFAULT', None)
          if not default_argv:
            short_usage = True
            raise GetoptError(
                "missing subcommand, expected one of: %s" %
                (', '.join(sorted(subcmds.keys())),)
            )
          argv = (
              [default_argv]
              if isinstance(default_argv, str) else list(default_argv)
          )
        subcmd = argv.pop(0)
        subcmd_ = subcmd.replace('-', '_').replace('.', '_')
        try:
          subcommand = subcmds[subcmd_]
        except KeyError:
          # pylint: disable=raise-missing-from
          short_usage = True
          bad_subcmd = subcmd
          subcmd = None
          raise GetoptError(
              "%s: unrecognised subcommand, expected one of: %s" % (
                  bad_subcmd,
                  ', '.join(sorted(subcmds.keys())),
              )
          )
        self._run = subcommand
      self._subcmd = subcmd
    except GetoptError as e:
      if self.getopt_error_handler(cmd, self.options, e,
                                   self.usage_text(subcmd=subcmd,
                                                   short=short_usage)):
        self._printed_usage = True
        return
      raise

  @classmethod
  def subcommands(cls):
    ''' Return a mapping of subcommand names to subcommand specifications
        for class attributes which commence with `cls.SUBCOMMAND_METHOD_PREFIX`
        by default `'cmd_'`.
    '''
    try:
      subcmds = cls.__dict__['_subcommands']
    except KeyError:
      subcmds = cls._subcommands = _BaseSubCommand.from_class(cls)
    return subcmds

  @classmethod
  def usage_text(
      cls, *, cmd=None, format_mapping=None, subcmd=None, short=False
  ):
    ''' Compute the "Usage:" message for this class
        from the top level `USAGE_FORMAT`
        and the `'Usage:'`-containing docstrings
        from its `cmd_*` methods.

        Parameters:
        * `cmd`: optional command name, default derived from the class name
        * `format_mapping`: an optional format mapping for filling
          in format strings in the usage text
        * `subcmd`: constrain the usage to a particular subcommand named `subcmd`;
          this is used to produce a shorter usage for subcommand usage failures
    '''
    if cmd is None:
      cmd = cutsuffix(cls.__name__, 'Command').lower()
    if format_mapping is None:
      format_mapping = {}
    format_mapping.setdefault('cmd', cmd)
    subcmds = cls.subcommands()
    has_subcmds = subcmds and list(subcmds) != ['help']
    usage_format_mapping = dict(getattr(cls, 'USAGE_KEYWORDS', {}))
    usage_format_mapping.update(format_mapping)
    usage_format = getattr(
        cls, 'USAGE_FORMAT', (
            r'Usage: {cmd} subcommand [...]'
            if has_subcmds else 'Usage: {cmd} [...]'
        )
    )
    usage_message = usage_format.format_map(usage_format_mapping)
    if subcmd:
      if not has_subcmds:
        raise ValueError("subcmd=%r: no subcommands!" % (subcmd,))
      subcmd_ = subcmd.replace('-', '_').replace('.', '_')
      try:
        subcmds[subcmd_]
      except KeyError:
        # pylint: disable=raise-missing-from
        raise ValueError(
            "subcmd=%r: unknown subcommand, I know %r" %
            (subcmd, sorted(subcmds.keys()))
        )
      else:
        subcmd = subcmd_
    if has_subcmds:
      subusages = []
      for attr, subcmd_spec in (sorted(subcmds.items()) if subcmd is None else
                                ((subcmd, subcmds[subcmd]),)):
        with Pfx(attr):
          subusage = subcmd_spec.usage_text(
              short=short, usage_format_mapping=usage_format_mapping
          )
          cls.subcommand_usage_text(
              attr, usage_format_mapping=usage_format_mapping, short=short
          )
          if subusage:
            subusages.append(subusage.replace('\n', '\n  '))
      if subusages:
        subcmds_header = 'Subcommands' if subcmd is None else 'Subcommand'
        if short:
          subcmds_header += ' (short form, long form with "help", "-h" or "--help")'
        usage_message = '\n'.join(
            [
                usage_message,
                '  ' + subcmds_header + ':',
            ] + [
                '    ' + subusage.replace('\n', '\n    ')
                for subusage in subusages
            ]
        )
    return usage_message

  @classmethod
  def subcommand_usage_text(
      cls, subcmd, usage_format_mapping=None, short=False
  ):
    ''' Return the usage text for a subcommand.

        Parameters:
        * `subcmd`: the subcommand name
        * `short`: just include the first line of the usage message,
          intented for when there are many subcommands
    '''
    method = cls.subcommands()[subcmd].method
    subusage = None
    # support (method, get_suboptions)
    try:
      classy = issubclass(method, BaseCommand)
    except TypeError:
      classy = False
    if classy:
      # first paragraph of the class usage text
      doc = method.usage_text(cmd=subcmd)
      subusage_format, *_ = cutprefix(doc, 'Usage:').lstrip().split("\n\n", 1)
    else:
      # extract the usage from the object docstring
      doc = obj_docstring(method)
      if doc:
        if 'Usage:' in doc:
          # extract the Usage: paragraph
          pre_usage, post_usage = doc.split('Usage:', 1)
          pre_usage = pre_usage.strip()
          post_usage_format, *_ = post_usage.split('\n\n', 1)
          subusage_format = stripped_dedent(post_usage_format)
        else:
          # extract the first paragraph
          subusage_format, *_ = doc.split('\n\n', 1)
      else:
        # default usage text - include the docstring below a header
        subusage_format = "\n  ".join(
            ['{cmd} ...'] + [doc.split('\n\n', 1)[0]]
        )
    if subusage_format:
      if short:
        subusage_format, *_ = subusage_format.split('\n', 1)
      mapping = dict(sys.modules[method.__module__].__dict__)
      if usage_format_mapping:
        mapping.update(usage_format_mapping)
      mapping.update(cmd=subcmd)
      subusage = subusage_format.format_map(mapping)
    return subusage or None

  def apply_defaults(self):
    ''' Stub `apply_defaults` method.

        Subclasses can override this to set up the initial state of `self.options`.
    '''

  @pfx_method
  # pylint: disable=no-self-use
  def apply_opt(self, opt, val):
    ''' Handle an individual global command line option.

        This default implementation raises a `RuntimeError`.
        It only fires if `getopt` actually gathered arguments
        and would imply that a `GETOPT_SPEC` was supplied
        without an `apply_opt` or `apply_opts` method to implement the options.
    '''
    raise RuntimeError("unhandled option %r" % (opt,))

  def apply_opts(self, opts):
    ''' Apply command line options.

        Subclasses can override this
        but it is usually easier to override `apply_opt(opt,val)`.
    '''
    for opt, val in opts:
      with Pfx(opt if val is None else "%s %r" % (opt, val)):
        self.apply_opt(opt, val)

  # pylint: disable=no-self-use
  def apply_preargv(self, argv):
    ''' Do any preparsing of `argv` before the subcommand/main-args.
        Return the remaining arguments.

        This default implementation returns `argv` unchanged.
    '''
    return argv

  class _OptSpec(namedtuple('_OptSpec',
                            'help_text, parse, validate, unvalidated_message')
                 ):
    ''' A class to support parsing an option value.
    '''

    @classmethod
    def from_specs(cls, *specs):
      ''' Construct an `_OptSpec` from a list of positional parameters
          as for `poparg()`.
      '''
      parse = None
      help_text = None
      validate = None
      unvalidated_message = None
      for spec in specs:
        with Pfx("%s", r(spec)):
          if help_text is None and isinstance(spec, str):
            help_text = spec
          elif parse is None and callable(spec):
            parse = spec
          elif validate is None and callable(spec):
            validate = spec
          elif (validate is not None and unvalidated_message is None
                and isinstance(spec, str)):
            unvalidated_message = spec
          else:
            raise TypeError(
                "unexpected argument, expected help_text or parse,"
                " then optional validate and optional invalid message"
            )
      if help_text is None:
        help_text = (
            "string value" if parse is None else "value for %s" % (parse,)
        )
      if parse is None:
        parse = str
      if unvalidated_message is None:
        unvalidated_message = "invalid value"
      return cls(
          help_text=help_text,
          parse=parse,
          validate=validate,
          unvalidated_message=unvalidated_message,
      )

    def parse_value(self, value):
      ''' Parse `value` according to the spec.
          Raises a `GetoptError` for invalid values.
      '''
      with Pfx("%s %r", self.help_text, value):
        try:
          value = pfx_call(self.parse, value)
          if self.validate is not None:
            if not pfx_call(self.validate, value):
              raise ValueError(self.unvalidated_message)
        except ValueError as e:
          raise GetoptError(": ".join(e.args))  # pylint: disable=raise-missing-from
      return value

  @classmethod
  def poparg(cls, argv: List[str], *a):
    ''' Pop the leading argument off `argv` and parse it.
        Return the parsed argument.
        Raises `getopt.GetoptError` on a missing or invalid argument.

        This is expected to be used inside a `main` or `cmd_*`
        command handler method or inside `apply_preargv`.

        You can just use:

            value = argv.pop(0)

        but this method provides conversion and valuation
        and a richer failure mode.

        Parameters:
        * `argv`: the argument list, which is modified in place with `argv.pop(0)`
        * the argument list `argv` may be followed by some help text
          and/or an argument parser function.
        * `validate`: an optional function to validate the parsed value;
          this should return a true value if valid,
          or return a false value or raise a `ValueError` if invalid
        * `unvalidated_message`: an optional message after `validate`
          for values failing the validation

        Typical use inside a `main` or `cmd_*` method might look like:

            self.options.word = self.poparg(argv, int, "a count value")
            self.options.word = self.poparg(
                argv, int, "a count value",
               lambda count: count > 0, "count should be positive")

        Because it raises `GetoptError` on a bad argument
        the normal usage message failure mode follows automatically.

        Demonstration:

            >>> argv = ['word', '3', 'nine', '4']
            >>> BaseCommand.poparg(argv, "word to process")
            'word'
            >>> BaseCommand.poparg(argv, int, "count value")
            3
            >>> BaseCommand.poparg(argv, float, "length")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: length 'nine': float('nine'): could not convert string to float: 'nine'
            >>> BaseCommand.poparg(argv, float, "width", lambda width: width > 5)
            Traceback (most recent call last):
              ...
            getopt.GetoptError: width '4': invalid value
            >>> BaseCommand.poparg(argv, float, "length")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: length: missing argument
    '''
    opt_spec = cls._OptSpec.from_specs(*a)
    with Pfx(opt_spec.help_text):
      if not argv:
        raise GetoptError("missing argument")
      arg0 = argv.pop(0)
    return opt_spec.parse_value(arg0)

  @classmethod
  def popopts(
      cls,
      argv,
      attrfor=None,
      **opt_specs,
  ):
    ''' Parse option switches from `argv`, a list of command line strings
        with leading option switches.
        Modify `argv` in place and return a dict mapping switch names to values.

        The optional positional argument `attrfor`
        may supply an object whose attributes may be set by the options,
        for example:

            def cmd_foo(self, argv):
                self.popopts(argv, self.options, a='all', j_=('jobs', int))
                ... use self.options.jobs etc ...

        The expected options are specified by the keyword parameters
        in `opt_specs`:
        * a single letter name specifies a short option
          and a multiletter name specifies a long option
        * options requiring an argument have a trailing underscore
        * options not requiring an argument normally imply a value
          of `True`; if their synonym commences with a dash they will
          imply a value of `False`, for example `n='dry_run',y='-dry_run'`

        Example:

            >>> import os.path
            >>> options = SimpleNamespace(
            ...   all=False,
            ...   jobs=1,
            ...   number=0,
            ...   path=None,
            ...   trace_exec=True,
            ...   verbose=False,
            ...   dry_run=False)
            >>> argv = ['-v', '-y', '-j4', '--path=/foo', 'bah', '-x']
            >>> opt_dict = BaseCommand.popopts(
            ...   argv,
            ...   options,
            ...   a='all',
            ...   j_=('jobs',int),
            ...   n='dry_run',
            ...   v='verbose',
            ...   x='-trace_exec',
            ...   y='-dry_run',
            ...   dry_run=None,
            ...   path_=(str, os.path.isabs, 'not an absolute path'),
            ...   verbose=None,
            ... )
            >>> opt_dict
            {'verbose': True, 'dry_run': False, 'jobs': 4, 'path': '/foo'}
            >>> options
            namespace(all=False, jobs=4, number=0, path='/foo', trace_exec=True, verbose=True, dry_run=False)
    '''
    keyfor = {}
    shortopts = ''
    longopts = []
    opt_spec_map = {}
    opt_name_map = {}
    for opt_name, opt_spec in opt_specs.items():
      with Pfx("opt_spec[%r]=%r", opt_name, opt_spec):
        needs_arg = False
        if opt_name.endswith('_'):
          needs_arg = True
          opt_name = opt_name[:-1]
        if len(opt_name) == 1:
          opt = '-' + opt_name
          shortopts += opt_name
          if needs_arg:
            shortopts += ':'
        elif len(opt_name) > 1:
          opt_dashed = opt_name.replace('_', '-')
          opt = '--' + opt_dashed
          longopts.append(opt_dashed + '=' if needs_arg else opt_dashed)
          default_help_text = opt
        else:
          raise ValueError("unexpected opt_name %s" % (r(opt_name),))
        if opt_spec is None:
          specs = [opt_name, str]
        elif isinstance(opt_spec, (list, tuple)):
          specs = list(opt_spec)
        else:
          specs = [opt_spec]
        if specs:
          spec0 = specs[0]
          if isinstance(spec0, str) and (is_identifier(spec0) or
                                         (spec0.startswith('-')
                                          and is_identifier(spec0[1:]))):
            opt_name = specs[0]
            if len(specs) > 1 and isinstance(specs[1], str):
              specs.pop(0)
        if not specs or not isinstance(specs[0], str):
          specs.insert(0, default_help_text)
        if needs_arg:
          opt_spec = cls._OptSpec.from_specs(*specs)
          opt_spec_map[opt] = opt_spec
        opt_name_map[opt] = opt_name
    opts, post_argv = getopt(argv, shortopts, longopts)
    argv[:] = post_argv
    for opt, val in opts:
      with Pfx(opt):
        opt_name = opt_name_map[opt]
        try:
          opt_spec = opt_spec_map[opt]
        except KeyError:
          # option expected no arguments
          assert val == ''
          if opt_name.startswith('-'):
            value = False
            opt_name = opt_name[1:]
          else:
            value = True
        else:
          value = opt_spec.parse_value(val)
        keyfor[opt_name] = value
        if attrfor is not None:
          setattr(attrfor, opt_name, value)
    return keyfor

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def run(self, **kw_options):
    ''' Run a command.
        Returns the exit status of the command.
        May raise `GetoptError` from subcommands.

        Any keyword arguments are used to override `self.options` attributes
        for the duration of the run,
        for example to presupply a shared `RunState` from an outer context.

        If the first command line argument *foo*
        has a corresponding method `cmd_`*foo*
        then that argument is removed from the start of `argv`
        and `self.cmd_`*foo*`(cmd=`*foo*`)` is called
        and its value returned.
        Otherwise `self.main(argv)` is called
        and its value returned.

        If the command implementation requires some setup or teardown
        then this may be provided by the `run_context()`
        context manager method.
    '''
    # short circuit if we've already complainted about bad invocation
    if self._printed_usage:
      return 2
    options = self.options
    try:
      try:
        runstate = options.runstate
      except AttributeError:
        runstate = options.runstate = RunState(
            self.cmd, signals=options.runstate_signals
        )
      runstate = getattr(options, 'runstate', RunState(self.cmd))
      upd = getattr(options, 'upd', self.loginfo.upd)
      upd_context = nullcontext() if upd is None else upd
      with upd_context:
        with stackattrs(self, cmd=self._subcmd):
          with stackattrs(
              options,
              runstate=runstate,
              upd=upd,
          ):
            with stackattrs(options, **kw_options):
              with options.runstate:
                with self.run_context():
                  return self._run(self._subcmd, self, self._argv)
    except GetoptError as e:
      if self.getopt_error_handler(
          self.cmd,
          options,
          e,
          (None if self._printed_usage else self.usage_text(
              cmd=self.cmd, subcmd=self._subcmd)),
          subcmd=self._subcmd,
      ):
        self._printed_usage = True
        return 2
      raise

  # pylint: disable=unused-argument
  @staticmethod
  def getopt_error_handler(cmd, options, e, usage, subcmd=None):  # pylint: disable=unused-argument
    ''' The `getopt_error_handler` method
        is used to control the handling of `GetoptError`s raised
        during the command line parse
        or during the `main` or `cmd_`*subcmd*` calls.

        This default handler issues a warning containing the exception text,
        prints the usage message to standard error,
        and returns `True` to indicate that the error has been handled.

        The handler is called with these parameters:
        * `cmd`: the command name
        * `options`: the `options` object
        * `e`: the `GetoptError` exception
        * `usage`: the command usage or `None` if this was not provided
        * `subcmd`: optional subcommand name;
          if not `None`, is the name of the subcommand which caused the error

        It returns a true value if the exception is considered handled,
        in which case the main `run` method returns 2.
        It returns a false value if the exception is considered unhandled,
        in which case the main `run` method reraises the `GetoptError`.

        To let the exceptions out unhandled
        this can be overridden with a method which just returns `False`.

        Otherwise,
        the handler may perform any suitable action
        and return `True` to contain the exception
        or `False` to cause the exception to be reraised.
    '''
    warning("%s", e)
    if usage:
      print(usage.rstrip(), file=sys.stderr)
    return True

  @staticmethod
  @contextmanager
  def run_context():
    ''' Stub context manager which surrounds `main` or `cmd_`*subcmd*.
    '''
    # redundant try/finally to remind subclassers of correct structure
    try:
      yield
    finally:
      pass

  # pylint: disable=unused-argument
  @classmethod
  def cmd_help(cls, argv):
    ''' Usage: {cmd} [-l] [subcommand-names...]
          Print the full help for the named subcommands,
          or for all subcommands if no names are specified.
          -l  Long help even if no subcommand-names provided.
    '''
    subcmds = cls.subcommands()
    if argv and argv[0] == '-l':
      argv.pop(0)
      short = False
    elif argv:
      short = False
    else:
      short = True
    argv = argv or sorted(subcmds)
    xit = 0
    print("help:")
    unknown = False
    for subcmd in argv:
      with Pfx(subcmd):
        subcmd_ = subcmd.replace('-', '_').replace('.', '_')
        try:
          subcommand = subcmds[subcmd_]
        except KeyError:
          warning("unknown subcommand")
          unknown = True
          xit = 1
          continue
        subusage = subcommand.usage_text(short)
        if not subusage:
          warning("no help")
          xit = 1
          continue
        print(' ', subusage.replace('\n', '\n    '))
    if unknown:
      warning("I know: %s", ', '.join(sorted(subcmds.keys())))
    return xit
