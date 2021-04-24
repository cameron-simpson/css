#!/usr/bin/python
#
# Hacks to assist with testing.
# - Cameron Simpson <cs@cskk.id.au> 11aug2020
#

''' Hacks to assist with testing.
'''

from itertools import product
from cs.deco import decorator

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
