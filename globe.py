#!/home/pi/venv/bin/python

'''Driver for the LED lamp.'''

import Adafruit_GPIO as GPIO
import Adafruit_SSD1306 as SSD1306
import aiohttp.web
import asyncio
import atexit
import bisect
import contextlib
import datetime
import enum
import functools
import logging
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import random
import _rpi_ws281x as ws

_DEBUG = False


def random_rgb():
    '''Generate a random triple of ints in [0, 255], with a 0 for white.'''
    r = lambda: random.randrange(256)
    return r(), r(), r(), 0


def profile(f):
    '''Profile a function's elapsed time.'''
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        start = datetime.datetime.now()
        f(*args, **kwargs)
        elapsed = (datetime.datetime.now() - start).total_seconds()
        logging.debug('{} took {:.1f}ms'.format(f.__name__, 1000 * elapsed))
    return wrapper


class LCD:
    '''A class for driving an OLED LCD.'''

    def __init__(self, pin, address, width=128, height=64):
        self.width = width
        self.height = height

        self._disp = SSD1306.SSD1306_128_64(rst=pin, i2c_address=address)
        self._disp_begin()
        if (width, height) != (self._disp.width, self._disp.height):
            raise RuntimeError('oled screen size mismatch')

        self._img = PIL.Image.new('1', (self.width, self.height))
        self._draw = PIL.ImageDraw.Draw(self._img)
        self._font = PIL.ImageFont.truetype('inconsolata.ttf', 48)
        self._needs_showing = True

    @profile
    def _disp_begin(self):
        self._disp.begin()

    @profile
    def _disp_clear(self):
        self._disp.clear()

    @profile
    def _disp_image(self):
        self._disp.image(self._img)

    @profile
    def _disp_display(self):
        self._disp.display()

    async def show(self):
        if self._needs_showing:
            self._disp_clear()
            self._disp_image()
            self._disp_display()
            self._needs_showing = False

    async def clear(self):
        self._draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)
        self._needs_showing = True

    async def rectangle(self, coords, fill=0, outline=1):
        self._draw.rectangle(coords, fill=fill, outline=outline)
        self._needs_showing = True

    async def ellipse(self, coords, fill=0, outline=1):
        self._draw.ellipse(coords, fill=fill, outline=outline)
        self._needs_showing = True

    async def line(self, coords, fill=1):
        self._draw.line(coords, fill=fill)
        self._needs_showing = True

    async def polygon(self, coords, fill=0, outline=1):
        self._draw.polygon(coords, fill=fill, outline=outline)
        self._needs_showing = True

    async def text(self, text, coords, fill=1):
        self._draw.text(coords, text, font=self._font, fill=fill)
        self._needs_showing = True


class Gamma:
    R = [int(0.5 + 255 * (i / 255) ** 2.0) for i in range(256)]
    G = [int(0.5 + 255 * (i / 255) ** 2.0) for i in range(256)]
    B = [int(0.5 + 255 * (i / 255) ** 2.0) for i in range(256)]
    W = [int(0.5 + 255 * (i / 255) ** 2.0) for i in range(256)]


