#!/usr/bin/env python

from bs4 import BeautifulSoup
import phabricator
import json
import datetime
import dateutil.parser
import os
import string
import pickle

MWCORETEAM_PHID = "PHID-PROJ-oft3zinwvih7bgdhpfgj"
WORKBOARD_HTML_CACHE = '/home/robla/2014/phabworkboard-data/html'
WORKBOARD_PICKLE_CACHE = '/home/robla/2014/phabworkboard-data/pickles'


# Keep track of all of the objects with associated PHIDs.  Aggregate all of
# PHIDs so that we only need to make one call to Phabricator.phid.query to
# lookup a big batch of PHIDs, rather than making dozens/hundreds of calls to
# look them up one at a time.
class PhidStore(object):
    def __init__(self):
        self.phids = set()

    def add(self, phid):
        self.phids.add(phid)

    def load_from_phabricator(self, phab, cachedir):
        phidapifunc = lambda: phab.phid.query(phids=list(phids))
        self.query = call_phab_via_cache(cachedir, "phidquery", phidapifunc)

    def name(self, phid):
        return self.query[phid]['name']


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
        if (tact["transactionType"] == "status"):
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


def process_transactions(transactions, start, end):
    taskstate = {}
    taskstate['actorset'] = set()
    for tact in transactions:
        ttime = datetime.datetime.fromtimestamp(tact['timestamp'],
                                                dateutil.tz.tzutc())
        if ttime > end:
            break
        if tact['authorPHID']:
            if ttime > start and ttime < end:
                taskstate['actorset'].add(tact['authorPHID'])
        if tact['transactionType'] == 'projectcolumn':
            taskstate['column'] = tact['newValue']
        elif tact['transactionType'] == 'status':
            taskstate['status'] = tact['newValue']
        elif tact['transactionType'] == 'reassign':
            taskstate['assignee'] = tact['newValue']
    if taskstate.get('assignee'):
        taskstate['actorset'].add(taskstate['assignee'])
    return taskstate


def render_transaction(tact, phidstore):
    time = datetime.datetime.fromtimestamp(
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


def render_actor(actor, tasklist, phidstore, transactions, start, end, taskstate):
    retval = "Actor: " + phidstore.name(actor) + "\n"
    for task in tasklist:
        retval += "  Task T{0}".format(task) + "\n"
        for tact in transactions[task]:
            ttime = datetime.datetime.fromtimestamp(tact['timestamp'],
                                                    dateutil.tz.tzutc())
            if ttime > start and ttime < end:
                retval += "  "
                retval += render_transaction(tact, phidstore) + "\n"
        if task.get('assignee') == actor:
            retval += task['actorset'].add(task['assignee'])
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

    transactions = {}
    phidstore = PhidStore()
    for tasknum, taskfeed in activity.iteritems():
        tacts = get_filtered_transactions_for_task(taskfeed, phidstore)
        transactions[tasknum] = tacts

    actortasks = {}
    taskstate = {}
    for task in transactions.keys():
        taskstate[task] = process_transactions(transactions[task], start, end)
        for actor in taskstate[task]['actorset']:
            assert actor
            if actortasks.get(actor):
                actortasks[actor].append(task)
            else:
                actortasks[actor] = [task]

    phidstore.load_from_phabricator(phab, cachedir)
    for actor, tasklist in actortasks.iteritems():
        print render_actor(actor, tasklist, phidstore,
                           transactions, start, end, taskstate),

if __name__ == "__main__":
    main()
