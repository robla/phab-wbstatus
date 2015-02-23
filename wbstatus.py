#!/usr/bin/env python
#
# Copyright 2015 Rob Lanphier, Wikimedia Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import argparse
from bs4 import BeautifulSoup
from xml.sax.saxutils import escape
import datetime
import dateutil.parser
import json
import os
import phabricator
import pickle
import string


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Generate a summary ' +
        'of Phabricator workboard activity for a given time period')
    parser.add_argument('--use-buggy-cache',
                        help='Overly aggressive cache that should ' +
                        'only be used for development purposes',
                        action='store_true')
    parser.add_argument('--start', help='Start of the interval for ' +
                        'the generated summary.  Default: 24 hours ' +
                        'before --end.')
    parser.add_argument('--end', help='End of the interval for ' +
                        'the generated summary.  Default: midnight at' +
                        ' start of today.')
    parser.add_argument('--config', help='Location of the config ' +
                        'file.', default='wbstatus-config.json')
    return parser.parse_args()


def get_config():
    """Read command line options and merge with whatever config file exists to
    create a config object we can lob around.
    """
    args = parse_arguments()

    with open(args.config) as fh:
        config = json.load(fh)

    if args.end:
        config['end'] = dateutil.parser.parse(args.end)
    else:
        now = datetime.datetime.utcnow()
        config['end'] = datetime.datetime(now.year, now.month, now.day,
                                          0, 0, 0, tzinfo=dateutil.tz.tzutc())

    if args.start:
        config['start'] = dateutil.parser.parse(args.start)
    else:
        config['start'] = config['end'] - datetime.timedelta(days=1)

    if not args.use_buggy_cache:
        config['cachedir'] = None

    return config


class PhidStore(object):
    """Keep track of all of the objects with associated PHIDs.  Aggregate all
    of PHIDs so that we only need to make one call to Phabricator.phid.query
    to lookup a big batch of PHIDs, rather than making dozens/hundreds of
    calls to look them up one at a time.
    """

    def __init__(self):
        self.phids = set()
        self.users = {}

    def add(self, phid):
        self.phids.add(phid)

    def load_from_phabricator(self, phab, cachedir):
        def phidapifunc():
            phab.phid.query(phids=list(self.phids))

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
    """The TaskStore is a wrapper around the Phabricator manifest.query API
    call, so this object indexes the result by task number.  This is necessary
    because the Phabricator API inexplicably doesn't return the result in such
    a way that the tasks can be easily looked up by task number.
    """

    def __init__(self, tasknums=set()):
        self.tasknums = tasknums

    def load_from_phabricator(self, phab, cachedir):
        def taskquerycall():
            phab.maniphest.query(ids=self.tasknums)

        self.query = call_phab_via_cache(
            cachedir, "taskquery", taskquerycall)
        self.bytasknum = {}
        for phid, task in self.query.iteritems():
            self.bytasknum[task['id']] = task


class User(object):
    """Yeah yeah, laugh it up at this tiny class, but there are big plans in
    store for this.  BIG PLANS I TELL YOU!
    """

    def __init__(self, phid):
        assert phid
        self.phid = phid
        self.tasks = []
        self.phidstore = None

    @property
    def name(self):
        return self.phidstore.name(self.phid)


def parse_workboard_html(wbtime, wbhtmlcache):
    """Scrape the HTML for a Phabricator workboard, and return a simple dict
    that represents the workboard.  The HTML is pre-retrieved via cron job
    that snarfs the HTML as much as hourly.  This function accesses the cache
    via timestamp, which is rounded to the nearest hour in the file name.
    """

    # File name will look something like workboard-2014-12-22T00.html,
    # which corresponds to midnight on 2014-12-22
    filename = 'workboard-{:%Y-%m-%dT%H%Z}.html'.format(wbtime)
    htmlhandle = open(os.path.join(wbhtmlcache, filename))
    soup = BeautifulSoup(htmlhandle)
    columns = soup.find_all(class_="phui-workpanel-view")
    retval = {}
    for col in columns:
        state = col.find(class_="phui-action-header-title").strings.next()
        objnames = col.find_all(class_="phui-object-item-objname")
        for objname in objnames:
            retval[objname.string] = state
    return retval


def get_activity_for_tasks(phab, cachedir, tasknums):
    """Pretty much the minimal wrapper around maniphest.gettasktransactions to
    use the cache.
    """
    def activityquery():
        phab.maniphest.gettasktransactions(ids=tasknums)

    activity = call_phab_via_cache(
        cachedir, "gettasktransactions", activityquery)
    return activity


