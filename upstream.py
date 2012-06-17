#!/usr/bin/env python2.7
# -*- coding: utf8 -*-

# COPYDOWN (C) You
# All code related to Upstream and code contained in this file
# Is released fully into the public domain

from gevent import monkey
monkey.patch_all()
from gevent import queue
from gevent import event
from gevent import pool
from gevent import socket as gsocket
import gevent
import sys
import os
import logging
import socket
import ssl
import re
import glob
import shelve
import subprocess
import bz2
import time
import fcntl
import binascii
import urllib
import hashlib
import argparse
try:
    import simplejson as json
except ImportError:
    import json
from StringIO import StringIO
from _yenc import encode_string
from xml.dom.minidom import getDOMImplementation
logging.basicConfig(level=logging.INFO)

TEMPDIR='./temp'

rivermeta = {}
filemeta = {}
nfo = None
filenfo = {}

class UpstreamNNTP:
    def __init__(self, server, username=None, password=None, port=-1, ssl=False):
        self.server = server
        self.username = username
        self.password = password
        self.port = port
        if port == -1:
            self.port = 119 if ssl == False else 563
        self.ssl = ssl
        self.connected = False
    
    def connect(self):
        if self.connected:
            raise Exception, "Already Connected"
        if self.ssl:
            self.psock = socket.socket()
            self.sock = ssl.wrap_socket(self.psock)
        else:
            self.sock = socket.socket()
        self.sock.connect((self.server, self.port))
        result = self.sock.recv(1024)
        if result[:3] != '200':
            raise Exception, 'Server not accepting connection'
        if self.username:
            self.sock.send('authinfo user %s\r\n' % self.username)
            result = self.sock.recv(1024)
            if result[:3] == '381': # PASS required
                if not self.password:
                    raise Exception, "Password required"
                self.sock.send('authinfo pass %s\r\n' % self.password)
                result = self.sock.recv(1024)
                if result[:3] != '281':
                    raise Exception, 'NNTP Login Incorrect'
        self.connected = True

    def post(self, data):
        global bytes
        if not self.connected:
            raise Exception, "Not Connected"
        self.sock.send("POST\r\n")
        self.sock.recv(2048)
        while len(data) > 0:
            sent = self.sock.send(data)
            bytes += sent
            data = data[sent:]
        self.sock.send("\r\n.\r\n")
        result = self.sock.recv(2048)
        if result[:3] == '441':
            raise Exception, "NNTP Error %s" % repr(result)
        return result[:-2]

    def article(self, message_id):
        if not self.connected:
            raise Exception, "Not Connected"
        self.sock.send("ARTICLE %s\r\n" % message_id)
        output = self.sock.recv(16384)
        if output[:3] == '430':
            raise Exception, "No such article"
        elif output[:3] == '412':
            raise Exception, "Not in a newsgroup"
        elif output[:1] == '4':
            raise Exception, output
        if not output[-5:] == '\r\n.\r\n':
            while True:
                output += self.sock.recv(16384)
                if output[-5:] == '\r\n.\r\n': break
        output = output[:-5]
        rheaders, article = output.split('\r\n\r\n')
        headers = rheaders.split('\r\n')
        head = dict((k,v) for k,v in (a.split(': ', 1) for a in headers[1:]))
        return head, article

    def date(self):
        if not self.connected:
            raise Exception, "Not Connected"
        self.sock.send("DATE\r\n")
        result = self.sock.recv(1024)
        return result[:-2]

    def quit(self):
        if not self.connected:
            raise Exception, "Not Connected"
        self.sock.send("QUIT\r\n")
        self.sock.recv(1024)
        self.sock.close()
        self.connected = False

def worker_thread(ready_event):
    global conn_workers
    logging.info("Starting new NNTP thread")
    tries = 0
    while True:
        try:
            n = UpstreamNNTP(server, username, password, port, use_ssl)
            n.connect()
            logging.info("NNTP thread %s started" % n)
            ready_event.set(True)
        except Exception, e:
            logging.info("NNTP thread failed! %s" % e.args[-1])
            if tries == 3:
                ready_event.set(False)
                return False, e.args[-1]
            tries += 1
            continue
        while True:
            try:
                messageid, data, name, customheaders, \
                yencdata, finish = river_queue.get(True, 60)
                ypart, ytotal, ybegin, yend = yencdata
                headers = []
                ydata, ycrc, ylen = encode_string(data)
                mcrc = '%08x' % (binascii.crc32(data) & 2**32L-1)
                headers.append('From: %s' % poster)
                headers.append('Newsgroups: %s' % group)
                headers.append('Subject: %s' % subject.format(name=name))
                headers.append('Message-ID: <%s>' % messageid)
                headers += customheaders
                outdata = '\r\n'.join(headers)+'\r\n\r\n'
                outdata += '=ybegin part=%d total=%d line=128 size=%d name=%s\r\n' % (ypart, ytotal, ylen, name)
                outdata += '=ypart begin=%d end=%d\r\n' % (ybegin, yend)
                outdata += ydata.replace('\r\n.','\r\n..')
                outdata += '\r\n=yend size=%d pcrc32=%s' % (ylen, mcrc)
                result = n.post(outdata)
                finish.set()
            except gevent.queue.Empty:
                try:
                    n.date()
                except:
                    logging.info("NNTP thread %s timed out" % n)
                    break

