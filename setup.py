from distutils.core import setup, Extension
 
edgeDetect = Extension('cs.venti.edgeDetect', sources = ['lib/cs/venti/edgeDetect.c'])

setup (name = 'cs.venti.edgeDetect',
       version = '0.1',
       description = 'C implementation of the edge detection code.',
       ext_modules = [edgeDetect])

