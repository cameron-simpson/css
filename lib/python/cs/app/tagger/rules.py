#!/usr/bin/env python3

''' Parser for tagger rules.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from dataclasses import dataclass, field
from functools import partial
from os.path import (
    abspath,
    basename,
    dirname,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
)
import re
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from icontract import ensure, require
from typeguard import typechecked

from cs.deco import decorator, promote, Promotable
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, TaggedPath, uses_fstags
from cs.hashindex import merge
from cs.lex import (
    get_dotted_identifier,
    get_qstr,
    is_identifier,
    skipwhite,
)
from cs.logutils import ifverbose, warning
from cs.obj import public_subclasses
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.queues import ListQueue
from cs.tagset import Tag, TagSet
from cs.upd import print

from cs.debug import X, trace, r, s

RULE_MODES = 'move', 'tag'

def slosh_quote(s: str, q: str):
  ''' Quote a string `s` with quote character `q`.
  '''
  return q + s.replace('\\', '\\\\').replace(q, '\\' + q)

@decorator
def pops_tokens(func):
  ''' Decorator to save the current tokens on entry and to restore
      them if an exception is raised.
  '''

  @typechecked
  def pops_token_wrapper(tokens: List["_Token"]):
    tokens0 = tokens[:]
    try:
      return func(tokens)
    except:  # pylint: disable=
      tokens[:] = tokens0
      raise

  return pops_token_wrapper

@dataclass
class TagChange:
  ''' A change to a `Tag`.

      If `add_remove` is true, add the `tag`.
      If false, remove the `tag`; the `tag.vaue` should be `None`.
  '''
  add_remove: bool
  tag: Tag

@dataclass
class RuleResult:
  ''' The result of applying a `Rule`.
  '''
  rule: "Rule"
  matched: bool
  # tag modifications
  tag_changes: List[TagChange] = field(default_factory=list)
  # places the source file was filed to
  filed_to: List[str] = field(default_factory=list)
  # exceptions running the action
  failed: List[Exception] = field(default_factory=list)

@dataclass
class _Token(Promotable):
  ''' Base class for tokens.
  '''

  source_text: str
  offset: int
  end_offset: int

  def __str__(self):
    return self.matched_text

  @property
  def matched_text(self):
    ''' The text from `self.source_text` which matches this token.
    '''
    return self.source_text[self.offset:self.end_offset]

  @pfx_method
  def parse(cls, text: str, offset: int = 0) -> "_Token":
    ''' Parse a token from `test` at `offset` (default `0`).
        Return a `_Token` subclass instance.
        Raise `SyntaxError` if no subclass parses it.

        This base class method attempts the `.parse` method of all
        the public subclasses.
    '''
    token_classes = public_subclasses(cls)
    if not token_classes:
      raise RuntimeError("no public subclasses")
    for subcls in token_classes:
      print("parse: try", subcls, "parse", repr(text))
      try:
        return subcls.parse(text, offset=offset)
      except SyntaxError as e:
        warning("%s.parse: %s", subcls.__name__, e)
    raise SyntaxError(
        'no subclass.parse succeeded,'
        f'tried {",".join(subcls.__name__ for subcls in token_classes)}'
    )

  @classmethod
  def pop(cls, text: str, offset: int = 0) -> "_Token":
    ''' Pop a token from `text` at `offset` (default `0`).
        This method skips any leading whitespace on `text` at `offset`.
    '''
    start_offset = skipwhite(text, offset)
    return cls.parse(text, start_offset)

  @classmethod
  @pfx_method
  @typechecked
  def from_str(cls, text: str) -> "_Token":
    ''' Parse `test` as a token of type `cls`, return the token.
        Raises `SyntaxError` on a parse failure.
        This is a wrapper for the `parse` class method.
    '''
    token = cls.parse(text)
    if token.end_offset != len(text):
      raise SyntaxError(
          f'unparsed text at offset {token.end_offset}:'
          f' {text[token.end_offset:]!r}'
      )
    return token

@dataclass
class Identifier(_Token):
  ''' A dotted identifier.
  '''

  name: str

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a dotted identifier from `test`.
    '''
    name, end_offset = get_dotted_identifier(text, offset)
    if not name:
      raise SyntaxError(
          f'{offset}: expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    return cls(
        source_text=text, offset=offset, end_offset=end_offset, name=name
    )