def init_connections():
    global river_queue, river_pool
    river_queue = queue.Queue()
    river_pool = []
    ready_pool = []
    for _ in range(connections):
        r = event.AsyncResult()
        g = gevent.Greenlet(worker_thread, r)
        river_pool.append(g)
        ready_pool.append(r)
        g.start()
    failed = 0
    for i in ready_pool:
        ret = i.wait()
        if ret == False:
            failed += 1
    if failed == connections:
        logging.fatal("Upstream could not be started")
        exit(1)
    pass

def init_metadata():
    global meta, TEMPDIR
    if not os.path.exists(TEMPDIR):
        logging.info("temp directory %s does not exist, using /tmp/upstream" % TEMPDIR)
        TEMPDIR = "/tmp/upstream"
        if not os.path.exists(TEMPDIR):
            os.mkdir(TEMPDIR)
        internal_files = []
    meta = shelve.open(TEMPDIR + "/.metadata")
    for i in files:
        if not os.path.exists(i):
            logging.fatal("File %s does not exist" % i)
            exit(1)
        fsize = os.stat(i).st_size
        bn = os.path.basename(i)
        if not bn in meta:
            meta[bn] = fsize
            meta[bn+'/segtotal'] = fsize / segbytes + (1 if fsize % segbytes > 0 else 0)
            meta[bn+'/arttotal'] = fsize / articlesize + (1 if fsize % articlesize > 0 else 0)
            meta[bn+"/segleft"] = range(fsize / segbytes + (1 if fsize % segbytes > 0 else 0))
            meta[bn+"/artleft"] = range(fsize / articlesize + (1 if fsize % articlesize > 0 else 0))

def handle_segment(ifile, seg):
    global meta, par_pool, article_pool
    seg_articles = range(seg*segsize, seg*segsize+segsize)
    gart = []
    for i in seg_articles:
        if i in meta[ifile+"/artleft"]:
            gart.append(article_pool.spawn(handle_article, ifile, seg, i))
    par = par_pool.spawn(par_segment, ifile, seg)
    gevent.joinall(gart)
           
def run_par2(args):
    p = subprocess.Popen(args, stdout=subprocess.PIPE,\
                         stderr=subprocess.STDOUT)
    fcntl.fcntl(p.stdout, fcntl.F_SETFL, os.O_NONBLOCK)
    while True:
        gsocket.wait_read(p.stdout.fileno())
        d = p.stdout.read(2048)
        if d == '':
            break
        #print d
    p.stdout.close()
           
def par_segment(ifile, seg):
    global meta, TEMPDIR, article_pool
    fsize = meta[ifile]
    byte_start = seg * segbytes
    byte_len = min(fsize-byte_start, segbytes)
    mainfile = open(ifile)
    mainfile.seek(byte_start)
    bn = os.path.basename(ifile)
    parfilename = TEMPDIR + '/' + bn + ".%03d" % (seg)
    parfile = open(parfilename,'w')
    parfile.write(mainfile.read(byte_len))
    parfile.close()
    if not (os.path.exists(parfilename+'.par2')):
        run_par2(['par2', 'c', '-b'+str(parblocks), '-c1',TEMPDIR+'/'+bn+'.%03d' % (seg)])
    try:
        os.unlink(parfilename)
    except:
        pass
    parfiles = glob.glob(parfilename+'.*par2')
    for i in parfiles:
        article_pool.spawn(handle_par, ifile, seg, i)
    t = meta[ifile+'/segleft']
    t.remove(seg)
    meta[ifile+'/segleft'] = t

