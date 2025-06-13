#!/usr/bin/env python3

''' Parser for tagger rules.

    The rule syntax is described in the `Rule.from_str` docstring.
'''

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import partial
import os
from os.path import (
    abspath,
    basename,
    dirname,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    islink as islinkpath,
    join as joinpath,
)
import re
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Tuple,
    Union,
)

from icontract import ensure, require
from typeguard import typechecked

from cs.cmdutils import vprint
from cs.deco import decorator, Promotable, uses_quiet, uses_verbose
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.hashindex import merge
from cs.lex import (
    BaseToken,
    CoreTokens,
    Identifier,
    QuotedString,
    cutsuffix,
    get_dotted_identifier,
    get_qstr,
    is_identifier,
    r,
    s,
    skipwhite,
)
from cs.logutils import warning
from cs.obj import public_subclasses
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.tagset import Tag, TagSet

RULE_MODES = 'move', 'tag'

class RuleToken(BaseToken):
  ''' The base class for `Rule` tokens.
  '''

  EXTRAS = (CoreTokens,)

@decorator
def pops_tokens(func):
  ''' Decorator to save the current tokens on entry and to restore
      them if an exception is raised.
  '''

  @typechecked
  def pops_token_wrapper(tokens: List[Union[RuleToken, CoreTokens]]):
    tokens0 = tokens[:]
    try:
      return func(tokens)
    except:  # noqa
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
class TagAddRemove(RuleToken):
  ''' An action to add or remove a `Tag`.
  '''

  tag_name: str
  tag_expression: Union[QuotedString, Identifier, None]
  add_remove: bool = True

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> Tuple[str, "RuleToken", int]:
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

class _Comparison(RuleToken, ABC):
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
  def parse(cls, text: str, offset: int = 0) -> "RuleToken":
    ''' Match a string or numeric value in `text` at `offset`.
    '''
    if not text.startswith('==', offset):
      raise SyntaxError(
          f'{offset}: expected "==", found {text[offset:offset+2]!r}'
      )
    qs = QuotedString.parse(text, offset + 2, skip=True)
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

  OP_SYMBOL = '~'

  # supported delimiters for regular expressions
  REGEXP_DELIMS = '/:!|'

  @classmethod
  def parse(cls, text: str, offset: int = 0) -> "RuleToken":
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
    vprint(self.__class__.__name__, self.regexp.pattern)
    vprint("  value", repr(value))
    m = self.regexp.search(value)
    if not m:
      vprint("  NO MATCH")
      return None
    vprint("  =>", m.groupdict())
    matched = m.groupdict()
    for k, v in list(matched.items()):
      if (k_ := cutsuffix(k, "_n")) is not k and k_ not in matched:
        matched[k_] = int(v)
    return matched

class _Action:

  @classmethod
  @typechecked
  def parse(cls, tokens: List[Union[RuleToken, CoreTokens]]):
    if not tokens:
      raise SyntaxError('tokens is empty')
    for subcls in public_subclasses(cls):
      with Pfx(subcls.__name__):
        try:
          return pops_tokens(subcls.parse)(tokens)
        except SyntaxError:
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
  def parse(cls, tokens: List[Union[RuleToken, CoreTokens]]) -> "MoveAction":
    ''' Parse a `mv` action from a list of tokens.
    '''
    token0 = tokens.pop(0)
    match token0:
      case Identifier(name="mv"):
        target_token = tokens.pop(0)
        match target_token:
          case QuotedString():
            return cls(target_format=target_token.value)
          case _:
            raise SyntaxError(f'expected a quoted string after {token0}')
      case _:
        raise SyntaxError(f'expected "mv", found {token0}')

  @uses_fstags
  @uses_quiet
  def __call__(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit=False,
      force=False,
      fstags: FSTags,
      quiet: bool,
  ) -> Tuple[str, ...]:
    ''' Move `fspath` to `self.target_format`, return the new fspath.
    '''
    format_tags = fstags[fspath].format_tagset()
    format_tags.update(tags)
    target_fspath = expanduser(format_tags.format_as(self.target_format))
    if not isabspath(target_fspath):
      target_fspath = joinpath(dirname(fspath), target_fspath)
    if target_fspath.endswith('/'):
      target_fspath = joinpath(target_fspath, basename(fspath))
    if doit and not force and (islinkpath(fspath) or not isfilepath(fspath)):
      raise ValueError(f'not a regular file: {fspath!r}')
    if not force and isfilepath(fspath) and os.stat(fspath).st_size == 0:
      raise ValueError(f'zero length file (placeholder?): {fspath!r}')
    target_dirpath = dirname(target_fspath)
    if doit and not isdirpath(target_dirpath):
      needdir(target_dirpath, use_makedirs=False, verbose=not quiet)
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
  def parse(cls, tokens: List[Union[RuleToken, CoreTokens]]) -> "TagAction":
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
  @uses_verbose
  def __call__(
      self,
      fspath: str,
      tags: TagSet,
      *,
      hashname: str,
      doit=False,
      force=False,
      verbose: bool,
      fstags: FSTags,
  ) -> Tuple[TagChange, ...]:
    ''' Apply `self.tag_tokens` to `tags` and `fstags[fspath]`.
        Return a tuple of the applied `TagChange`s.
    '''
    tagged = fstags[fspath]
    tag_changes = []
    for tag_token in self.tag_tokens:
      with Pfx(tag_token):
        tag_name = tag_token.tag_name
        tag_value = None
        if tag_token.add_remove:
          match tag_token.tag_expression:
            case Identifier():
              try:
                tag_value = tags[tag_token.tag_expression.name]
              except KeyError:
                warning(
                    "no tags[%r], not setting tags.%s",
                    tag_token.tag_expression.name, tag_name
                )
                continue
            case QuotedString():
              tag_value = tag_token.tag_expression.value
            case _:
              raise RuntimeError(f'unimplemented {s(self)}')
          tags.add(tag_name, tag_value, verbose=verbose)
          if doit:
            tagged.add(tag_name, tag_value)
        else:
          tags.discard(tag_name, verbose=verbose)
          if doit:
            tagged.discard(tag_name)
        tag_changes.append(
            TagChange(
                add_remove=tag_token.add_remove, tag=Tag(tag_name, tag_value)
            )
        )
    return tuple(tag_changes)

