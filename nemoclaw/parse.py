import json, sys
t = open("/tmp/oc.json").read()
def f(o, k):
    if isinstance(o, dict):
        for a, b in o.items():
            if a == k:
                return b
            r = f(b, k)
            if r is not None:
                return r
    if isinstance(o, list):
        for x in o:
            r = f(x, k)
            if r is not None:
                return r
try:
    d = json.loads(t[t.find("{"):])
    txt = " | ".join(x["text"] for x in d["result"]["payloads"])
    el = float(sys.argv[3]) - float(sys.argv[2])
    print("OPENCLAW", repr(sys.argv[1][:20]), "%.1fs" % el, "tools=", f(d, "toolSummary"), "::", repr(txt[:600]))
except Exception as ex:
    print("OPENCLAW FAIL", sys.argv[1][:20], ex, t[:300])
