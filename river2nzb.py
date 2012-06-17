#!/usr/bin/env python2.7
# -*- coding: utf8 -*-

# COPYDOWN (C) You
# All code related to Upstream and code contained in this file
# Is released fully into the public domain

from xml.dom.minidom import parseString
import time

def river_to_nzb(river):
    # Load the river file
    riverfile = parseString(river)
    rdoc = riverfile.documentElement
    if rdoc.tagName != 'river':
        raise Exception, "Not a valid river file"
    rfiles = rdoc.getElementsByTagName('file')
    if len(rfiles) == 0:
        raise Exception, "No files in this river file"
    rfiles_out = []
    rfiles_par = []
    for i in rfiles:
        rsegments = i.getElementsByTagName('segment')
        rgroup = i.getAttribute('group')
        for j in rsegments:
            rarticles = []
            for k in j.getElementsByTagName('article'):
                rarticles.append([k.firstChild.nodeValue.strip(), k.getAttribute('bytes')])
            rfiles_out.append([j.getAttribute('name'),rarticles, rgroup])
            for k in j.getElementsByTagName('par'):
                rfiles_par.append([k.getAttribute('name'),[[k.firstChild.nodeValue.strip(), k.getAttribute('bytes')]], rgroup])
    rfiles_out += rfiles_par
    
    # Output the nzb file
    impl = getDOMImplementation()
    ndt = impl.createDocumentType('nzb', '-//newzBin//DTD NZB 1.0//EN', 'http://www.newzbin.com/DTD/nzb/nzb-1.0.dtd')
    nzbfile = impl.createDocument(None, 'nzb', ndt)
    nzbfile.encoding = 'utf-16'
    ndoc = nzbfile.documentElement
    ndoc.setAttribute('xmlns','http://www.newzbin.com/DTD/2003/nzb');
    ntime = str(int(time.time()))
    for i in rfiles_out:
        nfile = nzbfile.createElement('file')
        ndoc.appendChild(nfile)
        
        # Set file attributes
        nfile.setAttribute('poster','river2nzb@upstream')
        nfile.setAttribute('date',ntime)
        nfile.setAttribute('subject', 'river2nzb "%s" yEnc (0/0)' % i[0])
        
        # Create groups element
        ngroups = nzbfile.createElement('groups')
        nfile.appendChild(ngroups)
        ngroup = nzbfile.createElement('group')
        ngroups.appendChild(ngroup)
        ngroup.appendChild(nzbfile.createTextNode(i[2]))
        
        # Create segments element
        nsegments = nzbfile.createElement('segments')
        nfile.appendChild(nsegments)
        for j in range(len(i[1])):
            nsegment = nzbfile.createElement('segment')
            nsegments.appendChild(nsegment)
            nsegment.setAttribute('number', str(j+1))
            nsegment.setAttribute('bytes', i[1][j][1])
            nsegment.appendChild(nzbfile.createTextNode(i[1][j][0]))
            
    out = nzbfile.toxml()
    return out, out2
    
if __name__ == '__main__':
    import sys
    river = open(sys.argv[1]).read()
    outfile = sys.argv[1]+'.nzb'
    nzb, nzb2 = river_to_nzb(river)
    open(outfile,'w').write(nzb)
    print "Written to %s" % outfile