class Pixels:
    '''A class representing a strip of NeoPixels.'''

    def __init__(self, size, pin, brightness=128):
        self._size = size
        self._leds = ws.new_ws2811_t()

        atexit.register(self._cleanup)

        self._ws2811_reset()
        self._ws2811_setup(pin, brightness)
        self._ws2811_init()

    def __del__(self):
        if ws:
            self._cleanup()

    def __len__(self):
        return self._size

    def _cleanup(self):
        if self._leds is not None:
            ws.ws2811_fini(self._leds)
            ws.delete_ws2811_t(self._leds)
            self._leds = None

    @profile
    def _ws2811_reset(self):
        for c in range(2):
            chan = ws.ws2811_channel_get(self._leds, c)
            ws.ws2811_channel_t_count_set(chan, 0)
            ws.ws2811_channel_t_gpionum_set(chan, 0)
            ws.ws2811_channel_t_invert_set(chan, 0)
            ws.ws2811_channel_t_brightness_set(chan, 0)

    @profile
    def _ws2811_setup(self, pin, brightness):
        self._channel = ws.ws2811_channel_get(self._leds, 0)
        ws.ws2811_channel_t_gamma_set(self._channel, list(range(256)))
        ws.ws2811_channel_t_count_set(self._channel, self._size)
        ws.ws2811_channel_t_gpionum_set(self._channel, pin)
        ws.ws2811_channel_t_invert_set(self._channel, 0)
        ws.ws2811_channel_t_brightness_set(self._channel, brightness)
        ws.ws2811_channel_t_strip_type_set(self._channel, ws.SK6812_STRIP_RGBW)
        ws.ws2811_t_freq_set(self._leds, 800000)
        ws.ws2811_t_dmanum_set(self._leds, 5)

    @profile
    def _ws2811_init(self):
        resp = ws.ws2811_init(self._leds)
        if resp != 0:
            str_resp = ws.ws2811_get_return_t_str(resp)
            raise RuntimeError('ws2811_init failed with code '
                               '{} ({})'.format(resp, str_resp))

    @profile
    def _ws2811_render(self):
        resp = ws.ws2811_render(self._leds)
        if resp != 0:
            str_resp = ws.ws2811_get_return_t_str(resp)
            raise RuntimeError('ws2811_render failed with code '
                               '{} ({})'.format(resp, str_resp))

    @profile
    def _ws2811_led_get(self):
        return [self.int_to_rgbw(ws.ws2811_led_get(self._channel, idx))
                for idx in range(self._size)]

    @profile
    def _ws2811_led_set(self, idx, value):
        for idx in range(idx.start or 0, idx.stop or len(self), idx.step or 1):
            ws.ws2811_led_set(self._channel, idx, value)

    async def show(self, rgbw):
        await self.set_color(rgbw)
        self._ws2811_render()

    async def get_colors(self):
        return self._ws2811_led_get()

    async def set_color(self, rgbw, idx=None):
        if idx is None:
            idx = slice(None, None, None)
        if isinstance(idx, int):
            idx = slice(idx, idx + 1, 1)
        self._ws2811_led_set(idx, self.rgbw_to_int(rgbw))

    @staticmethod
    def rgbw_to_int(rgbw):
        r, g, b, w = rgbw
        G = Gamma
        return (G.W[w] << 24) | (G.G[g] << 16) | (G.R[r] << 8) | G.B[b]

    @staticmethod
    def int_to_rgbw(value):
        w = bisect.bisect(Gamma.W, (value >> 24) & 0xff)
        g = bisect.bisect(Gamma.G, (value >> 16) & 0xff)
        r = bisect.bisect(Gamma.R, (value >> 8) & 0xff)
        b = bisect.bisect(Gamma.B, value & 0xff)
        return  r, g, b, w


@enum.unique
class Mode(enum.Enum):
    RGBW = 0
    WALK = 1
    DANCE = 2
    NIGHTLIGHT = 3


class Globe:
    '''A globe contains an RGB LED string, an LCD display, and 6 buttons.'''

    def __init__(self, app, time_override=None):
        self.on = True
        self.mode = Mode.RGBW
        self.color = (0, 0, 0, 255)
        self.target = None

        self._time_override = time_override

        self._lcd = LCD(pin=14, address=0x3d)
        self._leds = Pixels(size=13, pin=18, brightness=255)
        self._last_lcd_refresh = datetime.datetime.now()

        gpio = GPIO.get_platform_gpio()

        def add_button(pin, coro):
            def callback(_):
                asyncio.run_coroutine_threadsafe(coro(), app.loop)
            gpio.setup(pin, GPIO.IN)
            gpio.add_event_detect(
                pin, GPIO.RISING, callback=callback, bouncetime=500)

        add_button(20, self._on_power_pressed)
        add_button(19, self._on_mode_pressed)
        for channel, pin in enumerate((21, 13, 16, 26)):
            add_button(pin, self._on_color_pressed(channel))

        asyncio.ensure_future(self.show())
        asyncio.ensure_future(self._clock_loop())

    def __str__(self):
        return 'Light<on={}, mode={}, time={}, color={}, target={}>'.format(
                self.on, self.mode, self.time, self.hexcolor, self.hextarget)

    @property
    def hexcolor(self):
        return ('{:02x}' * 4).format(*self.color)

    @property
    def hextarget(self):
        return ('{:02x}' * 4).format(*self.target) if self.target else ''

    @property
    def time(self):
        return self._time_override or datetime.datetime.now()

    @time.setter
    def time(self, override):
        self._time_override = override

    @property
    def nightlight_color(self):
        t = self.time
        is_dusk = t.hour == 19 and t.minute <= 30
        is_dawn = t.hour == 6 and t.minute >= 55
        return ((60, 40, 20, 0) if is_dusk else
                (40, 20, 0, 0) if self.is_night else
                (20, 60, 40, 0) if is_dawn else
                self.color)

    @property
    def is_night(self):
        return not 7 <= self.time.hour <= 18

    async def show(self):
        now = datetime.datetime.now()
        refresh_ok = (now - self._last_lcd_refresh).total_seconds() > 0.5
        if not self.on:
            color = (0, 0, 0, 0)
            if refresh_ok:
                self._last_lcd_refresh = now
                await self._lcd.clear()
        elif self.mode == Mode.NIGHTLIGHT:
            color = self.nightlight_color
            if refresh_ok:
                self._last_lcd_refresh = now
                await self._lcd.clear()
        else:
            color = self.color
            if refresh_ok:
                self._last_lcd_refresh = now
                await self._lcd.clear()
                await self._redraw_lcd()
        await self._leds.show(color)
        await self._lcd.show()
        print(self)

    async def _redraw_lcd(self):
        # render the current color as 4 hex digits.
        await self._lcd.text(self.hexcolor[::2], (10, 10), 1)

        # render empty/filled circles to indicate the mode.
        for i in range(len(Mode) - 1):
            active = i == self.mode.value
            x, y = 115, 5 + i * 15
            await self._lcd.ellipse((x, y, x + 10, y + 10), fill=active, outline=1)

    async def _clock_loop(self):
        if self.is_night:
            self.mode = Mode.NIGHTLIGHT
            await self.show()
        await asyncio.sleep(60)
        asyncio.ensure_future(self._clock_loop())

    async def _walk_loop(self):
        if self.on and self.mode == Mode.WALK:
            if self.target is None or self.color == self.target:
                self.target = random_rgb()
            color = list(self.color)
            for j, (cchan, tchan) in enumerate(zip(self.color, self.target)):
                if cchan < tchan:
                    color[j] += 1
                if cchan > tchan:
                    color[j] -= 1
            self.color = tuple(color)
            await self.show()
            await asyncio.sleep(0.2)
            asyncio.ensure_future(self._walk_loop())

    async def _dance_loop(self):
        if self.on and self.mode == Mode.DANCE:
            self.color = random_rgb()
            await self.show()
            await asyncio.sleep(1)
            asyncio.ensure_future(self._dance_loop())

    async def ensure_mode(self):
        if self.mode == Mode.WALK:
            asyncio.ensure_future(self._walk_loop())
        if self.mode == Mode.DANCE:
            asyncio.ensure_future(self._dance_loop())
        if self.mode == Mode.RGBW:
            asyncio.ensure_future(self.show())

    async def _on_power_pressed(self):
        self.on = not self.on
        await (self.ensure_mode() if self.on else self.show())

    async def _on_mode_pressed(self):
        if self.on and not self.is_night:
            self.mode = Mode((self.mode.value + 1) % len(Mode))
            self.target = None
            await self.ensure_mode()

    def _on_color_pressed(self, idx):
        async def increment_color():
            if self.on and self.mode == Mode.RGBW:
                color = list(self.color)
                value = color[idx]
                value = (value - (value % 16) + 16) % 256
                color[idx] = value
                self.color = tuple(color)
                await self.ensure_mode()
        return increment_color


