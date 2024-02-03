#!/usr/bin/env python3

''' Parser for tagger rules.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from os.path import basename
import re
from re import Pattern
import sys
from typing import Any, Callable, List, Tuple, Union

from typeguard import typechecked
from icontract import ensure, require

from cs.deco import Promotable
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
  ''' An identifier.
  '''

  @require(lambda name: is_identifier(name))
  def __init__(self, name: str):
    self.name = name

  @ensure(lambda result: is_identifier(result))
  def __str__(self):
    return self.name

  @classmethod
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    offset0 = offset
    start_offset = skipwhite(text, offset)
    name, end_offset = get_identifier(text, start_offset)
    if name:
      return text[start_offset:end_offset], cls(name), end_offset
    return None, None, offset0

class QuotedString(_Token):

  def __init__(self, value: str, quote: str):
    self.value = value
    self.quote = quote

  def __str__(self):
    q = self.quote
    return q + self.value.replace('\\', '\\\\').replace(q, '\\' + q)

  @classmethod
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    offset0 = offset
    start_offset = skipwhite(text, offset)
    if text.startswith('"', start_offset):
      q = text[start_offset]
      value, end_offset = get_qstr(text, start_offset)
      return text[start_offset:end_offset], cls(value, q), end_offset
    return None, None, offset0

class RegexpComparison(_Token):

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
      match_test: Callable[[str], dict],
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
          # quick?
          if tokens:
            next_token = tokens[0].token
            match next_token:
              case Identifier(name="quick"):
                tokens.pop(0)
                quick = True
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
              case RegexpComparison():
                # ~/regexp/
                def match_test(test_s: str) -> Union[dict, None]:
                  m = match_op.regexp.search(test_s)
                  if not m:
                    return None
                  return m.groupdict()
              case _:
                raise ValueError(f'unsupported match-op {r(match_op)}')
          # collect the action
          if not tokens:
            action = None
          else:
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
                    case _:
                      raise ValueError(
                          f'expected quoted target format string, got: {target_token}'
                      )
                case _:
                  raise ValueError("unrecognised action")
            if tokens:
              raise ValueError(
                  f'extra tokens: {" ".join(T[0] for T in tokens)}'
              )
          return cls(rule_s, match_attribute, match_test, action, quick=quick)
        case _:
          raise ValueError("unrecognised verb")
    raise RuntimeError

  @strable
  def from_file(cls, f):
    ''' Read rules from the file `f`.
    '''
    rules = []
    for lineno, line in enumerate(f, 1):
      with Pfx(lineno):
        R = cls.from_str(line)
        if R is not None:
          rules.append(R)
    return rules

if __name__ == '__main__':
  from cs.logutils import setup_logging
  setup_logging()
  R = Rule.from_str(sys.argv[1])
  print(R)