def call_phab_via_cache(cachedir, key, apicall):
    """Really lame overzealous caching implementation that's only currently
    useful as a developer convenience.  It stores queries to Phabricator based
    on named token.  Purging is entirely manual, even if the query changes (no
    signature checking).
    """
    if not cachedir:
        result = apicall()
    else:
        print cachedir
        picklefile = os.path.join(cachedir, key + ".pickle")
        try:
            result = pickle.load(open(picklefile))
        except IOError:
            result = apicall()
            pickle.dump(result, open(picklefile, "wb"))
    return result


def get_filtered_transactions_for_task(taskfeed, phidstore, teamphid):
    """Return an item if it's relevant to our current search, or {} if it
    isn't. Also populate the PHIDs that will eventually need to be resolved.
    There's a fair amount of logic here for making the return value a bit more
    uniform than what is passed in.
    """
    # TODO: pass in MWCORETEAM_PHID instead of relying on global constant.
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
                tact["oldValue"]["projectPHID"] == teamphid):
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
    """Walk through the transactions and build up the state for a particular
    task at each end of the interval defined by "start" and "end". Also keep
    track of how long tasks have been in the "In Dev" and "Waiting for
    Review/Feedback" columns.
    """
    taskstate = {'column': {},
                 'status': {},
                 'assignee': {},
                 'title': {}}
    taskstate['actorset'] = set()
    wbstate = config['workboard_state_phids']
    for tact in transactions:
        ttime = datetime.datetime.fromtimestamp(
            tact['timestamp'], dateutil.tz.tzutc()
        )
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
        # (see unit test for example)
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


def render_actor(actor, phidstore, transactions, start, end, taskstate,
                 config, taskstore):
    """Return an HTML blob for a given user ("actor"), performing the many
    contortions necessary to have something read more-or-less like plain
    English.  The goal of this software is to present a simple view of things,
    so precision is compromised in the name of clarity and highlighting what's
    important.
    """
    # TODO: switch to bottle.SimpleTemplate or some other HTML template
    # solution
    wbstate = config['workboard_state_phids']
    retval = "<li class='userentry'><span class='user'>" + escape(actor.name)
    retval += "</span>\n"
    retval += "<ul>\n"
    for task in actor.tasks:
        assignee = taskstate[task]['assignee']
        column = taskstate[task]['column']
        status = taskstate[task]['status']
        title = taskstore.bytasknum[task]['title']

        # Stuff the things to be printed into an array.  If the array
        # is empty at the end of all of this, then we forego printing
        # the task number and title.
        taskarray = []
        if (assignee.get('start') == actor.phid and
                assignee.get('end') != actor.phid):
            taskarray.append("    <li>Unassigned</li>\n")
        if assignee.get('end') == actor.phid:
            newitem = (assignee.get('start') != actor.phid and
                       assignee.get('end') == actor.phid)
            # the move from "done" to "archive" isn't very interesting
            # so ignore it.
            if (column.get('start') == wbstate['done'] and
                    column.get('end') == wbstate['archive']):
                pass
            # We have a change, so there's likely something interesting
            # to report
            elif (column.get('start') != column.get('end')):
                taskval = "    <li class='taskstatus'>"
                if newitem:
                    taskval += "Assigned and "
                if((column.get('start') == wbstate['todo'] or
                    not column.get('start')) and
                   column['end'] == wbstate['indev']):
                    taskval += "Started</li>\n"
                elif(column['end'] == wbstate['feedback']):
                    taskval += "Asking for feedback</li>\n"
                elif(column['end'] == wbstate['done'] or
                     column['end'] == wbstate['archive']):
                    taskval += "Completed</li>\n"
                # Catchall in case one of the cases above doesn't do it.
                else:
                    if(column.get('start')):
                        taskval += phidstore.name(column['start']) + " -> "
                    taskval += phidstore.name(column['end']) + "\n"
                taskarray.append(taskval)
            elif (status['start'] != status['end']):
                taskval = "    <li class='taskstatus'>"
                if newitem:
                    taskval += "Assigned and "
                if(status['start']):
                    taskval += status['start'] + " -> "
                taskval += status['end'] + "</li>\n"
                taskarray.append(taskval)
            elif newitem:
                taskarray.append("    <li class='taskstatus'>Assigned</li>\n")
        if (column.get('start') == wbstate['indev'] == column['end'] and
                assignee.get('end') == actor.phid):
            taskval = "    <li class='taskstatus'>Still working on it (since "
            taskval += taskstate[task]['workingsince'].strftime("%a, %b %d")
            taskval += ")</li>\n"
            taskarray.append(taskval)
        if (column.get('start') == wbstate['feedback'] == column.get('end') and
                assignee.get('end') == actor.phid):
            taskval = "    <li class='taskstatus'>Waiting for feedback since "
            taskval += taskstate[task]['waitingsince'].strftime("%a, %b %d")
            taskval += "</li>\n"
            taskarray.append(taskval)
        # Now print out all of the activity for the task, or skip if
        # there hasn't been anything interesting to report.
        if taskarray:
            retval += "  <li>"
            retval += "<a href='https://phabricator.wikimedia.org/T" + \
                task + "'>"
            retval += "<span class='tasknum'>"
            retval += "T" + task
            retval += "</span>:  "
            retval += "<span class='tasktitle'>"
            retval += escape(title) + "</span></a>\n"
            retval += "  <ul>\n"
            for line in taskarray:
                retval += line
            retval += "  </ul>\n"
            retval += "  </li>\n"
    retval += "</ul></li>\n"
    return retval


