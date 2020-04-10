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
from types import SimpleNamespace as NS
from cs.context import stackattrs
from cs.lex import cutprefix
from cs.logutils import setup_logging, warning, exception
from cs.pfx import Pfx
from cs.resources import RunState

__version__ = '20200318'

DISTINFO = {
    'description':
    "convenience functions for working with the Cmd module and other command line related stuff",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.context', 'cs.lex', 'cs.pfx', 'cs.resources'],
}

def docmd(dofunc):
  ''' Decorator for Cmd subclass methods
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
      except Exception as e:
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
      one instantiates a subclass of BaseCommand:

          class MyCommand(BaseCommand):
            GETOPT_SPEC = 'ab:c'
            USAGE_FORMAT = r"""Usage: {cmd} [-a] [-b bvalue] [-c] [--] arguments...
              -a    Do it all.
              -b    But using bvalue.
              -c    The 'c' option!
            """
          ...
          the_cmd = MyCommand()

      Running a command is done by:

          the_cmd.run(argv)

      The subclass is customised by overriding the following methods:
      * `apply_defaults(options)`:
        prepare the initial state of `options`
        before any command line options are applied
      * `apply_opts(options,opts)`:
        apply the `opts` to `options`.
        `opts` is an `(option,value)` sequence
        as returned by `getopot.getopt`.
      * `cmd_`*subcmd*`(argv,options)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then the method `cmd_`*subcmd*`(argv,options)`
        will be called where `argv` contains the command line arguments
        after *subcmd*.
      * `main(argv,options)`:
        if there are no command line aguments after the options
        or the first argument does not have a corresponding
        `cmd_`*subcmd* method
        then method `main(argv,options)`
        will be called where `argv` contains the command line arguments.
      * `run_context(argv,options,cmd)`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively.
        If the implementation is a `cmd_`*subcmd* method
        then this is called with `cmd=`*subcmd*;
        if the implementation is `main`
        then this is called with `cmd=None`.

      To aid recursive use
      it is intended that all the per command state
      is contained in the `options` object
      and therefore that in typical use
      all of `apply_opts`, `cmd_`*subcmd*, `main` and `run_context`
      should be static methods making no reference to `self`.

      Editorial: why not arparse?
      Primarily because when incorrectly invoked
      an argparse command line prints the help/usage messgae
      and aborts the whole programme with `SystemExit`.
  '''

  SUBCOMMAND_METHOD_PREFIX = 'cmd_'

  @classmethod
  def add_usage_to_docstring(cls):
    ''' Append `cls.USAGE_FORMAT` to `cls.__doc__`
        with format substitutions.
    '''
    format_kwargs = dict(getattr(cls, 'USAGE_KEYWORDS', {}))
    if 'cmd' not in format_kwargs:
      format_kwargs['cmd'] = cls.__name__
    cls.__doc__ += '\n\nCommand line usage:\n\n    ' + cls.USAGE_FORMAT.format_map(
        format_kwargs
    ).replace('\n', '\n    ')

  def apply_defaults(self, options):
    ''' Stub `apply_defaults` method.

        Subclasses can override this to set up the initial state of `options`.
    '''

  def run(self, argv=None, options=None, cmd=None):
    ''' Run a command from `argv`.
        Returns the exit status of the command.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          optional command line arguments
          including the main command name if `cmd` is not specified.
          The default is `sys.argv`.
          The contents of `argv` are copied,
          permitting desctructive parsing of `argv`.
        * `options`:
          a object for command state and context.
          If not specified a new `SimpleNamespace`
          is allocated for use as `options`,
          and prefilled with `.cmd` set to `cmd`
          and other values as set by `.apply_default(options)`
          if such a method is provided.
        * `cmd`:
          optional command name for context;
          if this is not specified it is taken from `argv.pop(0)`.

        The command line arguments are parsed according to `getopt_spec`.
        If `getopt_spec` is not empty
        then `apply_opts(opts,options)` is called
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
      argv = list(sys.argv)
      if cmd is not None:
        # we consume the first argument anyway
        argv.pop(0)
    else:
      argv = list(argv)
    if cmd is None:
      cmd = basename(argv.pop(0))
    setup_logging(cmd)
    # post: argv is list of arguments after the command name
    usage_format = getattr(self, 'USAGE_FORMAT')
    # TODO: is this valid in the case of an already formatted usage string
    if usage_format:
      usage_kwargs = dict(getattr(self, 'USAGE_KEYWORDS', {}))
      usage_kwargs['cmd'] = cmd
      usage = usage_format.format_map(usage_kwargs)
    else:
      usage = None
    if options is None:
      options = NS(cmd=cmd, usage=usage)
      self.apply_defaults(options)
    # we catch GetoptError from this suite...
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # we do this regardless in order to honour --
      opts, argv = getopt(argv, getopt_spec, '')
      if getopt_spec:
        self.apply_opts(opts, options)

      subcmd_prefix = self.SUBCOMMAND_METHOD_PREFIX
      subcmd_names = list(
          map(
              lambda attr: cutprefix(attr, subcmd_prefix),
              (attr for attr in dir(self) if attr.startswith(subcmd_prefix))
          )
      )
      if subcmd_names:
        # look for a subcommand
        if not argv:
          raise GetoptError(
              "missing subcommand, expected one of %r" %
              (sorted(subcmd_names))
          )
        subcmd = argv.pop(0)
        subcmd_attr = subcmd_prefix + subcmd
        try:
          main = getattr(self, subcmd_attr)
        except AttributeError:
          raise GetoptError(
              "%s: unrecognised subcommand, expected one of: %r" %
              (subcmd, sorted(subcmd_names))
          )
        if isinstance(main, BaseCommand):

          def run_main(argv, options):
            ''' Invoke the run method of an instance of the subcommand class.
            '''
            return main().run(argv, options=options, cmd=subcmd)

          main = run_main
      else:
        subcmd = cmd
        try:
          main = self.main
        except AttributeError:
          raise GetoptError(
              "no main method and no %s* subcommand methods" %
              (subcmd_prefix,)
          )
      with RunState(cmd) as runstate:
        with stackattrs(options, cmd=subcmd, runstate=runstate):
          with self.run_context(argv, options):
            with Pfx(subcmd):
              return main(argv, options)
    except GetoptError as e:
      handler = getattr(self, 'getopt_error_handler')
      if handler and handler(cmd, options, e, usage):
        return 2
      raise

  @staticmethod
  def getopt_error_handler(cmd, options, e, usage):
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
  def run_context(argv, options):
    ''' Stub context manager which surrounds `main` or `cmd_`*subcmd*.
    '''
    # redundant try/finally to remind subclassers of correct structure
    try:
      yield
    finally:
      pass