Action = Union[str, Tuple[bool, Tag]]

class Rule(Promotable):
  ''' A tagger rule.
  '''

  @pfx_method
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
      force=False,
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
    for action in self.action, :
      if self.action is None:
        continue
      with Pfx(action.__doc__.strip().split()[0].strip()):
        if not (set(action.MODES) & set(modes)):
          vprint("SKIP action modes", action.MODES, "not in", modes)
          continue
        # apply the current tags in case the file gets moved
        fstags[fspath].update(tags)
        try:
          side_effects = action(
              fspath,
              tags,
              hashname=hashname,
              doit=doit,
              force=force,
          )
        except Exception as e:
          warning("action failed (%s): %s", e.__class__.__name__, e)
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

  # TODO: should be class method of RuleToken
  @classmethod
  @typechecked
  def get_token(cls, rule_s: str, offset: int = 0) -> RuleToken:
    ''' Parse a token from `rule_s` at `offset`.
        This skips any leading whitespace.
        Raise `EOFError` at the end of the string or at a comment.
        Raise `SyntaxError` if no token is recognised.
    '''
    offset = skipwhite(rule_s, offset)
    if offset == len(rule_s) or rule_s.startswith(('#', '//'), offset):
      # end of string or comment -> end of tokens
      raise EOFError
    try:
      return RuleToken.parse(rule_s, offset)
    except SyntaxError as e:
      raise SyntaxError(f'no token recognised at: {rule_s[offset:]!r}') from e

  @classmethod
  def tokenise(cls, rule_s: str, offset: int = 0):
    ''' Generator yielding `RuleToken`s.
    '''
    yield from RuleToken.scan(rule_s, offset)

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
              filename=filename,
              lineno=lineno,
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
  def pop_action(tokens: List[Union[RuleToken, CoreTokens]]) -> _Action:
    ''' Pop an action from `tokens`.
    '''
    return _Action.parse(tokens)

  @staticmethod
  @pops_tokens
  def pop_match_test(
      tokens: List[Union[RuleToken, CoreTokens]]
  ) -> Tuple[Callable, str]:
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
  def pop_quick(tokens: List[Union[RuleToken, CoreTokens]]) -> bool:
    ''' Check if the next token is `Identifier(name="quick")`.
        If so, pop it and return `True`, otherwise `False`.
    '''
    if not tokens:
      return False
    next_token = tokens[0]
    match next_token:
      case Identifier(name="quick"):
        tokens.pop(0)
        return True
      case _:
        return False

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
        with open(filename, encoding='utf-8') as lines2:
          return cls.from_file(
              lines2, filename=filename, start_lineno=start_lineno
          )
    rules = []
    for lineno, line in enumerate(lines, start_lineno):
      with Pfx(lineno):
        line_ = line.rstrip()
        _line_ = line_.lstrip()
        if not _line_ or _line_.startswith('#'):
          continue
        R = cls.from_str(line_, filename=filename, lineno=lineno)
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