class _LiteralValue(_Token):
  value: Any

@dataclass
class NumericValue(_LiteralValue):
  value: Union[int, float]

  # anything this matches should be a valid Python int/float
  _token_re = re.compile(r'[-+]?\d+(\.\d*([eE]-?\d+)?)?')

  def __str__(self):
    return str(self.value)

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a Python style `int` or `float`.
    '''
    start_offset = skipwhite(text, offset)
    m = cls._token_re.match(text, start_offset)
    if not m:
      raise SyntaxError(
          f'{start_offset}: expected int or float, found {text[start_offset:start_offset+16]!r}'
      )
    try:
      value = int(m.group())
    except ValueError:
      value = float(m.group())
    return cls(
        source_text=text, offset=offset, end_offset=m.end(), value=value
    )

@dataclass
class QuotedString(_LiteralValue):
  ''' A double quoted string.
  '''

  value: str
  quote: str = '"'

  def __str__(self):
    return slosh_quote(self.value, self.quote)

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> "_Token":
    ''' Parse a double quoted string from `text`.
    '''
    if not text.startswith('"', offset):
      raise SyntaxError(
          f'{offset}: expected ", found {text[offset:offset+1]!r}'
      )
    q = text[offset]
    value, end_offset = get_qstr(text, offset)
    return cls(
        source_text=text,
        offset=offset,
        end_offset=end_offset,
        value=value,
        quote=q,
    )

@dataclass
class TagAddRemove(_Token):
  tag_name: str
  tag_expression: Union[QuotedString, Identifier, None]
  add_remove: bool = True

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    offset0 = offset
    # leading + or -
    if text.startswith('-', offset):
      add_remove = False
    elif text.startswith('+', offset):
      add_remove = True
    else:
      raise SyntaxError(f'expected + or -, got: {text[offset:offset+1]!r}')
    offset += 1
    # tag name
    name, offset = get_dotted_identifier(text, offset)
    if not name:
      raise SyntaxError(
          f'expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    # tag value
    if text.startswith("=", offset):
      if not add_remove:
        raise ValueError(f'{offset}: unexpected assignment following -{name}')
      offset += 1
      if text.startswith('"', offset):
        expression = QuotedString.parse(text, offset)
      else:
        expression = Identifier.parse(text, offset)
      end_offset = expression.end_offset
    else:
      expression = None
      end_offset = offset
    return cls(
        source_text=text,
        offset=offset0,
        end_offset=end_offset,
        tag_name=name,
        tag_expression=expression,
        add_remove=add_remove,
    )

class _Comparison(_Token, ABC):
  ''' Abstract base class for comparisons.
  '''

  @abstractmethod
  def __call__(self, value, tags):
    raise NotImplementedError

@dataclass
class EqualityComparison(_Comparison):
  ''' A comparison of some value for equality.
  '''

  reference_value: Any

  OP_SYMBOL = '=='

  @classmethod
  @typechecked
  def parse(cls, text: str, offset: int = 0) -> "_Token":
    ''' Match a string or numeric value in `text` at `offset`.
    '''
    if not text.startswith('==', offset):
      raise SyntaxError(
          f'{offset}: expected "==", found {text[offset:offset+2]!r}'
      )
    qs = QuotedString.pop(text, offset + 2)
    return cls(
        source_text=text,
        offset=offset,
        end_offset=qs.end_offset,
        reference_value=qs.value
    )

  @typechecked
  def __call__(
      self, comparison_value: Union[str, int, float], tags: TagSet
  ) -> bool:
    ''' Test `comparison_value` for equality `self.reference_value` value.

        If the reference value is numeric we will promote a string
        comparison to its numeric value via `int()` or `float()`;
        invalid numeric strings issue a warning and return `False`.

        If the reference value is a string, return `False` if the comparison
        value is not a string.

        Otherwise compare using `==`.
    '''
    with Pfx("%s(%s %s %s)", r(comparison_value), self.OP_SYMBOL,
             r(self.reference_value)):
      value = self.reference_value
      # promote the comparison value to match the reference value
      if isinstance(value, str):
        if not isinstance(comparison_value, str):
          warning("cannot compare nonstring to string")
          return False
      elif isinstance(value, (int, float)):
        if isinstance(comparison_value, str):
          try:
            comparison_value = int(comparison_value)
          except ValueError:
            try:
              comparison_value = float(comparison_value)
            except ValueError:
              warning("cannot convert %s to int or float", r(comparison_value))
              return False
      else:
        raise RuntimeError(f'unhandled reference_value {r(value)}')
      return comparison_value == value

@dataclass
class RegexpComparison(_Comparison):
  ''' A comparison of some string using a regular expression.
      Return is `None` on no omatch or the `Match.groupdict()` on a match.
  '''

  regexp: re.Pattern
  delim: str

  # supported delimiters for regular expressions
  REGEXP_DELIMS = '/:!|'

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> "_Token":
    offset0 = offset
    if not text.startswith('~', offset):
      raise SyntaxError(
          f'{offset}: expected "~", found {text[offset:offset+1]!r}'
      )
    offset = skipwhite(text, offset + 1)
    if not text.startswith(tuple(cls.REGEXP_DELIMS), offset):
      raise SyntaxError(
          f'{offset}: expected regular expression delimited by one of {cls.REGEXP_DELIMS!r}'
      )
    delim = text[offset]
    offset += 1
    endpos = text.find(delim, offset)
    if endpos < 0:
      raise SyntaxError(
          f'{offset}: count not find closing delimiter {delim!r} for regular expression'
      )
    re_s = text[offset:endpos]
    end_offset = endpos + 1
    regexp = pfx_call(re.compile, re_s)
    return cls(
        source_text=text,
        offset=offset0,
        end_offset=end_offset,
        regexp=regexp,
        delim=delim
    )

  def __call__(self, value: str, tags: TagSet) -> dict:
    m = self.regexp.search(value)
    if not m:
      return None
    return m.groupdict()

class _Action:

  @classmethod
  @typechecked
  def parse(cls, tokens: List[_Token]):
    if not tokens:
      raise SyntaxError('tokens is empty')
    for subcls in public_subclasses(cls):
      with Pfx(subcls.__name__):
        try:
          return pops_tokens(subcls.parse)(tokens)
        except SyntaxError as e:
          warning("skip: %s", e)
          pass
    raise SyntaxError(
        f'no {cls.__name__} subclass matched, tried {",".join(subcls.__name__ for subcls in public_subclasses(cls))}'
    )

  @abstractmethod
  @typechecked
  def __call__(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit=False,
      quiet=False,
      fstags: FSTags,
  ):
    ''' Perform this action on `fspath` and `tags`.
    '''
    raise NotImplementedError

@dataclass
class MoveAction(_Action):
  ''' An action to move a file.
  '''

  MODES = ('move',)

  target_format: str

  @classmethod
  @typechecked
  def parse(cls, tokens: List[_Token]) -> "MoveAction":
    ''' Parse a `mv` action from a list of tokens.
    '''
    token0 = tokens.pop(0)
    match token0:
      case Identifier(name="mv"):
        target_token = tokens.pop(0)
        match target_token:
          case QuotedString():
            target_format = target_token.value
            return cls(target_format=target_token.value)
          case _:
            raise SyntaxError(f'expected a quoted string after {action_token}')
      case _:
        raise SyntaxError(f'expected "mv", found {token0}')

  @uses_fstags
  def __call__(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit=False,
      quiet=False,
      fstags: FSTags,
  ) -> Tuple[str, ...]:
    ''' Move `fspath` to `self.target_format`, return the new fspath.
    '''
    target_fspath = expanduser(tags.format_as(self.target_format))
    if not isabspath(target_fspath):
      target_fspath = joinpath(dirname(fspath), target_fspath)
    if target_fspath.endswith('/'):
      target_fspath = joinpath(target_fspath, basename(fspath))
    target_dirpath = dirname(target_fspath)
    if doit and not isdirpath(target_dirpath):
      needdir(target_dirpath, use_makedirs=False)
    merge(
        fspath,
        target_fspath,
        hashname=hashname,
        move_mode=True,
        symlink_mode=False,
        doit=doit,
        quiet=quiet,
    )
    return (target_fspath,)

@dataclass
class TagAction(_Action):
  ''' An action to move a file.
  '''

  MODES = ('tag',)

  tag_tokens: List[TagAddRemove]

  @classmethod
  @typechecked
  def parse(cls, tokens: List[_Token]) -> "TagAction":
    ''' Parse a `tag` action from a list of tokens.
    '''
    token0 = tokens.pop(0)
    match token0:
      case Identifier(name="tag"):
        tag_tokens = []
        while tokens and isinstance(tokens[0], TagAddRemove):
          tag_tokens.append(tokens.pop(0))
        if not tag_tokens:
          raise SyntaxError(f'no tag changes after "{token0}"')
        return cls(tag_tokens=tag_tokens)
      case _:
        raise SyntaxError(f'expected "tag", found {r(token0)} {token0}')

  @uses_fstags
  def __call__(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit=False,
      quiet=False,
      fstags: FSTags,
  ) -> Tuple[TagChange, ...]:
    ''' Apply `self.tag_tokens` to `tags`.
        Return a tuple of the applied `TagChange`s.
    '''
    tag_changes = []
    for tag_token in self.tag_tokens:
      if tag_token.add_remove:
        tags.add(tag_token.tag, verbose=not quiet)
      else:
        tags.discard(tag_token.tag.name, verbose=not quiet)
      tag_changes.append(
          TagChange(add_remove=tag_token.add_remove, tag=tag_token.tag)
      )
    return tuple(tag_changes)

Action = Union[str, Tuple[bool, Tag]]

class Rule(Promotable):
  ''' A tagger rule.
  '''

  @require(lambda match_attribute: is_identifier(match_attribute))
  @typechecked
  def __init__(
      self,
      definition: str,
      match_attribute: str,
      match_test: Callable[[str, TagSet], dict],
      action: Union[None, Callable[..., Iterable[Action]]],
      *,
      quick=False,
      filename=None,
      lineno=None,
  ):
    self.definition = definition
    self.match_attribute = match_attribute
    self.match_test = match_test
    self.action = action
    self.quick = quick
    self.filename = filename
    self.lineno = lineno

  def __str__(self):
    return self.definition

  # TODO: repr should do what str does now
  def __repr__(self):
    filename = self.filename
    lineno = self.lineno
    return ''.join(
        filter(
            None, (
                f'{self.__class__.__name__}'
                '(',
                (
                    ('' if filename is None else f'{shortpath(filename)}:')
                    if lineno is None else (
                        f'{lineno}:' if filename is None else
                        f'{shortpath(filename)}:{lineno}:'
                    )
                ),
                f'{self.definition!r},',
                f'{self.match_attribute},',
                f'match_test={self.match_test!r},',
                f'action={self.action},',
                self.quick and f'quick={self.quick},'
                ')',
            )
        )
    )

  @uses_fstags
  @typechecked
  def apply(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit: bool = False,
      quiet: bool = False,
      modes=RULE_MODES,
      fstags: FSTags,
  ) -> RuleResult:
    ''' Apply this `Rule` to `fspath` using the working `TagSet` `tags`,
        typically the inherited tags of `fspath`.
        On no match return `False`.
        On a match, return an iterable of side effects, each of which may be:
        * `str`: a new value for the fspath indicating a move or link
        * `(bool,Tag)`: a 2 tuple of an "add_remove" bool and `Tag`
    '''
    result = RuleResult(rule=self, matched=False)
    test_s = self.get_attribute_value(fspath, tags, self.match_attribute)
    if test_s is None:
      # attribute unavailable
      return result
    if not isinstance(test_s, str):
      raise TypeError(
          f'expected str for {self.match_attribute!r} but got: {s(test_s)}'
      )
    match_result = self.match_test(test_s, tags)
    if match_result is None:
      return result
    if match_result is False:
      return result
    result.matched = True
    if 'tag' in modes:
      if isinstance(match_result, Mapping):
        tags.update(match_result)
        for k, v in match_result.items():
          result.tag_changes.append(TagChange(add_remove=True, tag=Tag(k, v)))
    if self.action is not None:
      with Pfx(self.action.__doc__.strip().split()[0].strip()):
        if not (set(self.action.modes) & set(modes)):
          ##warning(
          ##    "SKIP action with unwanted modes %r: %s", self.action.modes,
          ##    self.action
          ##)
          pass
        else:
          # apply the current tags in case the file gets moved
          fstags[fspath].update(tags)
          try:
            side_effects = self.action(
                fspath,
                tags,
                hashname=hashname,
                doit=doit,
                quiet=quiet,
            )
          except Exception as e:
            warning("action failed: %s", e)
            result.failed.append(e)
          else:
            for side_effect in side_effects:
              match side_effect:
                case str(new_fspath):
                  result.filed_to.append(new_fspath)
                case TagChange() as tag_change:
                  result.tag_changes.append(tag_change)
                case _:
                  raise NotImplementedError(
                      f'unsupported side effect {r(side_effect)}'
                  )
    return result

  @typechecked
  def get_attribute_value(
      self, fspath: str, tags: TagSet, attribute_name: str
  ):
    ''' Given the filesystem path `fspath` and the working `TagSet` `tags`,
        return the value indicated by `attribute_name`.

        The following attributes are predefined:
        * `basename`: the basename of `fspath`
        * `fspath`: the absolute path of `fspath`
        Other names are used as tag names in `tags`.

        `None` is returned for an unknown name.
    '''
    try:
      # predefined
      func = {
          'basename': partial(basename, fspath),
          'fspath': partial(abspath, fspath),
      }[attribute_name]
    except KeyError:
      return tags.get(attribute_name)
    return func()

  # TODO: should be class method of _Token
  @classmethod
  @typechecked
  def get_token(cls, rule_s: str, offset: int = 0) -> _Token:
    ''' Parse a token from `rule_s` at `offset`.
        This skips any leading whitespace.
        Raise `EOFError` at the end of the string or at a comment.
        Raise `SyntaxError` if no token is recognised.
    '''
    offset = skipwhite(rule_s, offset)
    if offset == len(rule_s) or rule_s.startswith(('#', '//'), offset):
      # end of string or comment -> end of tokens
      raise EOFError
    for token_type in public_subclasses(_Token):
      try:
        return token_type.parse(rule_s, offset)
      except SyntaxError as e:
        continue
    raise SyntaxError(f'no token recognised at: {rule_s[offset:]!r}')

  @classmethod
  def tokenise(cls, rule_s: str, offset: int = 0):
    ''' Generator yielding `_Token`s.
    '''
    while True:
      try:
        token = cls.get_token(rule_s, offset)
      except EOFError:
        return
      offset = token.end_offset
      yield token

  @classmethod
  def from_str(
      cls,
      rule_s: str,
      filename=None,
      lineno: int = None,
  ) -> Union["Rule", None]:
    ''' Parse `rule_s` as a text definition of a `Rule`.

        Syntax:

            match [quick] [attribute] match-op [action]
            drop [quick] [attribute] match-op
            do [quick] action

        Match ops:

            attribute ~ /regexp/
            attribute == "string"

        Actions:

            mv "path-format-string"
            tag -tag_name +tag_name=["tag-format-string"]
    '''
    tokens = list(cls.tokenise(rule_s))
    if not tokens:
      # empty command
      return None
    verb = tokens.pop(0)
    with Pfx(verb):
      quick = False
      match verb:
        case Identifier(name="match"):
          quick = cls.pop_quick(tokens)
          match_test, match_attribute = cls.pop_match_test(tokens)
          # collect the action
          if tokens:
            action = cls.pop_action(tokens)
          else:
            action = None
          if tokens:
            raise ValueError(f'extra tokens: {" ".join(T[0] for T in tokens)}')
          return cls(
              rule_s.rstrip(),
              match_attribute,
              match_test,
              action,
              quick=quick
          )
        case _:
          raise ValueError("unrecognised verb")
    raise RuntimeError

  @staticmethod
  @pops_tokens
  @ensure(
      lambda result:
      (result is None or all([mode in RULE_MODES for mode in result.MODES])),
      f'action.modes not in RULE_MODES:f{RULE_MODES!r}'
  )
  @typechecked
  def pop_action(tokens: List[_Token]) -> _Action:
    ''' Pop an action from `tokens`.
    '''
    return _Action.parse(tokens)

  @staticmethod
  @pops_tokens
  def pop_match_test(tokens: List[_Token]) -> Tuple[Callable, str]:
    ''' Pop a match-test from `tokens`.
    '''
    # [match-name] match-op
    match_attribute = "basename"
    if tokens:
      next_token = tokens[0]
      match next_token:
        case Identifier():
          tokens.pop(0)
          match_attribute = next_token.name
    if not tokens:
      raise ValueError("missing match-op")
    # make a match_test function
    match_op = tokens.pop(0)
    with Pfx(match_op):
      match match_op:
        case _Comparison():
          return match_op, match_attribute
        case _:
          raise ValueError(f'unsupported match-op {r(match_op)}')
    raise ValueError("invalid match-test")

  @staticmethod
  def pop_quick(tokens: List[_Token]) -> bool:
    ''' Check if the next token is `Identifier(name="quick")`.
        If so, pop it and return `True`, otherwise `False`.
    '''
    if not tokens:
      return False
    next_token = tokens[0]
    if type(next_token) is not Identifier or next_token.name != 'quick':
      return False
    tokens.pop(0)
    return True

  @classmethod
  def from_file(
      cls,
      lines: [str, Iterable[str]],
      *,
      filename: str = None,
      start_lineno: int = 1,
  ) -> List["Rule"]:
    ''' Read rules from `lines`.
        If `lines` is a string, treat it as a filename and open it for read.
    '''
    if isinstance(lines, str):
      filename = lines
      with Pfx(filename):
        with open(filename, encoding='utf-8') as lines:
          return cls.from_file(
              lines, filename=filename, start_lineno=start_lineno
          )
    rules = []
    for lineno, line in enumerate(lines, start_lineno):
      with Pfx(lineno):
        R = cls.from_str(line.rstrip(), filename=filename, lineno=lineno)
        print(filename, lineno, R)
        if R is not None:
          rules.append(R)
    return rules

if __name__ == '__main__':
  ##import sys
  from . import Tagger
  from cs.logutils import setup_logging
  setup_logging()
  ##print(Rule.from_str(sys.argv[1]))
  tagger = Tagger('.')
  tagger.process('test_file')
