#!/usr/bin/env python3
#
# Command line stuff. - Cameron Simpson <cs@cskk.id.au> 03sep2015
#
# pylint: disable=too-many-lines

''' Convenience functions for working with the Cmd module,
    the BaseCommand class for constructing command line programmes,
    and other command line related stuff.
'''

from cmd import Cmd
from code import interact
from collections import namedtuple
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from getopt import getopt, GetoptError
from inspect import isclass
from os.path import basename
try:
  import readline  # pylint: disable=unused-import
except ImportError:
  pass
import shlex
from signal import SIGHUP, SIGINT, SIGQUIT, SIGTERM
import sys
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union

from typeguard import typechecked

from cs.context import stackattrs
from cs.deco import decorator, default_params, fmtdoc, Promotable
from cs.lex import (
    cutprefix,
    cutsuffix,
    indent,
    is_identifier,
    r,
    stripped_dedent,
)
from cs.logutils import setup_logging, warning, error, exception
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.doc import obj_docstring
from cs.resources import RunState, uses_runstate
from cs.result import CancellationError
from cs.threads import HasThreadState, ThreadState
from cs.typingutils import subtype
from cs.upd import Upd, uses_upd, print  # pylint: disable=redefined-builtin

__version__ = '20241007-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.doc',
        'cs.resources',
        'cs.result',
        'cs.threads',
        'cs.typingutils',
        'cs.upd',
        'typeguard',
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

@dataclass
class OptionSpec(Promotable):
  ''' A class to support parsing an option value.
  '''

  UNVALIDATED_MESSAGE_DEFAULT = "invalid value"

  help_text: str
  parse: Optional[Callable[[str], Any]] = None
  validate: Optional[Callable[[Any], bool]] = None
  unvalidated_message: str = UNVALIDATED_MESSAGE_DEFAULT

  def parse_value(self, value):
    ''' Parse `value` according to the spec.
        Raises `GetoptError` for invalid values.
    '''
    with Pfx("%s %r", self.help_text, value):
      try:
        value = pfx_call(self.parse, value)
        if self.validate is not None:
          if not pfx_call(self.validate, value):
            raise ValueError(self.unvalidated_message)
      except ValueError as e:
        raise GetoptError(str(e)) from e  # pylint: disable=raise-missing-from
    return value

  @classmethod
  def promote(cls, obj):
    ''' Construct an `OptionSpec` from a list of positional parameters
        as for `poparg()` or `popopts()`.

        Examples:

          >>> OptionSpec.promote( () ) #doctest: +ELLIPSIS
          OptionSpec(help_text='string value', parse=<function ...>, validate=None, unvalidated_message='invalid value')
    '''
    if isinstance(obj, cls):
      return obj
    # reform obj as a list to convert into a specification
    if isinstance(obj, str):
      # just the help text
      specs = (obj,)
    elif callable(obj):
      # just the value parser/factory
      specs = (obj,)
    else:
      # some iterable
      specs = tuple(obj)
    parse = None
    help_text = None
    validate = None
    unvalidated_message = None
    for spec in specs:
      with Pfx("%r", spec):
        if isinstance(spec, str):
          # help text or invlaid message
          if help_text is None:
            help_text = spec
            continue
          if unvalidated_message is None:
            unvalidated_message = spec
            continue
        elif callable(spec):
          # parser/factory or validator
          if parse is None:
            parse = spec
            continue
          if validate is None:
            validate = spec
            continue
        raise TypeError(
            "unexpected argument, expected help_text or parse,"
            " then optional validate and optional invalid message,"
            " received %s" % (r(spec),)
        )
    if help_text is None:
      help_text = (
          "string value" if parse is None else "value for %s" % (parse,)
      )
    if parse is None:
      # pass option value through unchanged
      parse = lambda val: val  # pylint: disable=unnecessary-lambda-assignment
    if unvalidated_message is None:
      unvalidated_message = cls.UNVALIDATED_MESSAGE_DEFAULT
    return cls(
        help_text=help_text,
        parse=parse,
        validate=validate,
        unvalidated_message=unvalidated_message,
    )

def extract_usage_from_doc(doc: str | None,
                           usage_marker="Usage:") -> Tuple[str, str]:
  ''' Extract a `"Usage:"`paragraph from a docstring
      and return the unindented usage and the docstring with that paragraph elided.

      If the usage paragraph is not present, return `(None,doc)`.
  '''
  if not doc:
    # no doc, return unchanged
    return None, doc
  try:
    pre_usage, usage_onward = doc.split(usage_marker, 1)
  except ValueError:
    # no usage: paragraph
    return None, doc
  try:
    usage_format, post_usage = usage_onward.split("\n\n", 1)
  except ValueError:
    usage_format, post_usage = usage_onward.rstrip(), ''
  usage_format = stripped_dedent(usage_format)
  # indent the second and following lines
  try:
    top_line, post_lines = usage_format.split("\n", 1)
  except ValueError:
    # single line usage only
    pass
  else:
    usage_format = f'{top_line}\n{indent(post_lines)}'
  return usage_format, pre_usage + post_usage

