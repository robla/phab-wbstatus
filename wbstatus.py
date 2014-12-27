#!/usr/bin/env python

from bs4 import BeautifulSoup
import phabricator
import json
import datetime
import dateutil.parser
import os
import string

MWCORETEAM_PHID = "PHID-PROJ-oft3zinwvih7bgdhpfgj"
WORKBOARD_HTML_CACHE = '/home/robla/2014/phabworkboard-data/html'

# Scrape the HTML for a Phabricator workboard, and return a simple dict
# that represents the workboard.  The HTML is pre-retrieved via cron job
# that snarfs the HTML as much as hourly.  This function accesses the 
# cache via timestamp, which is rounded to the nearest hour in the file
# name.
def parse_workboard_html(wbtime):
    # File name will look something like workboard-2014-12-22T00.html,
    # which corresponds to midnight on 2014-12-22
    htmlhandle = open(os.path.join(WORKBOARD_HTML_CACHE, 'workboard-{:%Y-%m-%dT%H%Z}.html'.format(wbtime)))
    soup = BeautifulSoup(htmlhandle)
    columns = soup.find_all(class_="phui-workpanel-view")
    retval = {}
    for col in columns:
        state = col.find(class_="phui-action-header-title").strings.next()
        objnames=col.find_all(class_="phui-object-item-objname")
        for objname in objnames:
            retval[objname.string]=state
    return retval

def print_all_project_tasks(phab, projectphid):
    taskquery = phab.maniphest.query(projectPHIDs=[projectphid])

    for taskphid, task in taskquery.iteritems():
        print task["id"]

#class PhabCache:
#    def __init__(phab, MWCORETEAM_PHID):
        

def populate_phab_cache(phab, MWCORETEAM_PHID):
    return

def populate_workboard_cache():
    #bs4_version()
    return

def work_out_workboard_diffs():
    return

def generate_report():
    return

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

def get_activity_for_tasks(phab, tasks):
    tasknums = [ int(string.lstrip(x,"T")) for x in tasks ]
    activity = phab.maniphest.gettasktransactions(ids=tasknums)
    return activity

def main():
    phab = phabricator.Phabricator()
    #phabcache = populate_phab_cache(phab, MWCORETEAM_PHID)
    #workboardcache = populate_workboard_cache()
    #work_out_workboard_diffs()
    #generate_report()
    start = dateutil.parser.parse("2014-12-22T0:00PST")
    end = dateutil.parser.parse("2014-12-23T0:00PST")
    old_workboard = parse_workboard_html(start)
    new_workboard = parse_workboard_html(end)
    diff = get_workboard_diff(old_workboard, new_workboard)
    activity = get_activity_for_tasks(phab, diff.keys())
    #phids = get_phids_from_activity(activity)

    columnmoves = []
    phids = set()
    for tasknum, taskfeed in activity.iteritems():
        for tact in taskfeed:
            if (tact["transactionType"] == "projectcolumn" and
                tact["oldValue"]["projectPHID"] == MWCORETEAM_PHID):
                item = {}
                item['timestamp'] = int(tact['dateCreated'])
                if isinstance(tact['oldValue']['columnPHIDs'], dict):
                    item['oldValue'] = tact['oldValue']['columnPHIDs'].values()[0]
                    phids.add(item['oldValue'])
                else:
                    item['oldValue'] = None
                item['newValue'] = tact['newValue']['columnPHIDs'][0]
                phids.add(item['newValue'])
                item['authorPHID'] = tact['authorPHID']
                phids.add(item['authorPHID'])
                columnmoves.append(item)
    
    import pprint
    pprint.pprint(list(phids), indent=4)
    phidquery = phab.phid.query(phids=list(phids))
    for move in columnmoves:
        time = datetime.datetime.fromtimestamp(move['timestamp']).strftime("%Y-%m-%d %H:%M UTC")
        if move['oldValue']:
            oldcolumn = phidquery[move['oldValue']]['name']
        else:
            oldcolumn = '(none)'
        newcolumn = phidquery[move['newValue']]['name']
        author = phidquery[move['authorPHID']]['name']
        print "{0} {1} {2} {3}".format(time, oldcolumn, newcolumn, author)

    #tasknums = [ int(string.lstrip(x,"T")) for x in diff.keys() ]
    #print json.dumps(tasknums, sort_keys=True)





    #print json.dumps(activity, indent=4, sort_keys=True)
    #print json.dumps(old_workboard, indent=4, sort_keys=True)
    #print json.dumps(new_workboard, indent=4, sort_keys=True)

main()

