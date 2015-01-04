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


class TaskStore(object):
    def __init__(self, tasknums=set()):
        self.tasknums = tasknums

    def load_from_phabricator(self, phab, cachedir):
        taskquerycall = lambda: phab.maniphest.query(ids=self.tasknums)
        self.query = call_phab_via_cache(
            cachedir, "taskquery", taskquerycall)
        self.bytasknum = {}
        for phid, task in self.query.iteritems():
            self.bytasknum[task['id']] = task


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


def get_activity_for_tasks(phab, cachedir, tasknums):
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


def build_taskstate_from_transactions(transactions, start, end, config):
    taskstate = {'column': {},
                 'status': {},
                 'assignee': {},
                 'title': {}}
    taskstate['actorset'] = set()
    wbstate = config['workboard_state_phids']
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
        if (tact['transactionType'] == 'projectcolumn' and
            tact['oldValue'] != wbstate['feedback'] and
            tact['newValue'] == wbstate['feedback']):
            taskstate['waitingsince'] = ttime
        if (tact['transactionType'] == 'projectcolumn' and
            tact['oldValue'] != wbstate['indev'] and
            tact['newValue'] == wbstate['indev']):
            taskstate['workingsince'] = ttime
    if taskstate.get('assignee') and taskstate['assignee']['end']:
        taskstate['actorset'].add(taskstate['assignee']['end'])
    return taskstate


def render_actor(actor, phidstore, transactions, start, end, taskstate, config, taskstore):
    wbstate = config['workboard_state_phids']
    retval = "=====================\n"
    retval += "Actor: " + actor.name + "\n"
    for task in actor.tasks:
        assignee = taskstate[task]['assignee']
        column = taskstate[task]['column']
        status = taskstate[task]['status']
        title = taskstore.bytasknum[task]['title']

        taskarray = []
        if (assignee.get('start') == actor.phid and 
            assignee.get('end') != actor.phid):
            taskarray.append("    Unassigned\n")
        if assignee.get('end') == actor.phid:
            newitem = (assignee.get('start') != actor.phid and
                       assignee.get('end') == actor.phid)
 
            if (column.get('start') == wbstate['done'] and
                column.get('end') == wbstate['archive']):
                pass
            elif (column.get('start') != column.get('end') and 
                  column.get('end') != wbstate['feedback']):
                taskval = "    "
                if newitem:
                    taskval += "Assigned and "
                if((column.get('start') == wbstate['todo'] or 
                    not column.get('start')) and
                   column['end'] == wbstate['indev']):
                    taskval += "Started\n"
                elif(column['end'] == wbstate['done'] or
                     column['end'] == wbstate['archive']):
                    taskval += "Completed\n"
                else:
                    if(column.get('start')):
                        taskval += phidstore.name(column['start']) + " -> "
                    taskval += phidstore.name(column['end']) + "\n"
                taskarray.append(taskval)
            elif (status['start'] != status['end']):
                taskval = "    "
                if newitem:
                    taskval += "Assigned and "
                if(column['start']):
                    taskval += status['start'] + " -> "
                taskval += status['end'] + "\n"
                taskarray.append(taskval)
            elif newitem:
                taskarray.append("    Assigned\n")
        if (column.get('start')  == wbstate['indev'] == column['end'] and
            assignee.get('end') == actor.phid):
            taskval = "    Still working on it (since "
            taskval += taskstate[task]['workingsince'].strftime("%a, %b %d")
            taskval += ")\n"
            taskarray.append(taskval)
        if (wbstate['feedback'] == column.get('end') and
            assignee.get('end') == actor.phid):
            taskval = "    Waiting for feedback since "
            taskval += taskstate[task]['waitingsince'].strftime("%a, %b %d")
            taskval += "\n"
            taskarray.append(taskval)
        if taskarray:
            retval += "  T" + task + ": " + title + "\n"
            for line in taskarray:
                retval += line


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
    alltasknums = [int(string.lstrip(x, "T")) for x in allkeys]
    activity = get_activity_for_tasks(phab, cachedir, alltasknums)
    config = global_config

    transactions = {}
    phidstore = PhidStore()
    taskstore = TaskStore(alltasknums)
    for tasknum, taskfeed in activity.iteritems():
        tacts = get_filtered_transactions_for_task(taskfeed, phidstore)
        transactions[tasknum] = tacts

    taskstate = {}
    for task in transactions.keys():
        taskstate[task] = build_taskstate_from_transactions(
                            transactions[task], start, end, config)
        for actorphid in taskstate[task]['actorset']:
            assert actorphid
            phidstore.get_user(actorphid).tasks.append(task)

    phidstore.load_from_phabricator(phab, cachedir)
    taskstore.load_from_phabricator(phab, cachedir)
    for phid, actor in phidstore.users.iteritems():
        print render_actor(actor, phidstore, transactions, start, end, taskstate, config, taskstore),

if __name__ == "__main__":
    main()
