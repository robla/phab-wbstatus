#!/usr/bin/env python

from bs4 import BeautifulSoup
import phabricator
import json
import datetime
import dateutil.parser
import os
import string
import pickle

from datetime import datetime as dt
from dateutil import tz

MWCORETEAM_PHID = "PHID-PROJ-oft3zinwvih7bgdhpfgj"
WORKBOARD_HTML_CACHE = '/home/robla/2014/phabworkboard-data/html'
WORKBOARD_PICKLE_CACHE = '/home/robla/2014/phabworkboard-data/pickles'

# TODO: read from config file
global_config = {}
global_config['workboard_state_phids'] = {}
global_config['workboard_state_phids']['todo'] = "PHID-PCOL-7w2pgpuac4mxaqtjbso3"
global_config['workboard_state_phids']['indev'] = "PHID-PCOL-hw5bskuzbvvef2zihx6r"
global_config['workboard_state_phids']['feedback'] = "PHID-PCOL-nwvtvi6b6rq32opevo7o"
global_config['workboard_state_phids']['archive'] = "PHID-PCOL-vdldqhpp2qukxikpf4zf"
global_config['workboard_state_phids']['done'] = "PHID-PCOL-vhdu7nnvhs6c76axdswy"




# Keep track of all of the objects with associated PHIDs.  Aggregate all of
# PHIDs so that we only need to make one call to Phabricator.phid.query to
# lookup a big batch of PHIDs, rather than making dozens/hundreds of calls to
# look them up one at a time.
class PhidStore(object):
    def __init__(self):
        self.phids = set()
        self.users = {}

    def add(self, phid):
        self.phids.add(phid)

    def load_from_phabricator(self, phab, cachedir):
        phidapifunc = lambda: phab.phid.query(phids=list(phids))
        self.query = call_phab_via_cache(cachedir, "phidquery", phidapifunc)

    def name(self, phid):
        try:
            return self.query[phid]['name']
        except KeyError:
            return None

    def get_user(self, phid):
        retval = self.users.get(phid)
        if not retval:
            retval = self.users[phid] = User(phid)
            retval.phidstore = self
        return retval


class User(object):
    def __init__(self, phid):
        assert phid
        self.phid = phid
        self.tasks = []
        self.phidstore = None

    @property
    def name(self):
        return self.phidstore.name(self.phid)

# Scrape the HTML for a Phabricator workboard, and return a simple dict
# that represents the workboard.  The HTML is pre-retrieved via cron job
# that snarfs the HTML as much as hourly.  This function accesses the
# cache via timestamp, which is rounded to the nearest hour in the file
# name.
def parse_workboard_html(wbtime):
    # File name will look something like workboard-2014-12-22T00.html,
    # which corresponds to midnight on 2014-12-22
    filename = 'workboard-{:%Y-%m-%dT%H%Z}.html'.format(wbtime)
    htmlhandle = open(os.path.join(WORKBOARD_HTML_CACHE, filename))
    soup = BeautifulSoup(htmlhandle)
    columns = soup.find_all(class_="phui-workpanel-view")
    retval = {}
    for col in columns:
        state = col.find(class_="phui-action-header-title").strings.next()
        objnames = col.find_all(class_="phui-object-item-objname")
        for objname in objnames:
            retval[objname.string] = state
    return retval


# Take data structures representing the task states in two workboards,
# and return a dict that contains the tasks for which the state changed
# between the two workboards.  The contents of each item in the dict
# should be a tuple with the old state and the new state.
def get_workboard_diff(old_workboard, new_workboard):
    allkeys = list(set(old_workboard.keys()).union(new_workboard.keys()))
    diff = {}
    for key in allkeys:
        oldvalue = old_workboard.get(key)
        newvalue = new_workboard.get(key)
        if oldvalue != newvalue:
            diff[key] = (oldvalue, newvalue)
    return diff


def get_activity_for_tasks(phab, cachedir, tasks):
    tasknums = [int(string.lstrip(x, "T")) for x in tasks]
    activityquery = lambda: phab.maniphest.gettasktransactions(ids=tasknums)
    activity = call_phab_via_cache(
        cachedir, "gettasktransactions", activityquery)
    return activity


# Really lame overzealous caching implementation that's only currently
# useful as a developer convenience.  It stores queries to Phabricator
# based on named token.  Purging is entirely manual, even if the query
# changes (no signature checking).
def call_phab_via_cache(cachedir, key, apicall):
    picklefile = os.path.join(cachedir, key + ".pickle")
    try:
        result = pickle.load(open(picklefile))
    except IOError:
        result = apicall()
        pickle.dump(result, open(picklefile, "wb"))
    return result