def hex_to_rgbw(x):
    if x.startswith('#'):
        x = x[1:]
    if len(x) == 3:
        x += '0'
    if len(x) == 4:
        return list(int(c, 16) for c in x)
    if len(x) == 6:
        x += '00'
    if len(x) == 8:
        pairs = x[0:2], x[2:4], x[4:6], x[6:8]
        return list(int(c, 16) for c in pairs)
    return None


HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ background: #000; text-align: center; }}
#color {{ width: 350px; margin: 1em auto; }}
#hex {{ width: 350px; margin: 1em auto; color: #fff; font: 4em monospace; }}
</style>
<title>Globe</title>
<body>

<div id="color"></div>
<div id="hex">{0}</div>

<script src="iro.min.js"></script>
<script>
(new iro.ColorPicker("#color", {{
  width: 320,
  height: 320,
  color: {{r: {1}, g: {2}, b: {3} }},
  borderWidth: 1,
  borderColor: "#fff",
}})).on("color:change", function(color) {{
  let hex = color.hexString;
  let xhr = new XMLHttpRequest();
  xhr.open("POST", ".");
  xhr.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
  xhr.send("color=" + hex);
  document.getElementById('hex').innerHTML = [hex[1], hex[3], hex[5], '0'].join('');
}});
</script>
'''


if __name__ == '__main__':
    if _DEBUG:
        logging.getLogger('asyncio').setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG)

    with open('iro.min.js', 'rb') as handle:
        irojs_file = handle.read()

    app = aiohttp.web.Application()

    globe = Globe(app)

    async def get(req):
        return aiohttp.web.Response(
            body=HTML.format(globe.hexcolor[::2], *globe.color),
            content_type='text/html')

    async def post(req):
        data = await req.post()
        time = data.get('time')
        if time is not None:
            if time:
                parts = (int(x) for x in re.split(r'\D+', time))
                globe.time = datetime.datetime(*tuple(parts)[:6])
            else:
                globe.time = None
        color = data.get('color')
        if color is not None:
            globe.color = hex_to_rgbw(color)
            globe.mode == Mode.RGBW
        target = data.get('target')
        if target is not None:
            globe.target = hex_to_rgbw(target)
            globe.mode == Mode.WALK
        asyncio.ensure_future(globe.ensure_mode())
        return aiohttp.web.Response('ok')

    async def irojs(req):
        return aiohttp.web.Response(
            body=irojs_file, content_type='application/octet-stream')

    app.add_routes([
        aiohttp.web.get('/', get),
        aiohttp.web.post('/', post),
        aiohttp.web.get('/iro.min.js', irojs),
    ])

    aiohttp.web.run_app(app, port=80)