@dataclass
class SubCommand:
  ''' An implementation for a subcommand.
  '''

  # the BaseCommand instance with which we're associated
  command: "BaseCommand"
  # a method or a subclass of BaseCommand
  method: Callable
  # the notional name of the command/subcommand
  cmd: str = None
  # optional additional usage keyword mapping
  usage_mapping: Mapping[str, Any] = field(default_factory=dict)

  @property
  def instance(self):
    ''' An instance of the class for `self.method`.
    '''
    return self.method(...) if isclass(self.method) else self.method.__self__

  def get_cmd(self) -> str:
    if self.cmd is None:
      method = self.method
      if isclass(method):
        return cutsuffix(method.__name__, 'Command').lower()
      return cutprefix(method.__name__, self.SUBCOMMAND_METHOD_PREFIX)
    return self.cmd

  @typechecked
  def __call__(self, argv: List[str]):
    ''' Run the subcommand.

        Parameters:
        * `argv`: the command line arguments after the subcommand name
    '''
    method = self.method
    if isclass(method):
      # plumb the options through to the subcommand
      updates = self.command.options.as_dict()
      updates.update(cmd=self.get_cmd())
      return pfx_call(method, argv, **updates).run()
    return method(argv)

  def default_usage(self):
    ''' Return `'{cmd} [options...]'` or `'{cmd} subcommand [options...]'`.
    '''
    if isclass(self.method):
      has_subcommands_test = getattr(
          self.instance, 'has_subcommands', lambda: False
      )
    else:
      has_subcommands_test = getattr(
          self.method, 'has_subcommands', lambda: False
      )
    return (
        '{cmd} subcommand [options...]'
        if has_subcommands_test() else '{cmd} [options...]'
    )

  def get_usage_format(self) -> str:
    ''' Return the usage format string for this subcommand.
        *Note*: no leading "Usage:" prefix.

        This first tries `self.method.USAGE_FORMAT`, falling back
        to deriving it from `obj_docstring(self.method)`.
        Usually a subcommand which is another `BaseCommand` instance
        will have a `.USAGE_FORMAT` attribute and a subcommand which
        is a method will derive the usage from its docstring.

        When deriving from the docstring we look for a paragraph
        commencing with the string `Usage:` and otherwise fall back
        to its first parapgraph.
    '''
    method = self.method
    method_name = method.__name__
    subcmd = self.command.method_cmdname(method_name)
    try:
      usage_format = method.USAGE_FORMAT
    except AttributeError:
      # derive from the docstring or from self.default_usage()
      doc = obj_docstring(self.method)
      usage_format, doc = extract_usage_from_doc(doc)
      if not usage_format:
        # No "Usage:" paragraph - use default usage line and first paragraph.
        usage_format = self.default_usage()
        paragraph1 = stripped_dedent(doc.split('\n\n', 1)[0])
        if paragraph1:
          usage_format += "\n" + indent(paragraph1)
    # The existing USAGE_FORMAT based usages have the word "Usage:"
    # at the front but this is supplied at print time now.
    return cutprefix(usage_format, 'Usage:').lstrip()

  def get_usage_keywords(self):
    ''' Return a mapping to be used when formatting the usage format string.
    '''
    usage_mapping = dict(getattr(self.method, 'USAGE_KEYWORDS', {}))
    return usage_mapping

  def get_subcommands(self):
    ''' Return `self.method`'s mapping of subcommand name to `SubCommand`.
    '''
    method = self.method
    if isclass(method):
      method = method(...)
    try:
      get_subcommands = method.subcommands
    except AttributeError:
      return {}
    return get_subcommands()

  def get_subcmds(self):
    ''' Return the names of `self.method`'s subcommands in lexical order.
    '''
    return sorted(self.get_subcommands().keys())

  @typechecked
  def usage_text(
      self,
      *,
      short: bool,
      recurse: bool = False,
      show_subcmds: Optional[Union[bool, str, List[str]]] = None,
      usage_mapping: Optional[Mapping] = None,
  ) -> str:
    ''' Return the filled out usage text for this subcommand.
    '''
    if show_subcmds is None:
      show_subcmds = True
    if isinstance(show_subcmds, bool):
      if show_subcmds:
        show_subcmds = self.get_subcmds()
      else:
        show_subcmds = []
    elif isinstance(show_subcmds, str):
      show_subcmds = [show_subcmds]
    usage_mapping = usage_mapping or {}
    usage_format = self.get_usage_format()  # pylint: disable=no-member
    if short:
      # just the summary line and opening sentence of the description
      lines = usage_format.split('\n')
      usage_lines = [lines.pop(0)]
      while usage_lines[-1].endswith('\\'):
        usage_lines.append(lines.pop(0))
      if lines and lines[0].endswith('.'):
        usage_lines.append(lines.pop(0))
      usage_format = '\n'.join(usage_lines)
    mapping = self.get_usage_keywords()
    if usage_mapping:
      mapping.update(usage_mapping)
    if self.usage_mapping:
      mapping.update(self.usage_mapping)
    mapping.update(cmd=self.get_cmd().replace('_', '-'))
    with Pfx("format %r using %r", usage_format, mapping):
      usage = usage_format.format_map(mapping)
    if recurse or show_subcmds:
      # include the (or some) subcmds
      subusages = [
          subcommand.usage_text(
              short=short,
              recurse=recurse,
          )
          for subcmd, subcommand in sorted(self.get_subcommands().items())
          if show_subcmds is None or subcmd in show_subcmds
      ]
      if subusages:
        subcmds_header = (
            'Subcommands'
            if show_subcmds is None or len(show_subcmds) > 1 else 'Subcommand'
        )
        if short:
          subcmds_header += ' (short form, long form with "help", "-h" or "--help")'
        subusage_listing = "\n".join(
            [f'{subcmds_header}:', *map(indent, subusages)]
        )
        usage = f'{usage}\n{indent(subusage_listing)}'
    return usage