def main():
    # Parse arguments and read config file plus various and sundry
    # other bits.
    config = get_config()
    start = config['start']
    end = config['end']

    # Scrape workboards from HTML (yes, "ewwww....").  At first, I
    # thought this was the only viable strategy, since most Phabricator
    # APIs don't return workboard state at all.  I discovered that I
    # could reconstruct all of the state I needed walking through the
    # transactions in a task (also, "ew", but not "ewwww.....").
    # It might be possible to eliminate scraping altogether, but one
    # would still need to keep track of which tasks got moved out of
    # the team project, which I haven't gotten to.  The advantage this
    # approach still presents is it provides a fairly narrowly scoped
    # list of issues (only those that are/were just visible on the
    # workboard; skipping long-since archived issues).
    old_workboard = parse_workboard_html(start, config['htmlcachedir'])
    new_workboard = parse_workboard_html(end, config['htmlcachedir'])
    allkeys = list(set(old_workboard.keys()).union(new_workboard.keys()))
    alltasknums = [int(string.lstrip(x, "T")) for x in allkeys]

    # Use the Phabricator API to fetch all of the activity for the list
    # of issues passed via "alltasknums".
    phab = phabricator.Phabricator()
    activity = get_activity_for_tasks(phab, config['cachedir'],
                                      alltasknums)

    # Build a sane view of the transactions, filtering out a lot of
    # noise and making the result a little more uniform and sane.
    # Also, start populating a list of PHIDs (Phabricator IDs used for
    # everything) in "phidstore".  In addition to storing the list of
    # PHIDs to lookup, the phidstore acts as a class factory and
    # registry for objects that can be referenced by PHID.
    transactions = {}
    phidstore = PhidStore()
    for tasknum, taskfeed in activity.iteritems():
        tacts = get_filtered_transactions_for_task(taskfeed, phidstore,
                                                   config['teamphid'])
        transactions[tasknum] = tacts

    # Walk through the transactions and build up the state for each
    # task at each end of the interval defined by "start" and "end".
    # Also keep track of how long tasks have been in the "In Dev" and
    # "Waiting for Review/Feedback" columns.  Start building a bunch of
    # User objects, and populating them lists of associated tasks.
    taskstate = {}
    for task in transactions.keys():
        taskstate[task] = build_taskstate_from_transactions(
            transactions[task], start, end, config)
        for actorphid in taskstate[task]['actorset']:
            assert actorphid
            phidstore.get_user(actorphid).tasks.append(task)

    # Look up what all of the PHIDs are, and squirrel away the resulting
    # metadata.
    phidstore.load_from_phabricator(phab, config['cachedir'])

    # The TaskStore is a wrapper around the Phabricator manifest.query
    # API call, indexing the result by task number.
    taskstore = TaskStore(alltasknums)
    taskstore.load_from_phabricator(phab, config['cachedir'])

    print """
<html>
<head>
 <style type="text/css">
    body {
        font-family: sans-serif;
        font-size: 13px;
        line-height: 18px;
    }
    .userentry {
        margin-top: 8px;
    }
    .user {
        color: #4B4D51;
        font-weight: bold;
        font-size: 13px;
        vertical-align: bottom;
    }
    .tasknum {
        color: #111;
        font-weight: bold;
    }
    .tasktitle {
        color: #6B748C;
    }
    a {
        text-decoration: none;
        color: #18559D;
        cursor: pointer;
    }
    a:hover {
        text-decoration: underline;
    }
    ul {
        list-style-type: none;
        padding-left:1em;
    }
 </style>
</head>
<body>
<ul>
"""
    # Spit out a text blob for each of the users.
    for phid in config['team'].keys():
        actor = phidstore.users[phid]
        print render_actor(actor, phidstore, transactions, start, end,
                           taskstate, config, taskstore),
    print """
</ul>
</body></html>
"""

if __name__ == "__main__":
    main()