def handle_par(ifile, seg, name):
    global meta, TEMPDIR
    data = open(name).read()
    ypart = 1
    ytotal = 1
    ybegin = 1
    yend = len(data)
    ydata = [ypart, ytotal, ybegin, yend]
    article, crc32, bytes = upload_article(data, os.path.basename(name), ydata)
    meta[ifile+"/par/"+str(seg)+"/"+os.path.basename(name)] = [article, crc32, bytes]
    os.unlink(name)
    
def handle_article(ifile, seg, art):
    fsize = meta[ifile]
    byte_start = art * articlesize
    byte_len = min(fsize-byte_start, articlesize)
    ypart = art - (seg * segsize)
    ytotal = fsize / segbytes + (1 if fsize % segbytes > 0 else 0)
    ybegin = byte_start
    yend = byte_start + byte_len
    ydata = [ypart, ytotal, ybegin, yend]
    t = meta[ifile+'/artleft']
    t.remove(art)
    meta[ifile+'/artleft'] = t
    meta.sync()
    afile = open(ifile)
    afile.seek(byte_start)
    fdata = afile.read(byte_len)
    afile.close()
    article, crc32, bytes = upload_article(fdata, ifile+".%03d" % (seg), ydata)
    del fdata
    meta[ifile+"/art/"+str(art)] = [article, crc32, bytes]

def upload_article(data, fname, yencdata, headers=[]):
    global river_queue
    messageid = "%f@%s" % (time.time(), messagecontext)
    crc32 = "%08x" % (binascii.crc32(data) & 0xffffffff)
    bytes = len(data)
    finish = event.Event()
    river_queue.put((messageid, data, fname, headers, yencdata, finish))
    finish.wait()
    return messageid, crc32, bytes

def write_river():
    impl = getDOMImplementation()
    xmlfile = impl.createDocument(None, 'river', None)
    doc = xmlfile.documentElement
    if nfo:
        outnfo = upload_nfo(nfo)
        if outnfo:
            rivermeta['nfo'] = outnfo
    if len(rivermeta) > 0:
        rmnode = xmlfile.createElement('rivermeta')
        doc.appendChild(rmnode)
        for i in rivermeta:
            rminode = xmlfile.createElement(i)
            rmnode.appendChild(rminode)
            rminode.appendChild(xmlfile.createTextNode(rivermeta[i]))
    for i in files:
        bn = os.path.basename(i)
        fsize = os.stat(i).st_size
        fnode = xmlfile.createElement('file')
        doc.appendChild(fnode)
        fnode.setAttribute('bytes', str(fsize))
        fnode.setAttribute('subject', subject.format(name=bn))
        fnode.setAttribute('group', group)
        fnode.setAttribute('name', bn)
        if bn in filenfo:
            outnfo = upload_nfo(filenfo[bn])
            if outnfo:
                filemeta[bn]['nfo'] = outnfo
        if bn in filemeta and len(filemeta[bn]) > 0:
            fmnode = xmlfile.createElement('filemeta')
            fnode.appendChild(fmnode)
            for j in filemeta[bn]:
                fmjnode = xmlfile.createElement(j)
                fmnode.appendChild(fmjnode)
                fmjnode.appendChild(xmlfile.createTextNode(filemeta[bn][j]))
        segs = range(fsize / segbytes + (1 if fsize % segbytes > 0 else 0))
        for seg in segs:
            snode = xmlfile.createElement('segment')
            fnode.appendChild(snode)
            snode.setAttribute('name', bn+".%03d"%(seg))
            snode.setAttribute('number',str(seg+1))
            arts = range(seg*segsize, seg*segsize+segsize)
            sbytes = 0
            for art in arts:
                if not bn+'/art/'+str(art) in meta: break
                anode = xmlfile.createElement('article')
                snode.appendChild(anode)
                messageid, crc, abytes = meta[bn+'/art/'+str(art)]
                sbytes += abytes
                anode.setAttribute('bytes', str(abytes))
                anode.setAttribute('number', str(art-(seg*segsize)+1))
                anode.setAttribute('crc', crc)
                anode.appendChild(xmlfile.createTextNode(messageid))
            snode.setAttribute('bytes', str(sbytes))
            pars = {k:v for k,v in meta.iteritems() if k.startswith(bn+'/par/'+str(seg)+'/')}.items()
            for par in pars:
                name = par[0].rsplit('/',1)[-1]
                messageid, _, pbytes = par[1]
                pnode = xmlfile.createElement('par')
                snode.appendChild(pnode)
                pnode.setAttribute('bytes', str(pbytes))
                pnode.setAttribute('name', name)
                pnode.setAttribute('number', str(pars.index(par)+1))
                pnode.appendChild(xmlfile.createTextNode(messageid))
        text_re = re.compile('>\n\s+([^<>\s].*?)\n\s+</', re.DOTALL)
        xmlout = text_re.sub('>\g<1></', xmlfile.toprettyxml(indent='  '))
        return xmlout

