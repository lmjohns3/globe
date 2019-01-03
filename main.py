#!/home/pi/venv/bin/python

'''Main / supervisor process for the globe LED lamp.'''

import Adafruit_GPIO as GPIO
import aiohttp
import aiohttp.web
import asyncio
import collections
import datetime
import json
import logging
import sys

import globe

class HM(collections.namedtuple('HM', 'h m')):

    def __str__(self):
        return '{}.{}'.format(self.h, self.m)


if __name__ == '__main__':
    #logging.getLogger('asyncio').setLevel(logging.DEBUG)
    #logging.basicConfig(level=logging.DEBUG)

    session = aiohttp.ClientSession()

    mode = None
    proc = None
    offset = 0
    managed_colors = {
        HM( 7,  0): '00201000',
        HM(19,  0): '20200020',
        HM(19, 30): '20100000',
    }

    # Globe subprocess controls.

    def is_managed():
        t = datetime.datetime.now() + datetime.timedelta(seconds=offset)
        now = HM(t.hour, t.minute)
        dawn = max(hm for hm in managed_colors if hm.h < 12)
        dusk = min(hm for hm in managed_colors if hm.h > 12)
        return not dawn < now < dusk

    async def start(m):
        global mode
        global proc
        mode = m
        if proc:
            proc.terminate()
        cmd = (sys.executable, 'globe.py', str(m.value))
        logging.info('starting globe: %s', cmd)
        proc = await asyncio.create_subprocess_exec(*cmd)

    if not is_managed():
        asyncio.ensure_future(start(globe.Mode.RGBW))

    async def loop():
        if is_managed():
            if mode != globe.Mode.MANAGED:
                await start(globe.Mode.MANAGED)
            t = datetime.datetime.now() + datetime.timedelta(seconds=offset)
            now = HM(t.hour, t.minute)
            color, delay = None, 1e100
            for hm, c in managed_colors.items():
                d = 60 * (now.h - hm.h) + (now.m - hm.m)
                if 0 <= d < delay:
                    color, delay = c, d
            await set_color(color)
        await asyncio.sleep(60)
        asyncio.ensure_future(loop())
    asyncio.ensure_future(loop())

    async def set_color(c):
        data = dict(color=c)
        for i in range(10):
            try:
                await session.post('http://localhost:8888/color', data=data)
                break
            except:
                await asyncio.sleep(3)

    async def get_color():
        async with session.get('http://localhost:8888/color') as resp:
            return await resp.text()

    # Button-press handlers for power and mode. These buttons control the globe
    # subprocess, which gets started/stopped for each mode.

    gpio = GPIO.get_platform_gpio()

    def add_button(pin, coro):
        def callback(_):
            asyncio.run_coroutine_threadsafe(coro(), app.loop)
        gpio.setup(pin, GPIO.IN)
        gpio.add_event_detect(
            pin, GPIO.RISING, callback=callback, bouncetime=300)

    async def on_power_pressed():
        pass
    add_button(20, on_power_pressed)

    async def on_mode_pressed():
        if not is_managed():
            next_mode = (mode.value + 1) % len(globe.Mode)
            await start(globe.Mode(next_mode or 1))
    add_button(19, on_mode_pressed)

    # HTTP interface.

    app = aiohttp.web.Application()

    with open('index.html', 'rb') as handle:
        html_file = handle.read()

    async def html(req):
        return aiohttp.web.Response(
            body=html_file, content_type='text/html')

    async def get_state(req):
        return aiohttp.web.Response(
            body=json.dumps(dict(
                color=await get_color(),
                now=datetime.datetime.now().isoformat(),
                offset=offset,
                mode=mode.value,
                managed_colors={str(k): v for k, v in managed_colors.items()},
            )),
            content_type='text/json')

    async def set_state(req):
        global offset
        data = await req.post()
        if 'offset' in data:
            offset = int(data['offset'])
        if 'color' in data and not is_managed():
            start(globe.Mode.RGBW)
            await set_color(data['color'])
        return aiohttp.web.Response(text='ok')

    with open('iro.min.js', 'rb') as handle:
        irojs_file = handle.read()

    async def irojs(req):
        return aiohttp.web.Response(
            body=irojs_file, content_type='text/javascript')

    app.add_routes([
        aiohttp.web.get('/', html),
        aiohttp.web.get('/state', get_state),
        aiohttp.web.post('/state', set_state),
        aiohttp.web.get('/iro.js', irojs),
    ])

    aiohttp.web.run_app(app, port=80)
