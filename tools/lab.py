import os
import socketserver, threading, http.server
S={"mode":"up"}
PAGE={"up":"<h1>Acme Shop</h1><p>Welcome. Status: all systems normal.</p>","defaced":"<h1>HACKED by anon</h1>"}
CTRL="""<!doctype html><meta charset=utf-8><title>PulseGuard test lab</title>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h2>PulseGuard test lab</h2><p>Test site: <b>http://127.0.0.1:8799/</b> &mdash; now: <b>%s</b></p>
<p><a href="/control/up"><button>Bring site UP</button></a>
<a href="/control/down"><button>Make site DOWN (HTTP 500)</button></a>
<a href="/control/defaced"><button>Deface page</button></a></p>
<h3>Send a heartbeat</h3>
<p>PulseGuard address <input id=a value="http://127.0.0.1:8080" size=24><br>
Token <input id=t value="example-heartbeat-token" size=30>
<button onclick="fetch(document.getElementById('a').value+'/ping/'+document.getElementById('t').value,{mode:'no-cors'}).then(()=>r.textContent='ping sent '+new Date().toLocaleTimeString())">Ping now</button></p>
<p id=r></p><p>TCP test port: <b>127.0.0.1:8798</b> (always open)</p></body>"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,code,body):
        b=body.encode(); self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=self.path.split("?")[0]
        if p.startswith("/control/"):
            S["mode"]=p.rsplit("/",1)[1]; self.send_response(302); self.send_header("Location","/control"); self.end_headers()
        elif p=="/control": self.send(200,CTRL%S["mode"])
        elif S["mode"]=="down": self.send(500,"<h1>Internal Server Error</h1>")
        else: self.send(200,PAGE.get(S["mode"],PAGE["up"]))
class T(socketserver.BaseRequestHandler):
    def handle(self): pass
socketserver.ThreadingTCPServer.allow_reuse_address=True
http.server.ThreadingHTTPServer.allow_reuse_address=True

def main() -> None:
    http_port = int(os.environ.get("LAB_HTTP_PORT", "8799"))
    tcp_port = int(os.environ.get("LAB_TCP_PORT", "8798"))
    threading.Thread(target=socketserver.ThreadingTCPServer(("127.0.0.1", tcp_port), T).serve_forever, daemon=True).start()
    print("Test lab running. Control panel: http://127.0.0.1:8799/control   (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", http_port), H).serve_forever()

if __name__ == "__main__":
    main()
