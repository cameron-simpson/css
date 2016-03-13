#!/usr/bin/python
#
# Convenience routines for expressing and testing predicates.
#   - Cameron Simpson <cs@zip.com.au> 07nov2015
#

from contextlib import contextmanager
from cs.logutils import Pfx, error

@contextmanager
def post_condition(*predicates):
  ''' Context manager to test post conditions.
      Predicates may either be a tuple of (description, callable) or a plain callable.
      For the latter the description is taken from callable.__doc__ or str(callable).
      Raises AssertionError if any predicates are false.
  '''
  def test_predicates(message_only=False):
    failed = []
    for pred in predicates:
      try:
        desc, func = pred
      except TypeError:
        fund = pred
        desc = getattr(func, '__doc__', str(func))
      with Pfx("post_condition: %s", desc):
        try:
          result = func()
        except Exception as e:
          failed.append(desc)
          error("exception evaluating post condition: %s", e)
          raise
        else:
          if not result:
            failed.append(desc)
            error("false")
    if failed and not message_only:
      raise AssertionError("post conditions false: %r" % (failed,))
  try:
    yield None
  except:
    test_predicates(message_only=True)
    raise
  else:
    test_predicates(message_only=False)
