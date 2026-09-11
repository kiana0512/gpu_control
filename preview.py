from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import os
os.chdir('/tmp/gpu-control-5070-web-adapter/dist')
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not os.path.isfile(self.translate_path(self.path)):
            self.path = '/index.html'
        super().do_GET()
ThreadingHTTPServer(('127.0.0.1', 18751), Handler).serve_forever()