# Return an item if it's relevant to our current search, or {} if it isn't.
# Also populate the PHIDs that will eventually need to be resolved.
def get_filtered_transactions_for_task(taskfeed, phidstore):
    transactions = []
    for tact in taskfeed:
        item = {}
        item['transactionType'] = tact["transactionType"]
        item['timestamp'] = int(tact['dateCreated'])
        item['authorPHID'] = tact['authorPHID']
        phidstore.add(item['authorPHID'])
        if (tact["transactionType"] == "status" or
            tact["transactionType"] == "title"):
            item['oldValue'] = tact['oldValue']
            item['newValue'] = tact['newValue']
        elif (tact["transactionType"] == "reassign"):
            item['oldValue'] = tact['oldValue']
            if tact['oldValue']:
                phidstore.add(item['oldValue'])
            item['newValue'] = tact['newValue']
            if tact['newValue']:
                phidstore.add(item['newValue'])
        elif (tact["transactionType"] == "projectcolumn" and
                tact["oldValue"]["projectPHID"] == MWCORETEAM_PHID):
            oldvalphids = tact['oldValue']['columnPHIDs']
            if isinstance(oldvalphids, dict):
                item['oldValue'] = oldvalphids.values()[0]
                phidstore.add(item['oldValue'])
            else:
                item['oldValue'] = None
            item['newValue'] = tact['newValue']['columnPHIDs'][0]
            phidstore.add(item['newValue'])
        else:
            item = {}
        if item:
            transactions.append(item)
    return transactions


def build_taskstate_from_transactions(transactions, start, end):
    taskstate = {'column': {},
                 'status': {},
                 'assignee': {},
                 'title': {}}
    taskstate['actorset'] = set()
    for tact in transactions:
        ttime = dt.fromtimestamp(tact['timestamp'], tz.tzutc())
        if ttime > end:
            break
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
        # TODO: make a series of unit tests out of this
        tmap = {'projectcolumn': 'column',
                'status': 'status',
                'reassign': 'assignee',
                'title': 'title'}
        ttype = tact['transactionType']
        if tact['transactionType'] in tmap:
            if ttime >= start and not taskstate.get(tmap[ttype]):
                taskstate[tmap[ttype]]['start'] = tact['oldValue']
            elif ttime < start:
                taskstate[tmap[ttype]]['start'] = tact['newValue']
            taskstate[tmap[ttype]]['end'] = tact['newValue']
        if ttime > start and tact['authorPHID']:
            taskstate['actorset'].add(tact['authorPHID'])
    if taskstate.get('assignee') and taskstate['assignee']['end']:
        taskstate['actorset'].add(taskstate['assignee']['end'])
    return taskstate


def build_highlights_for_actor(transactions, taskstate, actor, start, end, config):
    highlights = []
    wbphids = config['workboard_state_phids']

    def wbfinished(wbstate):
        return (wbstate == wbphids['done'] or
                wbstate == wbphids['archive'])

    for task in actor.tasks:
        for tact in transactions[task]:
            ttime = dt.fromtimestamp(tact['timestamp'], tz.tzutc())
            if ttime < start and ttime > end:
                pass
            if (tact['transactionType'] == 'reassign' and
                actor.phid == tact['newValue'] and
                actor.phid != tact['oldValue']):
                if tact['authorPHID'] == actor.phid:
                    highlights.append({'type': 'claim',
                                       'task': task})
                else:
                    highlights.append({'type': 'receive',
                                       'author': tact['authorPHID'],
                                       'task': task})
            elif (tact['transactionType'] == 'reassign' and
                actor.phid == tact['oldValue'] and
                actor.phid != tact['newValue']):
                if tact['authorPHID'] == actor.phid:
                    highlights.append({'type': 'give',
                                       'recipient': tact['newValue'],
                                       'task': task})
                elif tact['authorPHID'] == tact['newValue']:
                    highlights.append({'type': 'surrender',
                                       'recipient': tact['newValue'],
                                       'task': task})
            elif (tact['transactionType'] == 'projectcolumn' and
                tact['oldValue'] == wbphids['done'] and
                tact['newValue'] == wbphids['archive']):
                    #*yawn*
                    pass
            elif (tact['transactionType'] == 'projectcolumn' and
                not wbfinished(tact['oldValue']) and
                wbfinished(tact['newValue'])):
                    highlights.append({'type': 'completed',
                                       'task': task})
                                 
        if tact['transactionType'] == 'projectcolumn':
            taskstate['column'] = tact['newValue']
        elif tact['transactionType'] == 'status':
            taskstate['status'] = tact['newValue']
        elif tact['transactionType'] == 'reassign':
            taskstate['assignee'] = tact['newValue']
        if ttime > start and tact['authorPHID']:
            taskstate['actorset'].add(tact['authorPHID'])
    if taskstate.get('assignee'):
        taskstate['actorset'].add(taskstate['assignee'])
        if (taskstate[task].get('assignee') == actor.phid):
            print "yay"
    for tact in transactions:
        if tact['authorPHID']:
            taskstate['actorset'].add(tact['authorPHID'])
        
    if taskstate.get('assignee'):
        taskstate['actorset'].add(taskstate['assignee'])
    return ractions