# gimmicked name to support @fmtdoc on BaseCommandOptions.popopts
_COMMON_OPT_SPECS = dict(
    n='dry_run',
    q='quiet',
    v='verbose',
)

@dataclass
class BaseCommandOptions(HasThreadState):
  ''' A base class for the `BaseCommand` `options` object.

      This is the default class for the `self.options` object
      available during `BaseCommand.run()`,
      and available as the `BaseCommand.Options` attribute.

      Any keyword arguments are applied as field updates to the instance.

      It comes prefilled with:
      * `.dry_run=False`
      * `.force=False`
      * `.quiet=False`
      * `.verbose=False`
      and a `.doit` property which is the inverse of `.dry_run`.

      It is recommended that if `BaseCommand` subclasses use a
      different type for their `Options` that it should be a
      subclass of `BaseCommandOptions`.
      Since `BaseCommandOptions` is a data class, this typically looks like:

          @dataclass
          class Options(BaseCommand.Options):
              ... optional extra fields etc ...
  '''

  DEFAULT_SIGNALS = SIGHUP, SIGINT, SIGQUIT, SIGTERM
  COMMON_OPT_SPECS = _COMMON_OPT_SPECS

  # the cmd prefix while a command runs
  cmd: Optional[str] = None
  # dry run, no action
  dry_run: bool = False
  force: bool = False
  quiet: bool = False
  runstate: Optional[RunState] = None
  runstate_signals: Tuple[int] = DEFAULT_SIGNALS
  verbose: bool = False
  opt_spec_class = OptionSpec

  perthread_state = ThreadState()

  def as_dict(self):
    ''' Return the optionas as a `dict`.
    '''
    return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

  def copy(self, **updates):
    ''' Return a new instance of `BaseCommandOptions` (well, `type(self)`)
        which is a shallow copy of the public attributes from `self.__dict__`.

        Any keyword arguments are applied as attribute updates to the copy.
    '''
    copied = pfx_call(type(self), **self.as_dict())
    for k, v in updates.items():
      setattr(copied, k, v)
    return copied

  def update(self, **updates):
    ''' Modify the options in place with the mapping `updates`.
        It would be more normal to call the options in a `with` statement
        as shown for `__call__`.
    '''
    for k, v in updates.items():
      setattr(self, k, v)

  # TODO: remove this - the overt make-a-copy-and-with-the-copy is clearer
  @contextmanager
  def __call__(self, **updates):
    ''' Calling the options object returns a context manager whose
        value is a shallow copy of the options with any `suboptions` applied.

        Example showing the semantics:

            >>> from cs.cmdutils import BaseCommandOptions
            >>> @dataclass
            ... class DemoOptions(BaseCommandOptions):
            ...   x: int = 0
            ...
            >>> options = DemoOptions(x=1)
            >>> assert options.x == 1
            >>> assert not options.verbose
            >>> with options(verbose=True) as subopts:
            ...     assert options is not subopts
            ...     assert options.x == 1
            ...     assert not options.verbose
            ...     assert subopts.x == 1
            ...     assert subopts.verbose
            ...
            >>> assert options.x == 1
            >>> assert not options.verbose

    '''
    suboptions = self.copy(**updates)
    yield suboptions

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

  @fmtdoc
  def popopts(self, argv, **opt_specs):
    ''' Convenience method to appply `BaseCommand.popopts` to the options (`self`).

        Example for a `BaseCommand` `cmd_foo` method:

            def cmd_foo(self, argv):
                self.options.popopts(
                    c_='config',
                    l='long',
                    x='trace',
                )
                if self.options.dry_run:
                    print("dry run!")

        The class attribute `COMMON_OPT_SPECS` is a mapping of
        options which are always supported. `BaseCommandOptions`
        has: `COMMON_OPT_SPECS={_COMMON_OPT_SPECS!r}`.

        A subclass with more common options might extend this like so,
        from `cs.hashindex`:

            COMMON_OPT_SPECS = dict(
                e='ssh_exe',
                h_='hashname',
                H_='hashindex_exe',
                **BaseCommand.Options.COMMON_OPT_SPECS,
            )

    '''
    for k, v in self.COMMON_OPT_SPECS.items():
      opt_specs.setdefault(k, v)
    return BaseCommand.popopts(argv, self, **opt_specs)

