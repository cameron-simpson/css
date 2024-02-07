#!/usr/bin/env python3

''' Parser for tagger rules.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from functools import partial
from os.path import abspath, basename
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

from typeguard import typechecked
from icontract import ensure, require

from cs.deco import decorator, promote, Promotable
from cs.fstags import FSTags, TaggedPath, uses_fstags
from cs.lex import (
    get_dotted_identifier,
    get_qstr,
    is_identifier,
    skipwhite,
)
from cs.logutils import ifverbose, warning
from cs.pfx import Pfx, pfx_call
from cs.tagset import Tag, TagSet

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
  ''' Base class for tokens.
  '''

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
    start_offset = skipwhite(text, offset)
    name, end_offset = get_dotted_identifier(text, start_offset)
    if not name:
      raise SyntaxError(
          f'{offset}: expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    return text[start_offset:end_offset], cls(name), end_offset

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
    start_offset = skipwhite(text, offset)
    if not text.startswith('"', start_offset):
      raise SyntaxError(
          f'{start_offset}: expected ", found {text[start_offset:start_offset+1]!r}'
      )
    q = text[start_offset]
    value, end_offset = get_qstr(text, start_offset)
    return text[start_offset:end_offset], cls(value, q), end_offset

class TagAddRemove(_Token):
  ''' A `+name[="string"]` or `-name` tag update token.
  '''

  @require(lambda tag: tag.value is None or isinstance(tag.value, (int, str)))
  @require(lambda tag, add_remove: add_remove or tag.value is None)
  def __init__(self, tag: Tag, add_remove: Optional[bool] = True):
    self.tag = tag
    self.add_remove = add_remove

  def __str__(self):
    if self.add_remove:
      return str(self.tag)
    return f'-{self.tag.name}'

  @classmethod
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
    start_offset = skipwhite(text, offset)
    if text.startswith('-', start_offset):
      add_remove = False
    elif text.startswith('+', start_offset):
      add_remove = True
    else:
      raise ValueError(f'exported + or -, got: {text[offset:offset+1]!r}')
    offset += 1
    name, offset = get_dotted_identifier(text, offset)
    if not name:
      raise SyntaxError(
          f'{offset}: expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    if text.startswith("=", offset):
      if not add_remove:
        raise ValueError(f'{offset}: unexpected assignment following -{name}')
      offset += 1
      _, qs, end_offset = QuotedString.from_str(text, offset)
      if qs is None:
        raise ValueError(f'{offset}: expected quoted string after {name}=')
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
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
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
  def from_str(cls, text: str, offset: int = 0) -> Tuple[str, "_Token", int]:
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
      action: Callable[[str, Mapping], Iterable[Action]],
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
  def apply(
      self,
      fspath: str,
      tags: TagSet,
      doit: bool = True,
      verbose: bool = True,
  ) -> Union[bool, Iterable[Action]]:
    ''' Apply this `Rule` to `fspath` using the working `TagSet` `tags`,
        typically the inherited tags of `fspath`.
        On no match return `False`.
        On a match, return an iterable of side effects, each of which may be:
        * `str`: a new value for the fspath indicating a move or link
        * `(bool,Tag)`: a 2 tuple of aan "add_remove" bool and `Tag`
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
    if self.action is None:
      return True
    return self.action(fspath, tags, doit=doit, verbose=verbose)

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

              def mv_action(
                  fspath: str,
                  fkwargs: dict,
                  doit=True,
                  verbose=False,
              ) -> Tuple[str]:
                ''' Move `fspath` to `target_format`, return the new fspath.
                '''
                target_fspath = target_format.format(**fkwargs)
                ifverbose(verbose, "mv %r -> %r", fspath, target_fspath)
                # TODO: actually move the file
                if doit:
                  pass
                return (target_fspath,)

              mv_action.__doc__ = (
                  f'Move `fspath` to {target_format!r}`.format_kwargs(**format_kwargs)`.'
              )
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
              doit=True,
              verbose=False,
          ) -> Iterable[Tuple[bool, Tag]]:
            ''' Apply tag changes.
            '''
            tag_changes = []
            for tag_token in tag_tokens:
              if tag_token.add_remove:
                tags.add(tag_token.tag, verbose=verbose)
              else:
                tags.discard(tag_token.tag.name, verbose=verbose)
              tag_changes.append((tag_token.add_remove, tag_token.tag))
            return tag_changes

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
