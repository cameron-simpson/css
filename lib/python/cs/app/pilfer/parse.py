#!/usr/bin/env python3

import re
from string import whitespace
from typing import Iterable, Tuple
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.lex import (
    get_decimal_or_float_value,
    get_dotted_identifier,
    get_identifier,
    get_other_chars,
    get_qstr,
    skipwhite,
)
from cs.logutils import (debug, error, exception)
from cs.pfx import Pfx, pfx, pfx_call
from cs.pipeline import StageType
from cs.py.modules import import_module_name
from cs.urlutils import URL

from .format import FormatMapping

# regular expressions used when parsing actions
re_GROK = re.compile(r'([a-z]\w*(\.[a-z]\w*)*)\.([_a-z]\w*)', re.I)

def parse_action(action, do_trace):
  ''' Accept a string `action` and return a `BaseAction` subclass
      instance or a `(sig,function)` tuple.

      This is used primarily by `action_func` below, but also called
      by subparses such as selectors applied to the values of named
      variables.
      Selectors return booleans, all other functions return or yield Pilfers.
  '''
  # save original form of the action string
  action0 = action

  if action.startswith('!'):
    # ! shell command to generate items based off current item
    # receive text lines, stripped
    return ActionShellCommand(action0, action[1:])

  if action.startswith('|'):
    # | shell command to pipe though
    # receive text lines, stripped
    return ActionShellFilter(action0, action[1:])

  # select URLs matching regexp
  # /regexp/
  # named groups in the regexp get applied, per URL, to the variables
  if action.startswith('/'):
    if action.endswith('/'):
      regexp = action[1:-1]
    else:
      regexp = action[1:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      # a regexp with named groups
      def named_re_match(P):
        U = P._
        m = regexp.search(U)
        if m:
          varmap = m.groupdict()
          if varmap:
            P = P.copy_with_vars(**varmap)
          yield P

      return StageType.ONE_TO_MANY, named_re_match
    else:
      return StageType.SELECTOR, lambda P: regexp.search(P._)

  # select URLs not matching regexp
  # -/regexp/
  if action.startswith('-/'):
    if action.endswith('/'):
      regexp = action[2:-1]
    else:
      regexp = action[2:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      raise ValueError(
          "named groups may not be used in regexp rejection patterns"
      )
    return StageType.SELECTOR, lambda P: not regexp.search(P._)

  # parent
  # ..
  if action == '..':
    return StageType.ONE_TO_ONE, pilferify11(lambda P: P._.parent)

  # select URLs ending in particular extensions
  if action.startswith('.'):
    if action.endswith('/i'):
      exts, case = action[1:-2], False
    else:
      exts, case = action[1:], True
    exts = exts.split(',')
    return StageType.SELECTOR, lambda P: has_exts(
        P._, exts, case_sensitive=case
    )

  # select URLs not ending in particular extensions
  if action.startswith('-.'):
    if action.endswith('/i'):
      exts, case = action[2:-2], False
    else:
      exts, case = action[2:], True
    exts = exts.split(',')
    return StageType.SELECTOR, lambda P: not has_exts(
        P._, exts, case_sensitive=case
    )

  # catch "a.b.c" and convert to "grok:a.b.c"
  m = re_GROK.match(action)
  if m:
    action = "grok:" + action

  # collect leading identifier and process with parse
  name, offset = get_identifier(action)
  if not name:
    raise ValueError("unrecognised special action: %r" % (action,))

  # comparison
  # name==
  if action.startswith('==', offset):
    text = action[offset + 2:]

    def compare(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      cmp_value = M.format(text)
      return vvalue == cmp_value

    return StageType.SELECTOR, compare

  # uncomparison
  # name!=
  if action.startswith('!=', offset):
    text = action[offset + 2:]

    def uncompare(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      cmp_value = M.format(text)
      return vvalue != cmp_value

    return StageType.SELECTOR, uncompare

  # contains
  # varname(value,value,...)
  if action.startswith('(', offset):
    args, kwargs, offset = parse_args(action, offset + 1, ')')
    if kwargs:
      raise ValueError(
          "you may not have kw= arguments in the 'contains' value list: %r" %
          (kwargs,)
      )
    values = action[m.end():].split(',')

    def in_list(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      for value in values:
        cvalue = M.format(value)
        if vvalue == cvalue:
          return True
      return False

    return StageType.SELECTOR, in_list

  # assignment
  # varname=
  if action.startswith('=', offset):
    exprtext = action[offset + 1:]

    def assign(P):
      U = P._
      param_value = P.format_string(exprtext, U)
      P2 = P.copy_with_vars(**{param: param_value})
      return P2

    return StageType.ONE_TO_ONE, assign

  # test of variable value
  # varname~selector
  if action.startswith('~', offset):
    selector = _Action(action[offset + 1:])
    if selector.sig != StageType.SELECTOR:
      raise ValueError(
          "expected selector function but found: %s" % (selector,)
      )

    def do_test(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        return False
      return selector(P, vvalue)

    return StageType.SELECTOR, do_test

  if name == 's':
    # s/this/that/
    result_is_Pilfer = False
    if offset == len(action):
      raise ValueError("missing delimiter")
    delim = action[offset]
    delim2pos = action.find(delim, offset + 1)
    if delim2pos < offset + 1:
      raise ValueError("missing second delimiter (%r)" % (delim,))
    regexp = action[offset + 1:delim2pos]
    if not regexp:
      raise ValueError("empty regexp")
    delim3pos = action.find(delim, delim2pos + 1)
    if delim3pos < delim2pos + 1:
      raise ValueError("missing third delimiter (%r)" % (delim,))
    repl_format = action[delim2pos + 1:delim3pos]
    offset = delim3pos + 1
    repl_all = False
    repl_icase = False
    re_flags = 0
    while offset < len(action):
      modchar = action[offset]
      offset += 1
      if modchar == 'g':
        repl_all = True
      elif modchar == 'i':
        repl_icase = True
        re_flags != re.IGNORECASE
      else:
        raise ValueError("unknown s///x modifier: %r" % (modchar,))
    debug(
        "s: regexp=%r, repl_format=%r, repl_all=%s, repl_icase=%s", regexp,
        repl_format, repl_all, repl_icase
    )

    def substitute(P):
      ''' Perform a regexp substitution on the source string.
          `repl_format` is a format string for the replacement text
          using the `str.format` method.
          The matched groups from the regexp take the positional arguments 1..n,
          with 0 used for the whole matched string.
          The keyword arguments consist of '_' for the whole matched string
          and any named groups.
      '''
      src = P._
      debug(
          "SUBSTITUTE: src=%r, regexp=%r, repl_format=%r, repl_all=%s)...",
          src, regexp.pattern, repl_format, repl_all
      )
      strs = []
      offset = 0
      for m in regexp.finditer(src):
        # positional replacements come from the match groups
        repl_args = [m.group(0)] + list(m.groups())
        # named replacements come from the named regexp groups
        repl_kw = {'_': m.group(0)}
        repl_kw.update(m.groupdict())
        # save the unmatched section
        strs.append(src[offset:m.start()])
        # save the matched section with replacements
        strs.append(repl_format.format(*repl_args, **repl_kw))
        offset = m.end()
        if not repl_all:
          break
      # save the final unmatched section
      strs.append(src[offset:])
      result = ''.join(strs)
      debug("SUBSTITUTE: src=%r, result=%r", src, result)
      if isinstance(src, URL):
        result = URL.promote(result)
      return result

    return StageType.ONE_TO_ONE, substitute

  if name in ("copy", "divert", "pipe"):
    # copy:pipe_name[:selector]
    # divert:pipe_name[:selector]
    # pipe:pipe_name[:selector]
    marker = action[offset]
    offset += 1
    pipe_name, offset = get_identifier(action, offset)
    if not pipe_name:
      raise ValueError("no pipe name")
    if offset >= len(action):
      selector = None
    else:
      if action[offset] != marker:
        raise ValueError(
            "expected second marker to match first: expected %r, saw %r" %
            (marker, action[offset])
        )
      selector = _Action(action[offset + 1:])
      if selector.sig != StageType.SELECTOR:
        raise ValueError(
            "expected selector function but found: %s" % (selector,)
        )
    if name == 'copy':

      def copy(self, P):
        if selector is None or selector(P):
          pipe = P.diversion(pipe_name)
          pipe.put(P)
        return P

      return StageType.ONE_TO_ONE, copy
    elif name == 'divert':

      def divert(self, P):
        if selector is None or selector(P):
          pipe = P.diversion(pipe_name)
          pipe.put(P)
        else:
          yield P

      return StageType.ONE_TO_MANY, divert
    elif name == 'pipe':
      return ActionPipeTo(action0, pipe_name)
    else:
      raise NotImplementedError("unhandled action %r" % (name,))

  if name == 'grok' or name == 'grokall':
    # grok:a.b.c.d[:args...]
    # grokall:a.b.c.d[:args...]
    result_is_Pilfer = True
    if offset >= len(action):
      raise ValueError("missing marker")
    marker = action[offset]
    offset += 1
    grokker, offset = get_dotted_identifier(action, offset)
    if '.' not in grokker:
      raise ValueError("no dotted identifier found")
    grok_module, grok_funcname = grokker.rsplit('.', 1)
    if offset >= len(action):
      args, kwargs = (), {}
    elif action[offset] != marker:
      raise ValueError(
          "expected second marker to match first: expected %r, saw %r" %
          (marker, action[offset])
      )
    else:
      args, kwargs, offset = get_action_args(action, offset)
    if offset < len(action):
      raise ValueError("unparsed content after args: %r", action[offset:])
    if name == "grok":

      @typechecked
      def grok(P: Pilfer) -> Pilfer:
        ''' Grok performs a user-specified analysis on the supplied Pilfer state `P`.
            (The current value, often an URL, is `P._`.)
            Import `func_name` from module `module_name`.
            Call `func_name( P, *a, **kw ).
            Receive a mapping of variable names to values in return.
            If not empty, copy P and apply the mapping via which is applied
            with `P.copy_with_vars()`.
            Returns `P` (possibly copied), as this is a one-to-one function.
        '''
        # TODO: use import_name()
        grok_func = P.import_module_func(grok_module, grok_funcname)
        if grok_func is None:
          error("import fails")
        else:
          var_mapping = grok_func(P, *args, **kwargs)
          if var_mapping:
            debug("grok: var_mapping=%r", var_mapping)
            P = P.copy_with_vars(**var_mapping)
        return P

      return StageType.ONE_TO_ONE, grok
    elif name == "grokall":

      @typechecked
      def grokall(Ps: Iterable[Pilfer]) -> Iterable[Pilfer]:
        ''' Grokall performs a user-specified analysis on the `Pilfer` items `Ps`.
            Import `func_name` from module `module_name`.
            Call `func_name( Ps, *a, **kw ).
            Receive a mapping of variable names to values in return,
            which is applied to each item[0] via .copy_with_vars().
            Return the possibly copied `Ps`.
        '''
        if not isinstance(Ps, list):
          Ps = list(Ps)
        if Ps:
          # TODO: use import_name()
          grok_func = pfx_call(
              Ps[0].import_module_func, grok_module, grok_funcname
          )
          if grok_func is None:
            error("import fails: %s.%s", grok_module, grok_funcname)
          else:
            try:
              var_mapping = pfx_call(grok_func, Ps, *args, **kwargs)
            except Exception as e:
              exception("call %s.%s: %s", grok_module, grok_funcname, e)
            else:
              if var_mapping:
                Ps = [P.copy_with_vars(**var_mapping) for P in Ps]
        return Ps

      return StageType.ONE_TO_MANY, grokall
    else:
      raise NotImplementedError("unhandled action %r", name)

  if name == 'for':
    # for:varname=value,...
    # for:varname:{start}..{stop}
    # warning: implies 'per'
    if offset == len(action) or action[offset] != ':':
      raise ValueError("missing colon")
    offset += 1
    varname, offset = get_identifier(action, offset)
    if not varname:
      raise ValueError("missing varname")
    if offset == len(action):
      raise ValueError("missing =values or :start..stop")
    marker = action[offset]
    if marker == '=':
      # for:varname=value,...
      values = action[offset + 1:]

      def for_specific(P):
        U = P._
        # expand "values", split on whitespace, iterate with new Pilfer
        value_list = P.format_string(values, U).split()
        for value in value_list:
          yield P.copy_with_vars(**{varname: value})

      return StageType.ONE_TO_MANY, for_specific
    if marker == ':':
      # for:varname:{start}..{stop}
      start, stop = action[offset + 1:].split('..', 1)

      def for_range(P):
        U = P._
        # expand "values", split on whitespace, iterate with new Pilfer
        istart = int(P.format_string(start, U))
        istop = int(P.format_string(stop, U))
        for value in range(istart, istop + 1):
          yield P.copy_with_vars(**{varname: str(value)})

      return StageType.ONE_TO_MANY, for_range
    raise ValueError("unrecognised marker after varname: %r", marker)

  if name in ('see', 'seen', 'unseen'):
    # see[:seenset,...[:value]]
    # seen[:seenset,...[:value]]
    # unseen[:seenset,...[:value]]
    seensets = ('_',)
    value = '{_}'
    if offset < len(action):
      marker = action[offset]
      seensets, offset = get_other_chars(action, offset + 1, marker)
      seensets = seensets.split(',')
      if not seensets:
        seensets = ('_',)
      if offset < len(action):
        if action[offset] != marker:
          raise ValueError(
              "parse should have a second marker %r at %r" % (action[offset:])
          )
        value = action[offset + 1:]
        if not value:
          value = '{_}'
    if name == 'see':
      func_sig = StageType.ONE_TO_ONE

      def see(P):
        U = P._
        see_value = P.format_string(value, U)
        for seenset in seensets:
          P.see(see_value, seenset)
        return P

      return StageType.ONE_TO_ONE, see
    if name == 'seen':

      def seen(P):
        U = P._
        see_value = P.format_string(value, U)
        return any([P.seen(see_value, seenset) for seenset in seensets])

      return StageType.SELECTOR, seen
    if name == 'unseen':

      def unseen(P):
        U = P._
        see_value = P.format_string(value, U)
        return not any([P.seen(see_value, seenset) for seenset in seensets])

      return StageType.SELECTOR, unseen
    raise NotImplementedError("unsupported action %r", name)

  if name == 'unique':
    # unique
    seen = set()

    def unique(P):
      value = P._
      if value not in seen:
        seen.add(value)
        yield P

    return StageType.ONE_TO_MANY, unique

  if action == 'first':
    is_first = [True]

    def first(P):
      if is_first[0]:
        is_first[0] = False
        return True

    return StageType.SELECTOR, first

  if action == 'new_save_dir':
    # create a new directory based on {save_dir} and update save_dir to match
    def new_save_dir(P):
      return P.copy_with_vars(save_dir=new_dir(P.save_dir))

    return StageType.ONE_TO_ONE, new_save_dir

  # some other function: gather arguments and then look up function by name in mappings
  if offset < len(action):
    marker = action[offset]
    args, kwargs, offset = get_action_args(action, offset + 1)
    if offset < len(action):
      raise ValueError(
          "unparsed text after arguments: %r (found a=%r, kw=%r)" %
          (action[offset:], args, kwargs)
      )
  else:
    args = ()
    kwargs = {}

  if name in many_to_many:
    # many-to-many functions get passed straight in
    sig = StageType.MANY_TO_MANY
    func = many_to_many[name]
  elif name in one_to_many:
    sig = StageType.ONE_TO_MANY
    func = one_to_many[name]
    func.__name__ = "one_to_many[%r]:%s" % (name, func)
  elif name in one_to_one:
    func = one_to_one[name]
    sig = StageType.ONE_TO_ONE
  elif name in one_test:
    func = one_test[name]
    sig = StageType.SELECTOR
  else:
    raise ValueError("unknown action")

  # pretty up lambda descriptions
  if func.__name__ == '<lambda>':
    func.__name__ = '<lambda %r>' % (name,)
  if sig == StageType.ONE_TO_ONE:
    func = pilferify11(func)
  elif sig == StageType.ONE_TO_MANY:
    func = pilferify1m(func)

  if args or kwargs:

    def func_args(*a, **kw):
      a2 = args + a
      kw2 = dict(kwargs)
      kw2.update(kw)
      return func(*a2, **kw2)

    func = func_args
  return sig, func

def get_delim_regexp(s, offset) -> Tuple[re.Pattern, int]:
  ''' Parse a delimited regexp such as /foo/.
  '''
  if offset == len(s):
    raise SyntaxError("missing regexp delimter")
  re_delim = s[offset]
  offset += 1
  end_offset = s.find(re_delim, offset)
  if end_offset == -1:
    raise SyntaxError(f'missing closing regexp delimiter {re_delim!r}')
  assert end_offset > offset
  regexp_s = s[offset:end_offset]
  offset = end_offset + 1
  regexp = pfx_call(re.compile, regexp_s)
  return regexp, offset

def get_action_args(action, offset, delim=None):
  ''' Parse `[[kw=]arg[,[kw=]arg...]` from `action` at `offset`,
      return `(args,kwargs,offset)`.

      Parameters:
      - `action`,`offset`: the action string and the parse offset
      - `delim`: an optional character ending the parse such as a closing bracket

     The following `arg` forms are recognised:
     - a quoted string delimited with `"` or `'`
     - a decimal or floating point number
     - a regular expression between tilde-redelim and redelim
     - a run of characters not including whichspace or comma or `delim`

     Examples:
     - `"foo"`, `'foo'`: quoted strings
     - `1`, `2.3`: numeric values
     - `~/foo/`, `~ :this|[abc/def]:`: regular expressions
     - `blah.snort`: nonwhitespace "bare" string
  '''
  other_chars = ',' + whitespace
  if delim is not None:
    other_chars += delim
  args = []
  kwargs = {}
  while offset < len(action):
    with Pfx("get_action_args(%r)", action[offset:]):
      if delim is not None and action.startswith(delim, offset):
        break
      if action.startswith(',', offset):
        offset += 1
        continue
      # gather leading "kw=" if present
      name, offset1 = get_identifier(action, offset)
      if name and action.startswith('=', offset1):
        kw = name
        offset = offset1 + 1
      else:
        kw = None
      # quoted string
      if action.startswith(('"', "'")):
        arg, offset = get_qstr(action, offset, q=action[offset])
      # numeric value
      elif action[offset:offset + 1].isdigit():
        # TODO: recognise an optional sign
        arg, offset = get_decimal_or_float_value(action, offset)
      # ~ /regexp/
      elif action.startswith('~', offset):
        offset1 = skipwhite(action, offset + 1)
        arg, offset = get_delim_regexp(action, offset1)
      else:
        # nonwhitespace etc
        arg, offset = get_other_chars(action, offset, other_chars)
      if kw is None:
        args.append(arg)
      else:
        kwargs[kw] = arg
  return args, kwargs, offset

def get_name_and_args(
    text: str,
    offset: int = 0,
    delim=None,
) -> tuple[str, list | None, list | None, int]:
  ''' Match a dotted identifier optionally followed by a colon
      and position and keyword arguments.
      Return `('',None,None,offset)` on no match.
      Return `(name,args,kwargs,offset)` on a match.
      '''
  name, offset = get_dotted_identifier(text, offset)
  if not name:
    return name, None, None, offset
  if text.startswith(':', offset):
    offset += 1
    args, kwargs, offset = get_action_args(text, offset, delim)
    if offset < len(text):
      raise ValueError(f'unparsed text after params: {text[offset:]!r}')
  else:
    args = []
    kwargs = {}
  return name, args, kwargs, offset

@pfx
def import_name(module_subname: str):
  ''' Parse a reference to an object from a module, return the object.
      Raise `ImportError` for a module which does not import.
      Raise `NameError` for a name which does not resolve.

      `module_subname` takes the form `*dotted_identifier:dotted_identifier`,
      being the module name and the name within the module respectively.
  '''
  module_name, sub_name = module_subname.split(':', 1)
  name, *sub_names = sub_name.split('.')
  obj = import_module_name(module_name, name)
  for attr in sub_names:
    obj = getattr(obj, attr)
  return obj