@decorator
def uses_cmd_options(
    func, cls=BaseCommandOptions, options_param_name='options'
):
  ''' A decorator to provide a default parameter containing the
      prevailing `BaseCommandOptions` instance as the `options` keyword
      argument, using the `cs.deco.default_params` decorator factory.

      This allows functions to utilitse global options set by a
      command such as `options.dry_run` or `options.verbose` without
      the tedious plumbing through the entire call stack.

      Parameters:
      * `cls`: the `BaseCommandOptions` or `BaseCommand` class,
        default `BaseCommandOptions`. If a `BaseCommand` subclass is
        provided its `cls.Options` class is used.
      * `options_param_name`: the parameter name to provide, default `options`

      Examples:

          @uses_cmd_options
          def f(x,*,options):
              """ Run directly from the prevailing options. """
              if options.verbose:
                  print("doing f with x =", x)
              ....

          @uses_cmd_options
          def f(x,*,verbose=None,options):
              """ Get defaults from the prevailing options. """
              if verbose is None:
                  verbose = options.verbose
              if verbose:
                  print("doing f with x =", x)
              ....
  '''
  if issubclass(cls, BaseCommand):
    cls = cls.Options
  return default_params(
      func, **{options_param_name: lambda: cls.default() or cls()}
  )

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
          def main(argv=None, **run_kw):
              """ The command line mode.
              """
              return MyCommand(argv).run(**run_kw)
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
      commencing with `Usage:` then that paragraph is incorporated
      into the main usage message automatically.
      Example:

          def cmd_ls(self, argv):
              """ Usage: {cmd} [paths...]
                    Emit a listing for the named paths.

                  Further docstring non-usage information here.
              """
              ... do the "ls" subcommand ...

      The subclass is customised by overriding the following methods:
      * `apply_opt(opt,val)`:
        apply an individual getopt global command line option
        to `self.options`.
      * `apply_opts(opts)`:
        apply the `opts` to `self.options`.
        `opts` is an `(option,value)` sequence
        as returned by `getopot.getopt`.
        The default implementation iterates over these and calls `apply_opt`.
      * `run_context()`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively,
        such as to open and close a database.
      * `cmd_`*subcmd*`(argv)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then the method `cmd_`*subcmd*`(subcmd_argv)`
        will be called where `subcmd_argv` contains the command line arguments
        following *subcmd*.
      * `main(argv)`:
        if there are no `cmd_`*subcmd*` methods then method `main(argv)`
        will be called where `argv` contains the command line arguments.

      Editorial: why not arparse?
      Primarily because when incorrectly invoked
      an argparse command line prints the help/usage messgae
      and aborts the whole programme with `SystemExit`.
      But also, I find the whole argparse `add_argument` thing cumbersome.
  '''

  SUBCOMMAND_METHOD_PREFIX = 'cmd_'
  GETOPT_SPEC = ''
  SUBCOMMAND_ARGV_DEFAULT = 'shell'

  Options = BaseCommandOptions

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
          If not specified a new `self.Options` instance
          is allocated for use as `options`.
          The default `Options` class is `BaseCommandOptions`,
          a dataclass with some prefilled attributes and properties
          to aid use later.
        Other keyword arguments are applied to `self.options`
        as attributes.

        The `cmd` and `argv` parameters have some fiddly semantics for convenience.
        There are 3 basic ways to initialise:
        * `BaseCommand()`: `argv` comes from `sys.argv`
          and the value for `cmd` is derived from `argv[0]`
        * `BaseCommand(argv)`: `argv` is the complete command line
          including the command name and the value for `cmd` is
          derived from `argv[0]`
        * `BaseCommand(argv, cmd=foo)`: `argv` is the command
          arguments _after_ the command name and `cmd` is set to
          `foo`

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
    if argv is None:
      # using sys.argv
      argv = list(sys.argv)
    elif argv is ...:
      # dummy mode for BaseCOmmand instances made to access this
      # but not run a command
      pass
    else:
      # argv provided
      argv = list(argv)
    if cmd is None:
      if argv is ... or not argv:
        cmd = cutsuffix(self.__class__.__name__, 'Command').lower()
      else:
        cmd = basename(argv.pop(0))
        if cmd.endswith('.py'):
          # "python -m foo" sets argv[0] to "..../foo.py"
          # fall back to the class name
          cmd = cutsuffix(self.__class__.__name__, 'Command').lower()
    options = self.Options(cmd=cmd)
    # override the default options
    for option, value in kw_options.items():
      setattr(options, option, value)
    self.cmd = cmd
    self._argv = argv
    self.options = options

  def _prerun_setup(self):
    argv = self._argv
    options = self.options
    subcmds = self.subcommands()
    has_subcmds = self.has_subcommands()
    log_level = getattr(options, 'log_level', None)
    loginfo = setup_logging(cmd=self.cmd, level=log_level)
    # post: argv is list of arguments after the command name
    self.loginfo = loginfo
    self._run = lambda argv: 2
    # we catch GetoptError from this suite...
    subcmd = None  # default: no subcmd specific usage available
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # catch bare -h or --help if no 'h' in the getopt_spec
      if ('h' not in getopt_spec and len(argv) == 1
          and argv[0] in ('-h', '-help', '--help')):
        argv = ['help']
      else:
        # we do this regardless in order to honour '--'
        opts, argv = getopt(argv, getopt_spec, '')
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
        self._run = SubCommand(self, main)
      else:
        # expect a subcommand on the command line
        if not argv:
          default_argv = self.SUBCOMMAND_ARGV_DEFAULT
          if not default_argv:
            raise GetoptError(
                "missing subcommand, expected one of: %s" %
                (', '.join(sorted(subcmds.keys())),)
            )
          argv = (
              [default_argv]
              if isinstance(default_argv, str) else list(default_argv)
          )
        subcmd = argv.pop(0)
        try:
          subcommand = self.subcommand(subcmd)
        except KeyError:
          # pylint: disable=raise-missing-from
          bad_subcmd = subcmd
          subcmd = None
          raise GetoptError(
              f'unrecognised subcommand {bad_subcmd!r}, expected one of:'
              f' {", ".join(sorted(subcmds.keys()))}'
          )

        def _run(argv):
          with Pfx(subcmd):
            return subcommand(argv)

        self._run = _run
    except GetoptError as e:
      if self.getopt_error_handler(
          options.cmd,
          self.options,
          e,
          self.usage_text(short=True, show_subcmds=subcmd),
      ):
        return
      raise

  @classmethod
  def method_cmdname(cls, method_name: str):
    ''' The `cmd` value from a method name.
    '''
    return cutprefix(method_name, cls.SUBCOMMAND_METHOD_PREFIX)

  @cache
  def subcommands(self):
    ''' Return a mapping of subcommand names to subcommand specifications
        for class attributes which commence with `cls.SUBCOMMAND_METHOD_PREFIX`
        by default `'cmd_'`.
    '''
    cls = type(self)
    prefix = cls.SUBCOMMAND_METHOD_PREFIX
    usage_mapping = getattr(cls, 'USAGE_KEYWORDS', {})
    mapping = {}
    for method_name in dir(cls):
      if method_name.startswith(prefix):
        subcmd = self.method_cmdname(method_name)
        method = getattr(self, method_name)
        subusage_mapping = dict(usage_mapping)
        method_keywords = getattr(method, 'USAGE_KEYWORDS', {})
        subusage_mapping.update(method_keywords)
        subusage_mapping.update(cmd=subcmd)
        mapping[subcmd] = SubCommand(
            self,
            method,
            cmd=subcmd,
            usage_mapping=subusage_mapping,
        )
    return mapping

  def has_subcommands(self):
    ''' Test whether the class defines additional subcommands.
    '''
    subcmds = set(self.subcommands())
    subcmds.discard('help')
    subcmds.discard('shell')
    return bool(subcmds)

  @cache
  def subcommand(self, subcmd: str):
    subcmd_ = subcmd.replace('-', '_').replace('.', '_')
    subcommands = self.subcommands()
    return subcommands[subcmd_]

  def usage_text(
      self,
      *,
      cmd=None,
      format_mapping=None,
      short=False,
      show_subcmds=None,
  ):
    ''' Compute the "Usage:" message for this class
        from the top level `USAGE_FORMAT`
        and the `'Usage:'`-containing docstrings of its `cmd_*` methods.

        Parameters:
        * `cmd`: optional command name, default derived from the class name
        * `format_mapping`: an optional format mapping for filling
          in format strings in the usage text
        * `short`: default `False`; if true then just provide the opening sentence
        * `show_subcmds`: constrain the usage to particular subcommands
          named in `show_subcmds`; this is used to produce a shorter
          usage for subcommand usage failures
    '''
    return SubCommand(
        self, method=type(self)
    ).usage_text(
        short=short, show_subcmds=show_subcmds
    )

  def subcommand_usage_text(
      self, subcmd, usage_format_mapping=None, short=False
  ):
    ''' Return the usage text for a subcommand.

        Parameters:
        * `subcmd`: the subcommand name
        * `short`: just include the first line of the usage message,
          intented for when there are many subcommands
    '''
    method = self.subcommands()[subcmd].method
    subusage = None
    # support (method, get_suboptions)
    try:
      classy = issubclass(method, BaseCommand)
    except TypeError:
      classy = False
    if classy:
      # first paragraph of the class usage text
      doc = method([]).usage_text(cmd=subcmd)
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
    return subusage.replace('\n', '\n  ')

  @classmethod
  def extract_usage(cls, cmd=None):
    ''' Extract the `Usage:` paragraph from `cls__doc__` if present.
        Return a 2-tuple of `(doc_without_usage,usage_text)`
        being the remaining docstring and a full usage message.
    '''
    if cmd is None:
      # infer a cmd from the class name
      cmd = cutsuffix(cls.__name__, 'Command').lower()
    instance = cls([cmd])
    usage_format, doc_without_usage = extract_usage_from_doc(
        obj_docstring(cls)
    )
    ## # This little shuffle is so that instance.usage_text()
    ## # does not process format strings twice.
    ## cls.__doc__ = doc_without_usage
    if usage_format and not hasattr(cls, 'USAGE_FORMAT'):
      cls.USAGE_FORMAT = usage_format
    usage_text = instance.usage_text()
    return doc_without_usage, usage_text

  @pfx_method
  # pylint: disable=no-self-use
  def apply_opt(self, opt, val):
    ''' Handle an individual global command line option.

        This default implementation raises a `RuntimeError`.
        It only fires if `getopt` actually gathered arguments
        and would imply that a `GETOPT_SPEC` was supplied
        without an `apply_opt` or `apply_opts` method to implement the options.
    '''
    raise NotImplementedError("unhandled option %r" % (opt,))

  def apply_opts(self, opts):
    ''' Apply command line options.

        Subclasses can override this
        but it is usually easier to override `apply_opt(opt,val)`.
    '''
    badopts = False
    for opt, val in opts:
      with Pfx(opt if val is None else "%s %r" % (opt, val)):
        try:
          self.apply_opt(opt, val)
        except GetoptError as e:
          warning("%s", e)
          badopts = True
    if badopts:
      raise GetoptError("bad options")

  # pylint: disable=no-self-use
  def apply_preargv(self, argv):
    ''' Do any preparsing of `argv` before the subcommand/main-args.
        Return the remaining arguments.

        This default implementation applies the default options
        supported by `self.options` (an instance of `self.Options`
        class).
    '''
    self.options.popopts(argv)
    return argv

  @classmethod
  def poparg(
      cls, argv: List[str], *a, unpop_on_error=False, opt_spec_class=None
  ):
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
        * `unpop_on_error`: optional keyword parameter, default `False`;
          if true then push the argument back onto the front of `argv`
          if it fails to parse; `GetoptError` is still raised

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
            >>> argv = ['-5', 'zz']
            >>> BaseCommand.poparg(argv, float, "size", lambda f: f>0, "size should be >0")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: size '-5': size should be >0
            >>> argv  # -5 was still consumed
            ['zz']
            >>> BaseCommand.poparg(argv, float, "size2", unpop_on_error=True)
            Traceback (most recent call last):
              ...
            getopt.GetoptError: size2 'zz': float('zz'): could not convert string to float: 'zz'
            >>> argv  # zz was pushed back
            ['zz']
    '''
    if opt_spec_class is None:
      opt_spec_class = OptionSpec
    opt_spec = opt_spec_class.promote(a)
    with Pfx(opt_spec.help_text):
      if not argv:
        raise GetoptError("missing argument")
      arg0 = argv.pop(0)
    try:
      return opt_spec.parse_value(arg0)
    except GetoptError:
      if unpop_on_error:
        argv.insert(0, arg0)
      raise

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
        * options not starting with a letter may be preceeded by an underscore
          to allow use in the parameter list, for example `_1='once'`
          for a `-1` option setting the `once` option name
        * a single letter name specifies a short option
          and a multiletter name specifies a long option
        * options requiring an argument have a trailing underscore
        * options not requiring an argument normally imply a value
          of `True`; if their synonym commences with a dash they will
          imply a value of `False`, for example `n='dry_run',y='-dry_run'`

        The `BaseCommandOptions` class provides a `popopts` method
        which is a shim for this method with `attrfor=self` i.e.
        the options object.
        So common use in a command method usually looks like this:

            class SomeCommand(BaseCommand):

                def cmd_foo(self, argv):
                    options = self.options
                    # accept a -j or --jobs options
                    options.popopts(argv, jobs=1, j='jobs')
                    print("jobs =", options.jobs)

        The `self.options` object is preprovided as an instance of
        the `self.Options` class, which is `BaseCommandOptions` by
        default. This presupplies support for some basic options
        like `-v` for "verbose" and so forth, and a subcommand
        need not describe these in a call to `self.options.popopts()`.

        Example:

            >>> import os.path
            >>> from typing import Optional
            >>> @dataclass
            ... class DemoOptions(BaseCommandOptions):
            ...   all: bool = False
            ...   jobs: int = 1
            ...   number: int = 0
            ...   once: bool = False
            ...   path: Optional[str] = None
            ...   trace_exec: bool = False
            ...
            >>> options = DemoOptions()
            >>> argv = ['-1', '-v', '-y', '-j4', '--path=/foo', 'bah', '-x']
            >>> opt_dict = options.popopts(
            ...   argv,
            ...   _1='once',
            ...   a='all',
            ...   j_=('jobs',int),
            ...   x='-trace_exec',
            ...   y='-dry_run',
            ...   dry_run=None,
            ...   path_=(str, os.path.isabs, 'not an absolute path'),
            ...   verbose=None,
            ... )
            >>> opt_dict
            {'once': True, 'verbose': True, 'dry_run': False, 'jobs': 4, 'path': '/foo'}
            >>> options # doctest: +ELLIPSIS
            DemoOptions(cmd=None, dry_run=False, force=False, quiet=False, runstate_signals=(...), verbose=True, all=False, jobs=4, number=0, once=True, path='/foo', trace_exec=False)
    '''
    keyfor = {}
    shortopts = ''
    longopts = []
    opt_spec_map = {}
    opt_name_map = {}
    for opt_name, opt_spec in opt_specs.items():
      with Pfx("opt_spec[%r]=%r", opt_name, opt_spec):
        needs_arg = False
        # leading underscore for numeric options like -1
        if opt_name.startswith('_'):
          opt_name = opt_name[1:]
          if is_identifier(opt_name):
            warning(
                "unnecessary leading underscore on valid identifier option"
            )
        # trailing underscore indicates that the option expected an argument
        if opt_name.endswith('_'):
          needs_arg = True
          opt_name = opt_name[:-1]
        # single character option -x
        if len(opt_name) == 1:
          opt = '-' + opt_name
          shortopts += opt_name
          if needs_arg:
            shortopts += ':'
        # long option
        elif len(opt_name) > 1:
          opt_dashed = opt_name.replace('_', '-')
          opt = '--' + opt_dashed
          longopts.append(opt_dashed + '=' if needs_arg else opt_dashed)
          default_help_text = opt
        else:
          raise ValueError("unexpected opt_name %s" % (r(opt_name),))
        # construct an option specification list containing:
        #   [opt_name:str] [help_text:str] [parse:Callable [validate:Callable [invalid_msg:str]]]
        if opt_spec is None:
          # default opt_spec: opt citation and type str
          specs = [opt_name, str]
        elif isinstance(opt_spec, (list, tuple)):
          # list or tuple: copy it to a list
          specs = list(opt_spec)
        else:
          # promote scalar to single element list
          specs = [opt_spec]
        if specs:
          # see if the leading spec is an option citation
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
          opt_spec = OptionSpec.promote(specs)
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
        for example to presupply a shared `Upd` from an outer context.

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
    self._prerun_setup()
    options = self.options
    try:
      try:
        with self.run_context(**kw_options):
          return self._run(self._argv)
      except CancellationError:
        error("cancelled")
        return 1
    except GetoptError as e:
      if self.getopt_error_handler(
          self.cmd,
          options,
          e,
          self.usage_text(cmd=self.cmd),
      ):
        return 2
      raise

  def cmdloop(self, intro=None):
    ''' Use `cmd.Cmd` to run a command loop which calls the `cmd_`* methods.
    '''
    if not sys.stdin.isatty():
      raise GetoptError("input is not a tty")
    # TODO: get intro from usage/help
    cmdobj = BaseCommandCmd(self)
    cmdobj.prompt = f'{self.cmd}> '
    cmdobj.cmdloop(intro)

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
      print("Usage:", usage, file=sys.stderr)
    return True

  @uses_runstate
  def handle_signal(self, sig, frame, *, runstate: RunState):
    ''' The default signal handler, which cancels the default `RunState`.
    '''
    runstate.cancel()

  @contextmanager
  @uses_runstate
  @uses_upd
  def run_context(self, *, runstate: RunState, upd: Upd, **options_kw):
    ''' The context manager which surrounds `main` or `cmd_`*subcmd*.

        This default does several things, and subclasses should
        override it like this:

            @contextmanager
            def run_context(self):
              with super().run_context():
                try:
                  ... subclass context setup ...
                    yield
                finally:
                  ... any unconditional cleanup ...
    '''
    # prefer the runstate from the options if specified
    runstate = self.options.runstate or runstate
    # redundant try/finally to remind subclassers of correct structure
    try:
      run_options = self.options.copy(runstate=runstate, **options_kw)
      with run_options:  # make the default ThreadState
        with stackattrs(
            self,
            options=run_options,
        ):
          with upd:
            with runstate:
              with runstate.catch_signal(
                  run_options.runstate_signals,
                  call_previous=False,
                  handle_signal=self.handle_signal,
              ):
                yield

    finally:
      pass

  def cmd_help(self, argv):
    ''' Usage: {cmd} [-l] [subcommand-names...]
          Print help for subcommands.
          This outputs the full help for the named subcommands,
          or the short help for all subcommands if no names are specified.
          -l  Long help even if no subcommand-names provided.
    '''
    if argv and argv[0] == '-l':
      argv.pop(0)
      short = False
    elif argv:
      short = False
    else:
      short = True
    subcmds = self.subcommands()
    argv = argv or sorted(subcmds)
    unknown = False
    show_subcmds = []
    for subcmd in argv:
      if subcmd in subcmds:
        show_subcmds.append(subcmd)
      else:
        warning("unknown subcommand %r", subcmd)
        unknown = True
    if unknown:
      warning("I know: %s", ', '.join(sorted(subcmds.keys())))
    if short:
      print("Longer help with the -l option.")
    print(
        "Usage:",
        self.usage_text(short=short, show_subcmds=show_subcmds or None)
    )

  @uses_upd
  def cmd_shell(self, argv, *, upd: Upd):
    ''' Usage: {cmd}
          Run a command prompt via cmd.Cmd using this command's subcommands.
    '''
    if argv:
      raise GetoptError("extra arguments")
    with upd.without():
      self.cmdloop()

  def repl(self, *argv, banner=None, local=None):
    ''' Run an interactive Python prompt with some predefined local names.
        Aka REPL (Read Evaluate Print Loop).

        Parameters:
        * `argv`: any notional command line arguments
        * `banner`: optional banner string
        * `local`: optional local names mapping

        The default `local` mapping is a `dict` containing:
        * `argv`: from `argv`
        * `options`: from `self.options`
        * `self`: from `self`
        * the attributes of `options`
        * the attributes of `self`

        This is not presented automatically as a subcommand, but
        commands wishing such a command should provide something
        like this:

            def cmd_repl(self, argv):
                """ Usage: {cmd}
                      Run an interactive Python prompt with some predefined local names.
                """
                return self.repl(*argv)
    '''
    options = self.options
    if banner is None:
      banner = self.cmd
      try:
        sqltags = options.sqltags
      except AttributeError:
        pass
      else:
        banner += f': {sqltags}'
    if local is None:
      local = dict(self.__dict__)
      local.update(options.__dict__)
      local.update(argv=argv, cmd=self.cmd, options=options, self=self)
    try:
      # pylint: disable=import-outside-toplevel
      from bpython import embed
    except ImportError:
      return interact(
          banner=banner,
          local=local,
      )
    return embed(
        banner=banner,
        locals_=local,
    )

BaseCommandSubType = subtype(BaseCommand)

class BaseCommandCmd(Cmd):
  ''' A `cmd.Cmd` subclass used to provide interactive use of a
      command's subcommands.

      The `BaseCommand.cmdloop()` class method instantiates an
      instance of this and calls its `.cmdloop()` method
      i.e. `cmd.Cmd.cmdloop`.
  '''

  @typechecked
  def __init__(self, command: BaseCommandSubType):
    super().__init__()
    self.__command = command

  def get_names(self):
    cmdcls = type(self.__command)
    names = []
    for method_name in dir(cmdcls):
      if method_name.startswith(cmdcls.SUBCOMMAND_METHOD_PREFIX):
        subcmd = cutprefix(method_name, cmdcls.SUBCOMMAND_METHOD_PREFIX)
        names.append('do_' + subcmd)
        ##names.append('help_' + subcmd)
    return names

  def __getattr__(self, attr):
    command = self.__command
    cmdcls = type(command)
    subcmd = cutprefix(attr, 'do_')
    if subcmd is not attr:
      method_name = command.SUBCOMMAND_METHOD_PREFIX + subcmd
      try:
        method = getattr(command, method_name)
      except AttributeError:
        pass
      else:

        def do_subcmd(arg: str):
          argv = shlex.split(arg)
          method(argv)

        do_subcmd.__name__ = attr
        do_subcmd.__doc__ = command.subcommand_usage_text(subcmd)
        return do_subcmd
      if subcmd in ('EOF', 'exit', 'quit'):
        return lambda _: True
    raise AttributeError("%s.%s" % (self.__class__.__name__, attr))

@uses_cmd_options
def vprint(*print_a, options, verbose=None, **print_kw):
  ''' Call `print()` if `options.verbose`.
  '''
  if verbose is None:
    verbose = options.verbose
  if verbose:
    print(*print_a, **print_kw)
