#!/usr/bin/env python

from bs4 import BeautifulSoup
soup = BeautifulSoup(open("test/data/MediaWiki-Core-Team Board.html"))
#with open("/tmp/mwcoreboard-pretty.html","w") as text_file:
#    text_file.write(soup.prettify())


columns = soup.find_all(class_="phui-workpanel-view")
for col in columns:
    print col.find(class_="phui-action-header-title").strings.next()
    objnames=col.find_all(class_="phui-object-item-objname")
    for objname in objnames:
        print objname.string

