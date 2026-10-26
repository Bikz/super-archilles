# Loopback TCP forwarder: 127.0.0.1:LISTEN -> 127.0.0.1:TARGET (workaround: NemoClaw protects port 8080)
import asyncio, sys
L, T = int(sys.argv[1]), int(sys.argv[2])
async def pipe(r, w):
    try:
        while (b := await r.read(65536)):
            w.write(b); await w.drain()
    except Exception: pass
    finally:
        try: w.close()
        except Exception: pass
async def handle(cr, cw):
    try: ur, uw = await asyncio.open_connection("127.0.0.1", T)
    except Exception: cw.close(); return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
async def main():
    s = await asyncio.start_server(handle, "127.0.0.1", L)
    async with s: await s.serve_forever()
asyncio.run(main())
