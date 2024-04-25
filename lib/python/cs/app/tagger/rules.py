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
from re import Pattern
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
  def pops_token_wrapper(tokens: List[TokenRecord]):
    tokens0 = list(tokens)
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

class _Token(Promotable, ABC):
  ''' Base class for tokens.
  '''

  @classmethod
  def token_subclasses(cls):
    token_classes = []
    q = ListQueue(cls.__subclasses__())
    for subcls in q:
      if not issubclass(subcls, _Token):
        continue
      if not subcls.__name__.startswith('_'):
        token_classes.append(subcls)
        print(cls, "token_classes +", subcls)
      q.extend(subcls.__subclasses__())
    return token_classes

  @abstractclassmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a token from `test` at `offset`.
        Return a 3-tuple of `(token_s,token,end_offset)`:
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `end_offset`: the parse offset after the token
        This skips any leading whitespace.
        If there is no recognised token, return `(None,None,offset)`.
    '''
    raise NotImplementedError

  @classmethod
  @pfx_method
  @typechecked
  def from_str(cls, text: str) -> "_Token":
    ''' Parse `test` as a token of type `cls`, return the token.
        This is a wrapper for the `parse` class method.
    '''
    if not text or text[0].isspace():
      raise ValueError("expected text to start with nonwhitespace")
    token_classes = cls.token_subclasses()
    for subcls in token_classes:
      print("from_str: try", subcls, "parse", repr(text))
      try:
        token_s, token, end_offset = subcls.parse(text)
      except (SyntaxError, ValueError) as e:
        print("from_str: exception:", e)
      else:
        break
    else:
      raise ValueError(
          f'no {cls.__name__} subclass matched'
          f', tried: {", ".join(subcls.__name for subcls in token_classes)}'
      )
    if end_offset != len(text):
      raise ValueError('whitespace after token {token.matched!r}')
    return token

  @abstractmethod
  def __str__(self):
    ''' Return this `_Token`'s parsable form.
    '''
    raise NotImplementedError

class Identifier(_Token):
  ''' A dotted identifier.
  '''

  @require(lambda name: is_identifier(name))
  def __init__(self, name: str):
    self.name = name

  @ensure(lambda result: is_identifier(result))
  def __str__(self):
    return self.name

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a dotted identifier from `test`.
    '''
    start_offset = skipwhite(text, offset)
    name, end_offset = get_dotted_identifier(text, start_offset)
    if not name:
      raise SyntaxError(
          f'{offset}: expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    return text[start_offset:end_offset], cls(name), end_offset

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
  @trace
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
    return text[start_offset:m.end()], cls(value), m.end()

@dataclass
class QuotedString(_LiteralValue):
  ''' A double quoted string.
  '''

  value: str
  quote: str = '"'

  def __str__(self):
    return slosh_quote(self.value, self.quote)

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a double quoted string from `text`.
    '''
    start_offset = skipwhite(text, offset)
    if not text.startswith('"', start_offset):
      raise SyntaxError(
          f'{start_offset}: expected ", found {text[start_offset:start_offset+1]!r}'
      )
    q = text[start_offset]
    value, end_offset = get_qstr(text, start_offset)
    return text[start_offset:end_offset], cls(value, q), end_offset

@dataclass
class TagAddRemove(_Token):
  tag: Tag
  add_remove: bool = True

  def __str__(self):
    if self.add_remove:
      return str(self.tag)
    return f'-{self.tag.name}'

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    start_offset = skipwhite(text, offset)
    with Pfx("%d:%r...", start_offset, text[start_offset:start_offset + 16]):
      if text.startswith('-', start_offset):
        add_remove = False
      elif text.startswith('+', start_offset):
        add_remove = True
      else:
        raise ValueError(f'expected + or -, got: {text[offset:offset+1]!r}')
    offset += 1
    with Pfx("%d:%r...", offset, text[offset:offset + 16]):
      name, offset = get_dotted_identifier(text, offset)
      if not name:
        raise SyntaxError(
            f'expected dotted identifier, found {text[offset:offset+3]!r}...'
        )
    if text.startswith("=", offset):
      if not add_remove:
        raise ValueError(f'{offset}: unexpected assignment following -{name}')
      offset += 1
      with Pfx("%d:%r...", offset, text[offset:offset + 16]):
        _, qs, end_offset = QuotedString.from_str(text, offset)
        if qs is None:
          raise ValueError(f'expected quoted string after {name}=')
      value = qs.value
    else:
      value = None
      end_offset = offset
    return text[start_offset:end_offset], cls(
        tag=Tag(name, value), add_remove=add_remove
    ), end_offset

class Comparison(_Token, ABC):
  ''' Abstract base class for comparisons.
  '''

  @abstractmethod
  def __call__(self, value, tags):
    raise NotImplementedError

class EqualityComparison(Comparison):
  ''' A comparison of some string for equality.
      Return is `None` on no omatch or the `Match.groupdict()` on a match.
  '''

  @typechecked
  def __init__(self, compare_s: str):
    self.compare_s = compare_s

  def __str__(self):
    q = '"'
    return f'== {slosh_quote(self.compare_s,q)}'

  @classmethod
  @typechecked
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Match a string or numeric value in `text` at `offset`.
        Return a 3-tuple of `(matched_text,_Token,end_offset)`
        being the matching source text,
    '''
    start_offset = skipwhite(text, offset)
    if not text.startswith('==', start_offset):
      raise SyntaxError(
          f'{offset}: expected "==", found {text[start_offset:start_offset+2]!r}'
      )
    offset = skipwhite(text, start_offset + 2)
    _, qs, end_offset = QuotedString.from_str(text, offset)
    return text[start_offset:end_offset], cls(qs.value), end_offset

  def __call__(self, value_s: str, tags: TagSet):
    return value_s == self.compare_s

class RegexpComparison(Comparison):
  ''' A comparison of some string using a regular expression.
      Return is `None` on no omatch or the `Match.groupdict()` on a match.
  '''

  # supported delimiters for regular expressions
  REGEXP_DELIMS = '/:!|'

  @require(lambda self, delim: delim in self.REGEXP_DELIMS)
  @typechecked
  def __init__(self, regexp: Pattern, delim: str):
    self.regexp = regexp
    self.delim = delim

  def __str__(self):
    return f'~ {self.delim}{self.regexp.pattern}{self.delim}'

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    start_offset = skipwhite(text, offset)
    if not text.startswith('~', start_offset):
      raise SyntaxError(
          f'{start_offset}: expected "~", found {text[start_offset:start_offset+1]!r}'
      )
    offset = skipwhite(text, start_offset + 1)
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
    return text[start_offset:end_offset], cls(regexp, delim), end_offset

  def __call__(self, value: str, tags: TagSet):
    m = self.regexp.search(value)
    if not m:
      return None
    return m.groupdict()

TokenRecord = namedtuple('TokenRecord', 'matched token end_offset')

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
      action: Callable[..., Iterable[Action]],
      *,
      quick=False
  ):
    self.definition = definition
    self.match_attribute = match_attribute
    self.match_test = match_test
    self.action = action
    self.quick = quick

  def __str__(self):
    return self.definition

  # TODO: repr should do what str does now
  def __repr__(self):
    return (
        f'{self.__class__.__name__}('
        f'{self.definition!r},'
        f'{self.match_attribute},'
        f'match_test={self.match_test!r},'
        f'action={self.action},'
        f'quick={self.quick},'
        ')'
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
                  raise RuntimeError(f'unhandled side effect {r(side_effect)}')
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
  def get_token(cls, rule_s: str, offset: int = 0) -> Tuple[str, _Token, int]:
    ''' Parse a token from `rule_s` at `offset`.
        Return a 3-tuple of `(token_s,token,offset)`:
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `offset`: the parse offset after the token
        This skips any leading whitespace.
        If there is no recognised token, return `(None,None,offset)`.
    '''
    offset = skipwhite(rule_s, offset)
    if offset == len(rule_s) or rule_s.startswith(('#', '//'), offset):
      # end of string or comment -> end of tokens
      raise EOFError
    for token_type in (
        Identifier,
        QuotedString,
        EqualityComparison,
        RegexpComparison,
        TagAddRemove,
    ):
      try:
        matched_s, token, end_offset = token_type.from_str(rule_s, offset)
      except SyntaxError:  # as e:
        ##warning("not %s: %s", token_type.__name__, e)
        continue
      return matched_s, token, end_offset
    raise SyntaxError(f'unrecognised token at: {rule_s[offset:]}')

  @classmethod
  def tokenise(cls, rule_s: str, offset: int = 0):
    ''' Generator yielding `(token_s,token,offset)` 3-tuples.
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `offset`: the parse offset after the token
    '''
    while True:
      try:
        token_s, token, offset = cls.get_token(rule_s, offset)
      except EOFError:
        return
      assert rule_s[:offset].endswith(token_s), (
          f'rule_s[:offset={offset}]'
          f' should end with token_s:{token_s!r}'
          f' but ends with {rule_s[:offset][-len(token_s):]!r}'
      )
      yield TokenRecord(matched=token_s, token=token, end_offset=offset)

  @classmethod
  def from_str(cls, rule_s: str) -> Union["Rule", None]:
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
    verb = tokens.pop(0).token
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
      (result is None or all([mode in RULE_MODES for mode in result.modes])),
      f'action.modes not in RULE_MODES:f{RULE_MODES!r}'
  )
  @typechecked
  def pop_action(tokens: List[TokenRecord]) -> Union[Callable, None]:
    ''' Pop an action from `tokens`.
    '''
    action_token = tokens.pop(0).token
    with Pfx(action_token):
      match action_token:
        case Identifier(name="mv"):
          if not tokens:
            raise ValueError("missing mv target")
          target_token = tokens.pop(0).token
          match target_token:
            case QuotedString():
              target_format = target_token.value

              @uses_fstags
              @typechecked
              def mv_action(
                  fspath: str,
                  tags: TagSet,
                  *,
                  hashname: str,
                  doit=False,
                  quiet=False,
                  fstags: FSTags,
              ) -> Tuple[str, ...]:
                ''' Move `fspath` to `target_format`, return the new fspath.
                '''
                target_fspath = expanduser(tags.format_as(target_format))
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

              mv_action.__doc__ = (
                  f'Move `fspath` to {target_format!r}`.format_kwargs(**format_kwargs)`.'
              )
              mv_action.modes = ('move',)
              return mv_action
        case Identifier(name="tag"):
          if not tokens:
            raise ValueError("missing tags")
          tag_tokens = []
          while tokens:
            token = tokens.pop(0).token
            if not isinstance(token, TagAddRemove):
              raise ValueError(f'expected TagAddRemove tokens, found: {token}')
            tag_tokens.append(token)

          @typechecked
          def tag_action(
              fspath: str,
              tags: TagSet,
              *,
              hashname: str,
              doit=False,
              quiet=False,
          ) -> Iterable[TagChange]:
            ''' Apply tag changes from this `Rule` to `tags`.
            '''
            tag_changes = []
            for tag_token in tag_tokens:
              if tag_token.add_remove:
                tags.add(tag_token.tag, verbose=not quiet)
              else:
                tags.discard(tag_token.tag.name, verbose=not quiet)
              tag_changes.append(
                  TagChange(
                      add_remove=tag_token.add_remove, tag=tag_token.tag
                  )
              )
            return tuple(tag_changes)

          tag_action.__doc__ = (
              f'Tag `fspath` with {" ".join(map(str,tag_tokens))}.'
          )
          tag_action.modes = ('tag',)

          return tag_action
    raise ValueError("invalid action")

  @staticmethod
  @pops_tokens
  def pop_match_test(tokens: List[TokenRecord]) -> Tuple[Callable, str]:
    ''' Pop a match-test from `tokens`.
    '''
    # [match-name] match-op
    match_attribute = "basename"
    if tokens:
      next_token = tokens[0].token
      match next_token:
        case Identifier():
          tokens.pop(0)
          match_attribute = next_token.name
    if not tokens:
      raise ValueError("missing match-op")
    # make a match_test function
    match_op = tokens.pop(0).token
    with Pfx(match_op):
      match match_op:
        case Comparison():
          return match_op, match_attribute
        case _:
          raise ValueError(f'unsupported match-op {r(match_op)}')
    raise ValueError("invalid match-test")

  @staticmethod
  @pops_tokens
  def pop_quick(tokens: List[TokenRecord]) -> bool:
    ''' Check if the next token is `Identifier(name="quick")`.
        If so, pop it and return `True`, otherwise `False`.
    '''
    if tokens:
      next_token = tokens[0].token
      match next_token:
        case Identifier(name="quick"):
          tokens.pop(0)
          return True
    return False

  @classmethod
  def from_file(cls, lines: [str, Iterable[str]]):
    ''' Read rules from `lines`.
        If `lines` is a string, treat it as a filename and open it for read.
    '''
    if isinstance(lines, str):
      filename = lines
      with Pfx(filename):
        with open(filename, encoding='utf-8') as lines:
          return cls.from_file(lines)
    rules = []
    for lineno, line in enumerate(lines, 1):
      with Pfx(lineno):
        R = cls.from_str(line)
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
