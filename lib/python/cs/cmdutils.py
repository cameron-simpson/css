#!/usr/bin/python
#
# Command line stuff. - Cameron Simpson <cs@cskk.id.au> 03sep2015
#

''' Convenience functions for working with the Cmd module
    and other command line related stuff.
'''

from __future__ import print_function, absolute_import
from contextlib import contextmanager
from getopt import getopt, GetoptError
from os.path import basename
import sys
from types import SimpleNamespace
from cs.context import stackattrs
from cs.deco import cachedmethod
from cs.gimmicks import nullcontext
from cs.lex import cutprefix, stripped_dedent
from cs.logutils import setup_logging, warning, exception
from cs.pfx import Pfx
from cs.py.doc import obj_docstring
from cs.resources import RunState

__version__ = '20210404'

DISTINFO = {
    'description':
    "a `BaseCommand` class for constructing command lines,"
    " some convenience functions for working with the `cmd` module,"
    " and some other command line related stuff",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context', 'cs.deco', 'cs.gimmicks', 'cs.lex', 'cs.logutils',
        'cs.pfx', 'cs.py.doc', 'cs.resources'
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

  def wrapped(self, *a, **kw):
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

  wrapped.__name__ = '@docmd(%s)' % (funcname,)
  wrapped.__doc__ = dofunc.__doc__
  return wrapped

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

      Running a command is done by:

          MyCommand(argv).run()

      Modules which implement a command line mode generally look like:

          ... imports etc ...
          def main(argv=None):
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
      should implement a `cmd_`*subcommand*`(argv)` method
      for each subcommand.
      If there is a paragraph in the method docstring
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
        if there are no command line aguments after the options
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
  OPTIONS_CLASS = SimpleNamespace

  def __init_subclass__(cls):
    ''' Update subclasses of `BaseCommand`.

        Appends the usage message to the class docstring.
    '''
    usage_message = cls.usage_text()
    cls.__doc__ += (
        '\n\nCommand line usage:\n\n    ' +
        usage_message.replace('\n', '\n    ')
    )

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def __init__(self, argv=None, *, cmd=None, **kw_options):
    ''' Initialise the command line.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          optional command line arguments
          including the main command name if `cmd` is not specified.
          The default is `sys.argv`.
          The contents of `argv` are copied,
          permitting desctructive parsing of `argv`.
        * `options`:
          a optional object for command state and context.
          If not specified a new `SimpleNamespace`
          is allocated for use as `options`,
          and prefilled with `.cmd` set to `cmd`
          and other values as set by `.apply_defaults()`
          if such a method is provided.
        * `cmd`:
          optional command name for context;
          if this is not specified it is taken from `argv.pop(0)`.
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
    self._run = None  # becomes the run state later if no GetoptError
    self._printed_usage = False
    self.options = self.OPTIONS_CLASS()
    if argv is None:
      argv = list(sys.argv)
      if cmd is not None:
        # consume the first argument anyway
        argv.pop(0)
    else:
      argv = list(argv)
    if cmd is None:
      cmd = basename(argv.pop(0))
    log_level = getattr(self.options, 'log_level', None)
    loginfo = setup_logging(cmd, level=log_level)
    # post: argv is list of arguments after the command name
    self.cmd = cmd
    self.usage = self.usage_text(cmd=cmd)
    self.loginfo = loginfo
    self.apply_defaults()
    # override the default options
    for option, value in kw_options.items():
      setattr(self.options, option, value)
    # we catch GetoptError from this suite...
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # we do this regardless in order to honour '--'
      opts, argv = getopt(argv, getopt_spec, '')
      if getopt_spec:
        self.apply_opts(opts)  # pylint: disable=no-member

      # now prepare:
      # * a callable `main` accepting `argv`
      # * the remaining arguments `main_argv`
      # * a context manager `main_context` for use around `run_context`
      subcmds = self.subcommands()
      if subcmds and list(subcmds) != ['help']:
        # expect a subcommand on the command line
        if not argv:
          raise GetoptError(
              "missing subcommand, expected one of: %s" %
              (', '.join(sorted(subcmds.keys())),)
          )
        subcmd = argv.pop(0)
        subcmd_ = subcmd.replace('-', '_')
        try:
          main = getattr(self, self.SUBCOMMAND_METHOD_PREFIX + subcmd_)
        except AttributeError:
          # pylint: disable=raise-missing-from
          raise GetoptError(
              "%s: unrecognised subcommand, expected one of: %s" % (
                  subcmd,
                  ', '.join(sorted(subcmds.keys())),
              )
          )
        try:
          main_is_class = issubclass(main, BaseCommand)
        except TypeError:
          main_is_class = False
        if main_is_class:
          subcmd_cls = main
          main = subcmd_cls(argv, cmd=subcmd).run
        main_cmd = subcmd
        main_context = Pfx(subcmd)
      else:
        try:
          main = self.main
        except AttributeError:
          raise GetoptError("no main method and no subcommand methods")  # pylint: disable=raise-missing-from
        main_cmd = cmd
        main_context = nullcontext()
      # stash for use by .run()
    except GetoptError as e:
      handler = getattr(self, 'getopt_error_handler')
      if handler and handler(cmd, self.options, e, self.usage):
        self._printed_usage = True
        return
      raise
    else:
      self._run = main, main_cmd, argv, main_context

  @classmethod
  def subcommands(cls):
    ''' Return a mapping of subcommand names to class attributes
        for attributes which commence with `cls.SUBCOMMAND_METHOD_PREFIX`
        by default `'cmd_'`.
    '''
    prefix = cls.SUBCOMMAND_METHOD_PREFIX
    return {
        cutprefix(attr, prefix): getattr(cls, attr)
        for attr in dir(cls)
        if attr.startswith(prefix)
    }

  @classmethod
  @cachedmethod
  def usage_text(cls, *, cmd=None, format_mapping=None):
    ''' Compute the "Usage:" message for this class
        from the top level `USAGE_FORMAT`
        and the `'Usage:'`-containing docstrings
        from its `cmd_*` methods.

        This is a cached method because it tries to update the
        method docstrings after formatting, which is bad if it
        happens more than once.
    '''
    if cmd is None:
      cmd = cls.__name__
    if format_mapping is None:
      format_mapping = {}
    if cmd is not None or 'cmd' not in format_mapping:
      format_mapping['cmd'] = cls.__name__ if cmd is None else cmd
    usage_format_mapping = dict(getattr(cls, 'USAGE_KEYWORDS', {}))
    usage_format_mapping.update(format_mapping)
    usage_format = getattr(cls, 'USAGE_FORMAT', None)
    subcmds = cls.subcommands()
    has_subcmds = subcmds and list(subcmds) != ['help']
    if usage_format is None:
      usage_format = (
          r'Usage: {cmd} subcommand [...]'
          if has_subcmds else 'Usage: {cmd} [...]'
      )
    usage_message = usage_format.format_map(usage_format_mapping)
    if has_subcmds:
      subusages = []
      for attr in sorted(subcmds):
        with Pfx(attr):
          subusage = cls.subcommand_usage_text(
              attr, usage_format_mapping=usage_format_mapping
          )
          if subusage:
            subusages.append(subusage.replace('\n', '\n  '))
      if subusages:
        usage_message = '\n'.join(
            [usage_message, '  Subcommands:'] + [
                '    ' + subusage.replace('\n', '\n    ')
                for subusage in subusages
            ]
        )
    return usage_message

  @classmethod
  def subcommand_usage_text(
      cls, subcmd, fulldoc=False, usage_format_mapping=None
  ):
    ''' Return the usage text for a subcommand.

        Parameters:
        * `subcmd`: the subcommand name
        * `fulldoc`: if true (default `False`)
          return the full docstring with the Usage section expanded
          otherwise just return the Usage section.
    '''
    method = cls.subcommands()[subcmd]
    subusage = None
    try:
      classy = issubclass(method, BaseCommand)
    except TypeError:
      classy = False
    if classy:
      subusage = method.usage_text(cmd=subcmd)
    else:
      doc = obj_docstring(method)
      if doc and 'Usage:' in doc:
        pre_usage, post_usage = doc.split('Usage:', 1)
        pre_usage = pre_usage.strip()
        post_usage_parts = post_usage.split('\n\n', 1)
        post_usage_format = post_usage_parts.pop(0)
        subusage_format = stripped_dedent(post_usage_format)
        if subusage_format:
          mapping = dict(sys.modules[method.__module__].__dict__)
          if usage_format_mapping:
            mapping.update(usage_format_mapping)
          mapping.update(cmd=subcmd)
          subusage = subusage_format.format_map(mapping)
          if fulldoc:
            parts = [pre_usage, subusage] if pre_usage else [subusage]
            parts.extend(post_usage_parts)
            subusage = '\n\n'.join(parts)
    return subusage if subusage else None

  def apply_defaults(self):
    ''' Stub `apply_defaults` method.

        Subclasses can override this to set up the initial state of `self.options`.
    '''

  def apply_opt(self, opt, val):
    ''' Handle a individual global command line option.
    '''
    raise RuntimeError("unhandled option %r" % (opt,))

  def apply_opts(self, opts):
    ''' Apply command line options.
    '''
    options = self.options
    for opt, val in opts:
      with Pfx(opt):
        self.apply_opt(opt, val)

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def run(self):
    ''' Run a command.
        Returns the exit status of the command.
        May raise `GetoptError` from subcommands.

        If the first command line argument *foo*
        has a corresponding method `cmd_`*foo*
        then that argument is removed from the start of `argv`
        and `self.cmd_`*foo*`(cmd=`*foo*`)` is called
        and its value returned.
        Otherwise `self.main(argv)` is called
        and its value returned.

        If the command implementation requires some setup or teardown
        then this may be provided by the `run_context`
        context manager method,
        called with `cmd=`*subcmd* for subcommands
        and with `cmd=None` for `main`.
    '''
    options = self.options
    try:
      if self._run is None:
        main_cmd = self.cmd  # used in "except" below
        raise GetoptError("bad invocation")
      main, main_cmd, main_argv, main_context = self._run
      upd_context = self.loginfo.upd
      if upd_context is None:
        upd_context = nullcontext()
      with RunState(main_cmd) as runstate:
        with upd_context:
          with stackattrs(
              options,
              cmd=main_cmd,
              runstate=runstate,
              upd=self.loginfo.upd,
          ):
            with self.run_context():
              with main_context:
                return main(main_argv)
    except GetoptError as e:
      handler = getattr(self, 'getopt_error_handler')
      if handler and handler(main_cmd, options, e,
                             None if self._printed_usage else self.usage):
        return 2
      raise

  # pylint: disable=unused-argument
  @staticmethod
  def getopt_error_handler(cmd, options, e, usage):  # pylint: disable=unused-argument
    ''' The `getopt_error_handler` method
        is used to control the handling of `GetoptError`s raised
        during the command line parse
        or during the `main` or `cmd_`*subcmd*` calls.

        The handler is called with these parameters:
        * `cmd`: the command name
        * `options`: the `options` object
        * `e`: the `GetoptError` exception
        * `usage`: the command usage or `None` if this was not provided

        It returns a true value if the exception is considered handled,
        in which case the main `run` method returns 2.
        It returns a false value if the exception is considered unhandled,
        in which case the main `run` method reraises the `GetoptError`.

        This default handler prints an error message to standard error,
        prints the usage message (if specified) to standard error,
        and returns `True` to indicate that the error has been handled.

        To let the exceptions out unhandled
        this can be overridden with a method which just returns `False`
        or even by setting the `getopt_error_handler` attribute to `None`.

        Otherwise,
        the handler may perform any suitable action
        and return `True` to contain the exception
        or `False` to cause the exception to be reraised.
    '''
    print("%s: %s" % (cmd, e), file=sys.stderr)
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
  def cmd_help(cls, argv, options):  # pylint: disable=unused-argument
    ''' Usage: {cmd} [subcommand-names...]
          Print the help for the named subcommands,
          or for all subcommands if no names are specified.
    '''
    subcmds = cls.subcommands()
    if argv:
      fulldoc = True
    else:
      fulldoc = False
      argv = sorted(subcmds)
    xit = 0
    print("help:")
    for subcmd in argv:
      with Pfx(subcmd):
        if subcmd not in subcmds:
          warning("unknown subcommand")
          xit = 1
          continue
        subusage = cls.subcommand_usage_text(subcmd, fulldoc=fulldoc)
        if not subusage:
          warning("no help")
          xit = 1
          continue
        print(' ', subusage.replace('\n', '\n    '))
    return xit
