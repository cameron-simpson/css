#!/usr/bin/env python3

''' Parser for tagger rules.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from os.path import basename
import re
from re import Pattern
import sys
from typing import Any, Callable, Iterable, List, Tuple, Union

from typeguard import typechecked
from icontract import ensure, require

from cs.deco import decorator, Promotable, strable
from cs.fstags import FSTags, uses_fstags
from cs.lex import (
    get_identifier,
    get_qstr,
    get_nonwhite,
    is_identifier,
    skipwhite,
)
from cs.pfx import Pfx, pfx_call

from cs.debug import X, trace, r, s

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

class _Token(ABC):

  @abstractclassmethod
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a token from `test` at `offset`.
        Return a 3-tuple of `(token_s,token,offset)`:
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `offset`: the parse offset after the token
        This skips any leading whitespace.
        If there is no recognised token, return `(None,None,offset)`.
    '''
    raise NotImplementedError

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
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a dotted identifier from `test`.
    '''
    offset0 = offset
    start_offset = skipwhite(text, offset)
    name, end_offset = get_dotted_identifier(text, start_offset)
    if name:
      return text[start_offset:end_offset], cls(name), end_offset
    return None, None, offset0

class QuotedString(_Token):
  ''' A double quoted string.
  '''

  def __init__(self, value: str, quote: str):
    self.value = value
    self.quote = quote

  def __str__(self):
    return slosh_quote(self.value, self.quote)

  @classmethod
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    ''' Parse a double quoted string from `text`.
    '''
    offset0 = offset
    start_offset = skipwhite(text, offset)
    if text.startswith('"', start_offset):
      q = text[start_offset]
      value, end_offset = get_qstr(text, start_offset)
      return text[start_offset:end_offset], cls(value, q), end_offset
    return None, None, offset0

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
  def __init__(self, compare_s: str, tags: TagSet):
    self.compare_s = compare_s

  def __str__(self):
    q = '"'
    return f'== {slosh_quote(self.compare_s,q)}'

  @classmethod
  @trace
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    offset0 = offset
    start_offset = skipwhite(text, offset)
    if text.startswith('==', start_offset):
      offset = skipwhite(text, start_offset + 2)
      _, qs, end_offset = QuotedString.from_str(text, offset)
      return text[start_offset:end_offset], cls(qs.value), end_offset
    return None, None, offset0

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
    return '~ {self.delim}{self.regexp.pattern}{self.delim}'

  @classmethod
  @trace
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    offset0 = offset
    start_offset = skipwhite(text, offset)
    if text.startswith('~', start_offset):
      X("~")
      offset = skipwhite(text, start_offset + 1)
      if not text.startswith(tuple(cls.REGEXP_DELIMS), offset):
        raise ValueError(
            f'{offset}: expected regular expression delimited by one of {cls.REGEXP_DELIMS!r}'
        )
      delim = text[offset]
      X("delim = %r", delim)
      X("  at %r", text[offset:])
      offset += 1
      endpos = text.find(delim, offset)
      X("endpos = %r", endpos)
      if endpos < 0:
        raise ValueError(
            f'{offset}: count not find closing delimiter {delim!r} for regular expression'
        )
      re_s = text[offset:endpos]
      X("re_s = %r", re_s)
      end_offset = endpos + 1
      X("offset => %r", offset)
      regexp = pfx_call(re.compile, re_s)
      return text[start_offset:end_offset], cls(regexp, delim), end_offset
    return None, None, offset0

  def __call__(self, value: str, tags: TagSet):
    m = self.regexp.search(value)
    if not m:
      return None
    return m.groupdict()

TokenRecord = namedtuple('TokenRecord', 'matched token end_offset')

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
      action: Callable[[str, dict], str],
      *,
      quick=False
  ):
    self.definition = definition
    self.match_attribute = match_attribute
    self.match_test = match_test
    self.action = action
    self.quick = quick

  # TODO: str should write out a valid rule
  # TODO: repr should do what str does now
  def __str__(self):
    return (
        f'{self.__class__.__name__}('
        f'{self.definition!r},'
        f'{self.match_attribute},'
        f'match_test={self.match_test},'
        f'action={self.action},'
        ')'
    )

  @typechecked
  def apply(self, fspath: str, tags: TagSet) -> bool:
    ''' Apply this `Rule` to `tagged` using the working `TagSet` `tags`.
        On no match return `False`.
        On a match:
        * update `tags` from the match result
        * run the `Rule.action` if not `None`
        * return `True`
    '''
    test_s = self.get_attribute_value(fspath, tags, self.match_attribute)
    if not isinstance(test_s, str):
      raise TypeError(
          f'expected str for {self.match_attribute!r} but got: {s(test_s)}'
      )
    match_result = self.match_test(test_s, tags)
    if match_result is None:
      return False
    tags.update(match_result)
    return True

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
  @classmethod
  def get_token(cls, rule_s: str, offset: int = 0) -> Tuple[str, Any, int]:
    ''' Parse a token from `rule_s` at `offset`.
        Return a 3-tuple of `(token_s,token,offset)`:
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `offset`: the parse offset after the token
        This skips any leading whitespace.
        If there is no recognised token, return `(None,None,offset)`.
    '''
    offset0 = offset
    offset = skipwhite(rule_s, offset)
    if not rule_s.startswith(('#', '//'), offset):
      for token_type in Identifier, QuotedString, RegexpComparison:
        try:
          matched_s, token, end_offset = token_type.from_str(rule_s, offset)
        except ValueError:
          continue
        if token is not None:
          return matched_s, token, end_offset
    return None, None, offset0

  @classmethod
  def tokenise(cls, rule_s: str, offset: int = 0):
    ''' Generator yielding `(token_s,token,offset)` 3-tuples.
        * `token_s`: the source text of the token
        * `token`: the parsed object which the token represents
        * `offset`: the parse offset after the token
    '''
    while True:
      token_s, token, offset = cls.get_token(rule_s, offset)
      X("tokenise: %r, offset=%d -> %r", token, offset, rule_s[offset:])
      if token_s is None:
        break
      yield TokenRecord(matched=token_s, token=token, end_offset=offset)

  @classmethod
  def from_str(cls, rule_s: str) -> Union["Rule", None]:
    ''' Parse `rule_s` as a text definition of a `Rule`.

        Syntax:

            match [quick] [attribute] match-op [action]
            drop [quick] [attribute] match-op
            do [quick] action

        Actions:

            mv "path-format-string"
            tag tag_name={"tag-format-string"|int}
    '''
    tokens = list(cls.tokenise(rule_s))
    print("TOKENS:", [T[0] for T in tokens])
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
          return cls(rule_s, match_attribute, match_test, action, quick=quick)
        case _:
          raise ValueError("unrecognised verb")
    raise RuntimeError

  @staticmethod
  @pops_tokens
  @trace
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

              def action(fspath: str, fkwargs: dict) -> str:
                target_fspath = target_format.format(**fkwargs)
                print("MV", fspath, "->", target_fspath)

              action.__doc__ = (
                  f'Move `fspath` to {target_format!r}`.format_kwargs(**format_kwargs)`.'
              )
              return action
    raise ValueError("invalid action")

  @staticmethod
  @pops_tokens
  @trace
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
  @trace
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
      with open(filename) as lines:
        return cls.from_file(lines)
    rules = []
    for lineno, line in enumerate(lines, 1):
      with Pfx(lineno):
        R = cls.from_str(line)
        if R is not None:
          rules.append(R)
    return rules

if __name__ == '__main__':
  from cs.logutils import setup_logging
  setup_logging()
  print(Rule.from_str(sys.argv[1]))
