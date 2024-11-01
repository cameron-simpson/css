#!/usr/bin/env python3

''' Support for Kindle libraries.
'''

KINDLE_LIBRARY_ENVVAR = 'KINDLE_LIBRARY'

# will be replaced with a factory
from .classic import KindleTree
