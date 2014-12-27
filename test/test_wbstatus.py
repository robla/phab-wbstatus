#!/usr/bin/env python
import os
import sys

sys.path.insert(0, os.path.abspath(os.curdir))

import wbstatus
import pytest


def test_get_workboard_diff():
    a1 = {'T1':'Foo', 'T2':'Bar'}
    a2 = {'T1':'Foo', 'T2':'Foo2', 'T3':'Foo3'}
    diff = wbstatus.get_workboard_diff(a1, a2)
    assert diff.get('T2')[0] == 'Bar'
    assert diff.get('T3')[0] == None
    assert diff.get('T1') == None

