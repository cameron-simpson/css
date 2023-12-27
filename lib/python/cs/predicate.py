#!/usr/bin/python
#
# Convenience routines for expressing and testing predicates.
#   - Cameron Simpson <cs@cskk.id.au> 07nov2015
#

r'''
Trite support for code predicates, presently just the context manager `post_condition`.

Interested people should also see the `icontract` module.
'''

from contextlib import contextmanager
from cs.logutils import error
from cs.pfx import Pfx

__version__ = '20210306-post'

DISTINFO = {
    'description': "fnctions for expressing predicates",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.pfx'],
}

@contextmanager
def post_condition(*predicates):
  ''' Context manager to test post conditions.

      Predicates may either be a tuple of `(description,callable)`
      or a plain callable.
      For the latter the description is taken from `callable.__doc__`
      or `str(callable)`.
      Raises `AssertionError` if any predicates are false.
  '''
  def test_predicates(message_only=False):
    failed = []
    for pred in predicates:
      try:
        desc, func = pred
      except TypeError:
        func = pred
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
