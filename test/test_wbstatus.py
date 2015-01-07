#!/usr/bin/env python
import os
import sys

sys.path.insert(0, os.path.abspath(os.curdir))

import wbstatus
import pytest


def get_fake_config():
    config = {}
    config['workboard_state_phids'] = {
        "archive": "PHID-PCOL-vdldqhpp2qukxikpf4zf",
        "done": "PHID-PCOL-vhdu7nnvhs6c76axdswy",
        "feedback": "PHID-PCOL-nwvtvi6b6rq32opevo7o",
        "indev": "PHID-PCOL-hw5bskuzbvvef2zihx6r",
        "todo": "PHID-PCOL-7w2pgpuac4mxaqtjbso3"
    }
    return config


def get_fake_transactions():
    return [
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500000000,
            "newValue": "A",
            "oldValue": None,
            "transactionType": "title"
        },
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500050000,
            "newValue": "B",
            "oldValue": "A",
            "transactionType": "title"
        },
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500100000,
            "newValue": "C",
            "oldValue": "B",
            "transactionType": "title"
        },
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500150000,
            "newValue": "D",
            "oldValue": "C",
            "transactionType": "title"
        },
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500200000,
            "newValue": "E",
            "oldValue": "D",
            "transactionType": "title"
        },
        {
            "authorPHID": "PHID-USER-blahblahblah",
            "timestamp": 1500400000,
            "newValue": "F",
            "oldValue": "E",
            "transactionType": "title"
        }
    ]


# For each type, build the "old" and "new" state along the
# interval defined by the start and end variables
# For example:
# a->b
# b->c
# ---START
# c->d
# d->e
# ---END
# Old should be "c" and new should be "e"
def test_build_taskstate_from_transactions():
    config = get_fake_config()
    transactions = get_fake_transactions()
    import datetime
    from dateutil import tz
    start = datetime.datetime.fromtimestamp(1500100001, tz.tzutc())
    end = datetime.datetime.fromtimestamp(1500300000, tz.tzutc())
    taskstate = wbstatus.build_taskstate_from_transactions(
        transactions, start, end, config)
    print taskstate
    assert taskstate['title']['start'] == 'C'
    assert taskstate['title']['end'] == 'E'
