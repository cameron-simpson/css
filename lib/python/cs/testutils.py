#!/usr/bin/python
#
# Hacks to assist with testing.
# - Cameron Simpson <cs@cskk.id.au> 11aug2020
#

''' Hacks to assist with testing.
'''

from itertools import product
from cs.context import push_cmgr, pop_cmgr
from cs.deco import decorator

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
        "Topic :: Software Development :: Testing :: Unit",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
    ],
}

@decorator
def product_test(test_method, **params):
  ''' Decorator for test methods which should run subTests
      against the Cartesian products from `params`.

      A specific TestCase would define its own decorator
      and apply it throughout the suite.
      Here is an example from cs.vt.datadir_tests:

        def multitest(test_method):
          return product_test(
              test_method,
              datadirclass=[DataDir, RawDataDir],
              indexclass=[
                  indexclass_by_name(indexname)
                  for indexname in sorted(indexclass_names())
              ],
              hashclass=[
                  HASHCLASS_BY_NAME[hashname]
                  for hashname in sorted(HASHCLASS_BY_NAME.keys())
              ],
          )

      whose test suite then just decorates each method with `@multitest`:

          @multitest
          def test000IndexEntry(self):
              ....

      Note that because there must be setup and teardown for each product,
      the TestCase class may well have empty `setUp` and `tearDown` methods
      and instead is expected to provide:
      * `product_setup(self,**params)`:
        a setup method taking keyword arguments for each product
      * `product_teardown(self)`:
        the corresponding testdown method
      There are called around each `subTest`.
  '''
  param_names = list(params.keys())
  product_iterables = [params[name] for name in param_names]

  def product_method(self):
    ''' Run the test method against the various Cartesian products.
    '''
    for prod in product(*product_iterables):
      # map the original names to the current Cartesian product
      call_params = dict(zip(param_names, prod))
      with self.subTest(**call_params):
        self.product_setup(**call_params)
        try:
          test_method(self)
        finally:
          self.product_teardown()

  return product_method

class SetupTeardownMixin:
  ''' A mixin to support a single `setupTeardown()` context manager method.
  '''

  def setUp(self):
    ''' Run `super().setUp()` then the set up step of `self.setupTeardown()`.
    '''
    super().setUp()
    push_cmgr(self, '_SetupTeardownMixin__tearDown', self.setupTeardown())

  def tearDown(self):
    ''' Run the tear down step of `self.setupTeardown()`,
        then `super().tearDown()`.
    '''
    pop_cmgr(self, '_SetupTeardownMixin__tearDown')
    super().tearDown()
