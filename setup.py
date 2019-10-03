from distutils.core import setup
import setuptools

short_description = "Create, visualise and leverage networks of ANPR cameras on the road network."

long_description = \
"""
**ANPRx** is a package for traffic analytics using networks of automatic number plate cameras.
"""

classifiers = ['Development Status :: 3 - Alpha',
               'License :: OSI Approved :: Apache Software License',
               'Operating System :: OS Independent',
               'Intended Audience :: Science/Research',
               'Topic :: Scientific/Engineering :: GIS',
               'Topic :: Scientific/Engineering :: Visualization',
               'Topic :: Scientific/Engineering :: Physics',
               'Topic :: Scientific/Engineering :: Mathematics',
               'Topic :: Scientific/Engineering :: Information Analysis',
               'Programming Language :: Python',
               'Programming Language :: Python :: 3.7']

install_requires = [
    'statistics',
    'osmnx',
    'scikit-learn',
    'adjustText',
    'progress',
    'ray',
    'psutil',
    'setproctitle',
    'scikit-learn'
]

links = ['http://github.com/ppintosilva/osmnx/tarball/master#egg=package-1.0']

extras_require = {
    'tests': [
       'pytest'],
    'docs': [
       'sphinx',
       'sphinx_rtd_theme'],
    'examples': [
       'ipykernel']}

# now call setup
setup(name = 'anprx',
      version = '0.1.3',
      description = short_description,
      long_description = long_description,
      classifiers = classifiers,
      url = 'https://github.com/ppintosilva/anprx',
      author = 'Pedro Pinto da Silva',
      author_email = 'ppintodasilva@gmail.com',
      license = 'Apache License 2.0',
      platforms = 'any',
      packages = ['anprx'],
      install_requires = install_requires,
      extras_require = extras_require,
      dependency_links = links)
