#!/usr/bin/env python

from bs4 import BeautifulSoup
import phabricator
import json
import datetime
import dateutil.parser
import os

MWCORETEAM_PHID = "PHID-PROJ-oft3zinwvih7bgdhpfgj"
WORKBOARD_HTML_CACHE = '/home/robla/2014/phabworkboard-data/html'

def parse_workboard_html(wbtime):
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


def main():
    #phab = phabricator.Phabricator()
    #phabcache = populate_phab_cache(phab, MWCORETEAM_PHID)
    #workboardcache = populate_workboard_cache()
    #work_out_workboard_diffs()
    #generate_report()
    old_workboard = parse_workboard_html(dateutil.parser.parse("2014-12-22T0:00PST"))
    new_workboard = parse_workboard_html(dateutil.parser.parse("2014-12-23T0:00PST"))
    allkeys = list(set(old_workboard.keys()).union(new_workboard.keys()))
    for key in allkeys:
        oldvalue = old_workboard.get(key)
        newvalue = new_workboard.get(key)
        if oldvalue == newvalue:
            print "  {0}: {1}".format(key, oldvalue)
        else:
            print "* {0}: {1}->{2}".format(key, oldvalue, newvalue)
    #print json.dumps(old_workboard, indent=4, sort_keys=True)
    #print json.dumps(new_workboard, indent=4, sort_keys=True)

main()

