# phab-wbstatus
Script to generate a summary of Phabricator workboard activity for a given time period

Wrote this about a month ago, and now I'm regretting not writing the README a month ago.

The basic idea is to create a time-based summary that paints a reasonably clear picture of the activity associated with a workboard, given a timespan.  As of this writing, the technique the script uses to get this done is to fetch the HTML associated with a workboard.

The script is reasonably well commented, especially the main() function, so go read that for more info.

Required library:
https://github.com/disqus/python-phabricator

...among others.  The others are all packaged for Ubuntu, and presumably Debian, Fedora, Red Hat, etc.