def render_transaction(tact, phidstore):
    time = dt.fromtimestamp(
        tact['timestamp']).strftime("%Y-%m-%d %H:%M UTC")
    author = phidstore.name(tact['authorPHID'])
    retval = ""
    if tact['transactionType'] == 'projectcolumn':
        if tact['oldValue']:
            oldcolumn = phidstore.name(tact['oldValue'])
        else:
            oldcolumn = '(none)'
        newcolumn = phidstore.name(tact['newValue'])
        if oldcolumn != newcolumn:
            retval = "  {0} {1} Column: '{2}' '{3}'".format(
                time, author, oldcolumn, newcolumn)
    elif tact['transactionType'] == 'status':
        oldstatus = str(tact['oldValue'])
        newstatus = tact['newValue']
        retval = "  {0} {1} Status: '{2}' '{3}'".format(
            time, author, oldstatus, newstatus)
    elif tact['transactionType'] == 'reassign':
        if tact['oldValue']:
            oldassignee = phidstore.name(tact['oldValue'])
        else:
            oldassignee = '(unassigned)'
        if tact['newValue']:
            newassignee = phidstore.name(tact['newValue'])
        else:
            newassignee = '(unassigned)'
        retval = "  {0} {1} Assignee: '{2}' '{3}'".format(
            time, author, oldassignee, newassignee)
    return retval


def render_actor(actor, phidstore, transactions, start, end, taskstate, config):
    retval = "Actor: " + actor.name + "\n"
    for task in actor.tasks:
        #retval += "  Task T{0}".format(task) + "\n"
        #for tact in transactions[task]:
            #ttime = dt.fromtimestamp(tact['timestamp'], tz.tzutc())
            #if ttime > start and ttime < end:
                #retval += "  "
                #retval += render_transaction(tact, phidstore) + "\n"
        assignee = taskstate[task]['assignee']
        column = taskstate[task]['column']
        status = taskstate[task]['status']
        if (assignee.get('start') == actor.phid and 
            assignee.get('end') != actor.phid):
            retval += "  Unassigned from T" + task + "\n"
        if (assignee.get('start') != actor.phid and
            assignee.get('end') == actor.phid):
            retval += "  Assigned to T" + task + "\n"
        if assignee.get('end') == actor.phid:
            if (column.get('start') != column.get('end')):
                retval += "  T" + task + ": "
                if(column.get('start')):
                    retval += phidstore.name(column['start']) + " -> "
                retval += phidstore.name(column['end']) + "\n"
            if (status['start'] != status['end']):
                retval += "  T" + task + ": "
                if(column['start']):
                    retval += status['start'] + " -> "
                retval += status['end'] + "\n"
    return retval


def main():
    phab = phabricator.Phabricator()
    cachedir = WORKBOARD_PICKLE_CACHE
    start = dateutil.parser.parse("2014-12-22T0:00PST")
    end = dateutil.parser.parse("2014-12-23T0:00PST")
    old_workboard = parse_workboard_html(start)
    new_workboard = parse_workboard_html(end)
    diff = get_workboard_diff(old_workboard, new_workboard)
    allkeys = list(set(old_workboard.keys()).union(new_workboard.keys()))
    activity = get_activity_for_tasks(phab, cachedir, allkeys)
    config = global_config

    transactions = {}
    phidstore = PhidStore()
    for tasknum, taskfeed in activity.iteritems():
        tacts = get_filtered_transactions_for_task(taskfeed, phidstore)
        transactions[tasknum] = tacts

    taskstate = {}
    for task in transactions.keys():
        taskstate[task] = build_taskstate_from_transactions(
                            transactions[task], start, end)
        for actorphid in taskstate[task]['actorset']:
            assert actorphid
            phidstore.get_user(actorphid).tasks.append(task)

    phidstore.load_from_phabricator(phab, cachedir)
    for phid, actor in phidstore.users.iteritems():
        print render_actor(actor, phidstore, transactions, start, end, taskstate, config),

if __name__ == "__main__":
    main()