def create_rlink(xmlfile):
    bn = os.path.basename(output)
    yencdata = [0,0,0,0]
    data = bz2.compress(xmlfile)
    crc = "%08x" % (binascii.crc32(data) & 0xffffffff)
    open(TEMPDIR+'/'+bn+'.bz2','w').write(data)
    run_par2(['par2', 'c', '-b1', '-c1', TEMPDIR+'/'+bn+'.bz2'])
    outpars = {}
    pars = glob.glob(TEMPDIR+'/'+bn+'.bz2.*par2')
    for i in pars:
        outpars[os.path.basename(i)] = upload_article(open(i).read(), os.path.basename(i), yencdata)[0]
        os.unlink(i)
    os.unlink(TEMPDIR+'/'+bn+'.bz2')
    articles = range(len(data) / articlesize + (1 if len(data) % articlesize > 0 else 0))
    lastarticle = ""
    for i in articles[::-1]:
        headers = []
        if articles.index(i) == 0:
            headers.append("X-RLink-Name: %s" % bn)
            headers.append("X-RLink-Crc: %s" % crc)
            headers.append("X-RLink-Par2: %s" % json.dumps(outpars))
        if articles.index(i) == len(articles)-1:
            headers.append("X-RLink-Next: END")
            lastarticle,_,_ = upload_article(data[i*articlesize:(i+1)*articlesize], bn+".rlink.bz2", yencdata, headers)
        else:
            headers.append("X-RLink-Next: %s" % lastarticle)
            lastarticle,_,_ = upload_article(data[i*articlesize:(i+1)*articlesize], bn+".rlink.bz2", yencdata, headers)
    return "river:%s" % lastarticle

def upload_nfo(nfo):
    nfofile = open(nfo).read()
    if len(nfofile) > articlesize:
        print "ERROR: NFO file too large to fit in an article"
        return None
    yencdata = [0,0,0,0]
    article, _, _ = upload_article(nfofile, os.path.basename(nfo), yencdata, {})
    return article

last = 0
bytes = 0
bandwidth = 0
def human(bytes):
    if bytes > 1000000000:
        return "%0.3fGB" % (bytes/1000000000.0)
    if bytes > 1000000:
        return "%0.3fMB" % (bytes/1000000.0)
    if bytes > 1000:
        return "%0.3fKB" % (bytes/1000.0)
    return "%0.3fB" % bytes

def timer_bandwidth():
    global bytes, last, bandwidth
    while True:
        gevent.sleep(0.5)
        now = time.time()
        bandwidth = bytes / (now-last) * 0.5
        last = now
        bytes = 0
        arttotal = 0
        artdone = 0
        for i in files:
            bn = os.path.basename(i)
            if bn+'/arttotal' in meta:
                arttotal += meta[bn+'/arttotal']
            if bn+'/artleft' in meta:
                artdone += len(meta[bn+'/artleft'])
        artdone = arttotal - artdone
        print "Uploading [%d/%d articles] [%s/s]" % (artdone, arttotal, human(bandwidth))
    

def upload_files():
    global meta, TEMPDIR, par_pool, article_pool
    par_pool = pool.Pool(parthreads)
    article_pool = pool.Pool(connections+10)
    segment_pool = pool.Pool()
    gsegs = []
    print "Uploading articles and par files..."
    for i in files:
        bn=os.path.basename(i)
        for j in meta[bn+'/segleft']:
            segment_pool.spawn(handle_segment, bn, j)
    for i in gsegs:
        i.start()
    segment_pool.join()
    par_pool.join()
    article_pool.join()
    print "Articles uploaded."
    print "Uploading river link..."
    outfile = open(output,'w')
    xmlfile = write_river()
    outfile.write(xmlfile)
    outfile.close()
    print "River link uploaded."
    if rlink:
        frlink = open(output+'.rlink', 'w')
        outrlink = create_rlink(xmlfile)
        frlink.write(outrlink+'\n')
        logging.info("The rlink for this river file is %s" % outrlink)
        frlink.close()
    for i in files:
        bn=os.path.basename(i)
        del meta[bn+'/segleft']
        del meta[bn+'/artleft']
        # delete all of the article results in meta
        del meta[bn+'/segtotal']
        del meta[bn+'/arttotal']
        del meta[bn]

