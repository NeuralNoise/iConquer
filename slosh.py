#!/usr/bin/env python
"""

Copyright (c) 2008  Dustin Sallings <dustin@spy.net>
"""

import xml.sax
import xml.sax.saxutils
import cStringIO as StringIO

from twisted.web import server, resource
from twisted.internet import task

class Topic(resource.Resource):

    max_queue_size = 100
    max_id = 1000000000
    id = ''

    def __init__(self, filter=lambda a: a):
        self.last_id = 0
        self.filter = filter
        self.objects=[]
        self.requests=[]
        self.known_sessions={}
        self.formats={'xml': self.__transmit_xml, 'json': self.__transmit_json}
        self.methods = {'GET': self._do_GET, 'POST': self._do_POST}
        l = task.LoopingCall(self.__touch_active_sessions)
        l.start(5, now=False)

    def _do_GET(self, request):
        session = request.getSession()
        if session.uid not in self.known_sessions:
            print "New session: ", session.uid
            self.known_sessions[session.uid] = self.last_id
            session.notifyOnExpire(self.__mk_session_exp_cb(session.uid))
        if not self.__deliver(request):
            #print "id / append request:", self.id, request
            self.requests.append(request)
            request.notifyFinish().addBoth(self.__req_finished, request)
        #print "return server.NOT_DONE_YET"
        return server.NOT_DONE_YET

    def deliver_msg(self, filtered):
        # Store the object
        #filtered = self.filter(request.args)
        if filtered:
            self.objects.append(filtered)
            if len(self.objects) > self.max_queue_size:
                #print "del self.objects[0]", self.objects[0]
                del self.objects[0]
            self.last_id += 1
            if self.last_id > self.max_id:
                #print "self.last_id", self.last_id, self.max_id
                self.last_id = 1
            print "id/requests: ", self.id, self.requests
            for r in self.requests:
                self.__deliver(r)
        return False #self.__mk_res(request, 'ok', 'text/plain')

    def _do_POST(self, request):
        # Store the object
        filtered = self.filter(request.args)
        if filtered:
            self.objects.append(filtered)
            if len(self.objects) > self.max_queue_size:
                del self.objects[0]
            self.last_id += 1
            if self.last_id > self.max_id:
                self.last_id = 1
            for r in self.requests:
                self.__deliver(r)
        return self.__mk_res(request, 'ok', 'text/plain')

    def render(self, request):
        return self.methods[request.method](request)

    def __since(self, n):
        # If a nonsense ID comes in, scoop them all up.
        if n > self.last_id:
            #print "Overriding last ID from %d to %d" % (n, self.last_id - 1)
            n = self.last_id - 1
        f = max(0, self.last_id - n)
        rv = self.objects[0-f:] if self.last_id > n else []
        return rv, self.last_id - n

    def __req_finished(self, whatever, request):
        self.requests.remove(request)

    def __touch_active_sessions(self):
        for r in self.requests:
            r.getSession().touch()

    def __deliver(self, req):
        sid = req.getSession().uid
        since = req.args.get('n')
        if since:
            since=int(since[0])
        else:
            since = self.known_sessions[sid]
        data, oldsize = self.__since(since)
        #print "id/data/since: ", self.id, data, since
        if data:
            fmt = 'xml'
            if req.path.find(".") > 0:
                fmt=req.path.split(".")[-1]
            self.formats.get(fmt, self.__transmit_xml)(req, data, oldsize)
            req.finish()
        self.known_sessions[sid] = self.last_id
        return data

    def __transmit_xml(self, req, data, oldsize):
        class G(xml.sax.saxutils.XMLGenerator):
            def doElement(self, name, value, attrs={}):
                self.startElement(name, attrs)
                if value is not None:
                    self.characters(value)
                self.endElement(name)

        s=StringIO.StringIO()
        g=G(s, 'utf-8')

        g.startDocument()
        g.startElement("res",
            {'max': str(self.last_id), 'saw': str(oldsize),
                'delivering': str(len(data)) })

        for h in data:
            g.startElement("p", {})
            for k,v in h.iteritems():
                for subv in v:
                    g.doElement(k, subv)
            g.endElement("p")
        g.endElement("res")

        g.endDocument()

        s.seek(0, 0)
        req.write(self.__mk_res(req, s.read(), 'text/xml'))

    def __transmit_json(self, req, data, oldsize):
        import cjson
        jdata=[dict(s) for s in data]
        j=cjson.encode({'max': self.last_id, 'saw': oldsize,
            'delivering': len(data), 'res': jdata})
        req.write(self.__mk_res(req, j, 'text/plain'))

    def __mk_session_exp_cb(self, sid):
        def f():
            print "Expired session", sid
            del self.known_sessions[sid]
        return f

    def __mk_res(self, req, s, t):
        req.setHeader("content-type", t)
        req.setHeader("content-length", str(len(s)))
        return s

class Topics(resource.Resource):
    alltopics = {}
    pid = None
    #def __init__(self):

    def getChild(self, path, request):
        t=path.split('/', 1)[0]
        if t.find(".") > 0:
            t=t.split(".", 1)[0]
            topic = self.getChildWithDefault(t, request)
        else:
            topic = Topic()
            import random
            topic.id = path+" - "+str(random.randint(0,1000))
            self.putChild(t, topic)
            self.alltopics[str(self.pid)+'-'+t] = topic
            print "Registered new topic:", self.pid, t, self.alltopics
        return topic

