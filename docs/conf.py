# -*- coding: utf-8 -*-

import sys
import os
from datetime import date

sys.path.insert(0, os.path.abspath('..'))

extensions = ['sphinx.ext.autodoc', 'sphinx.ext.viewcode']
templates_path = ['_templates']
source_suffix = '.rst'
master_doc = 'index'
project = u'Workboard Status'
copyright = u'%s, Rob Lanphier and Wikimedia Foundation' % date.today().year
version = '0.1'
release = version
exclude_patterns = ['_build']
pygments_style = 'sphinx'
html_theme = 'nature'
htmlhelp_basename = 'wbstatusdoc'

autodoc_default_flags = ['members', 'private-members', 'special-members']
autodoc_memeber_order = 'groupwise'