def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file
    return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Upload river files to usenet")
    parser.add_argument('-s','--server', required=True, help="Usenet server to connect to", dest='server')
    parser.add_argument('-o','--output', help="Where to output river file (default is stdout)", dest='output')
    parser.add_argument('-u','--username', help="Usenet username to connect with", dest='username')
    parser.add_argument('-p','--password', help="Usenet password to connect with", dest='password')
    parser.add_argument('-c','--connections', help="Max connections to usenet", dest='connections', default=10, type=int)
    parser.add_argument('-r', '--rlink', help="Create river link (currently always true)", dest='rlink', action='store_true', default=True)
    parser.add_argument('-m', '--meta', help="Metadata for river file (e.g. -m name=\"river\" description=\"river\")", dest='meta', nargs='+')
    parser.add_argument('-f', '--filemeta', help="Metadata for file contained within river file (e.g. -f fileinriverfile.mp3/name=\"river\" fileinriverfile.mp3/description=\"river\")", dest='filemeta', nargs='+')
    parser.add_argument('-n','--nfo', help="NFO file to upload with this river", dest='nfo')
    parser.add_argument('--filenfo', help="NFO file to upload with a river file (e.g. --filenfo fileinriverfile.mp3=nfofile.nfo)", dest='fnfo', nargs='+')
    parser.add_argument('--ssl', help="Connect to usenet with SSL", dest='ssl', action='store_true', default=False)
    parser.add_argument('--port', help="Port to connect to usenet with (default 119; 563 with SSL)", dest='port', type=int, default=-1)
    parser.add_argument('--subject', help="Subject in usenet articles", dest='subject', default="[RIVER] \"{name}\"")
    parser.add_argument('-d', '--domain', help="Domain in message IDs <mid@domain>", dest='domain', default="riverup")
    parser.add_argument('-g', '--group', help="Usenet newsgroup to post articles in", dest='group', default="alt.binaries.test")
    parser.add_argument('--poster', help="The from header in articles", dest='poster', default="river@riverup")
    parser.add_argument('--articlesize', help="The size of uploaded articles", dest='articlesize', default=650000, type=int)
    parser.add_argument('--segmentsize', help="Number of articles in a segment", dest='segmentsize', default=4, type=int)
    parser.add_argument('--parblocks', help="For 1/parblocks redundancy (4 parblocks is 25%% redundancy)", dest='parblocks', default=4, type=int)
    parser.add_argument('--parthreads', help="Max number of par2 instances running at once (should be number of cores plus one)", dest='parthreads', default=5, type=int)
    parser.add_argument('files', help="Files to be added to river file", nargs='+')
    ns = parser.parse_args(sys.argv[1:])
    server = ns.server
    username = ns.username
    password = ns.password
    port = ns.port
    use_ssl = ns.ssl
    connections = ns.connections
    articlesize = ns.articlesize
    segsize = ns.segmentsize
    segbytes = segsize * articlesize
    parblocks = ns.parblocks
    parthreads = ns.parthreads
    messagecontext = ns.domain
    poster = ns.poster
    group = ns.group
    rlink = ns.rlink
    subject = ns.subject
    files = ns.files
    output = ns.output
    if ns.meta:
        for i in ns.meta:
            k, v = i.split('=', 1)
            rivermeta[k] = v
    if ns.filemeta:
        for i in ns.filemeta:
            k, v = i.split('=', 1)
            f, k = k.split('/', 1)
            if not os.path.basename(f) in filemeta:
                filemeta[os.path.basename(f)] = {k: v}
            else:
                filemeta[os.path.basename(f)][k] = v
    if ns.nfo:
        if not os.path.exists(ns.nfo):
            print >> stderr, "ERROR: File %s does not exist, nfo not added" % ns.nfo
            nfo = None
        else:
            nfo = ns.nfo
        
    if ns.fnfo:
        for i in ns.fnfo:
            f, n = i.split('=', 1)
            if not os.path.exists(n):
                print >> stderr, "ERROR: File %s does not exist, nfo not added" % n
            filenfo[os.path.basename(f)] = n
    
    #sys.exit(0)
    par = which('par2')
    if par == None:
        raise Exception, "Par2 not found in path. It is required for Upstream to function"
    else:
        print "Found par2 at", par
    
    init_connections()
    init_metadata()
    logging.info('Starting upload')
    gup = gevent.Greenlet(upload_files)
    timer = gevent.Greenlet(timer_bandwidth)
    gup.start()
    timer.start()
    gup.join()
    meta.close()
    logging.info('Upload completed')
