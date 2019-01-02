#!/home/pi/venv/bin/python

'''Driver for the LED globe.'''

import Adafruit_GPIO as GPIO
import Adafruit_SSD1306 as SSD1306
import aiohttp.web
import asyncio
import atexit
import bisect
import contextlib
import datetime
import logging
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import random
import _rpi_ws281x as ws

from . import common


def random_rgb():
    '''Generate a random triple of ints in [0, 255], with a 0 for white.'''
    r = lambda: random.randrange(256)
    return r(), r(), r(), 0


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


class LCD:
    '''A class for driving an OLED LCD.'''

    def __init__(self, pin, address):
        self._disp = SSD1306.SSD1306_128_64(rst=pin, i2c_address=address)
        self._disp_begin()

        self._img = PIL.Image.new('1', (self._disp.width, self._disp.height))
        self._draw = PIL.ImageDraw.Draw(self._img)
        self._font = PIL.ImageFont.truetype('inconsolata.ttf', 48)

    @common.profile
    def _disp_begin(self):
        self._disp.begin()

    @common.profile
    def _disp_clear(self):
        self._disp.clear()

    @common.profile
    def _disp_image(self):
        self._disp.image(self._img)

    @common.profile
    def _disp_display(self):
        self._disp.display()

    @common.profile
    async def show(self):
        self._disp_clear()
        self._disp_image()
        self._disp_display()

    async def clear(self):
        self._draw.rectangle((0, 0, self._disp.width, self._disp.height),
                             outline=0, fill=0)

    async def rectangle(self, coords, fill=0, outline=1):
        self._draw.rectangle(coords, fill=fill, outline=outline)

    async def ellipse(self, coords, fill=0, outline=1):
        self._draw.ellipse(coords, fill=fill, outline=outline)

    async def line(self, coords, fill=1):
        self._draw.line(coords, fill=fill)

    async def polygon(self, coords, fill=0, outline=1):
        self._draw.polygon(coords, fill=fill, outline=outline)

    async def text(self, text, coords, fill=1):
        self._draw.text(coords, text, font=self._font, fill=fill)


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

    @common.profile
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


class Globe:
    '''A globe contains an RGB LED string, an LCD display, and 6 buttons.'''

    def __init__(self, app, mode):
        self.mode = mode
        self.color = (0, 0, 0, 255) if mode == common.Mode.RGBW else random_rgb()
        self.target = None

        self._lcd = LCD(pin=14, address=0x3d)
        self._leds = Pixels(size=13, pin=18, brightness=255)

        gpio = GPIO.get_platform_gpio()

        def add_button(pin, coro):
            def callback(_):
                asyncio.run_coroutine_threadsafe(coro(), app.loop)
            gpio.setup(pin, GPIO.IN)
            gpio.add_event_detect(
                pin, GPIO.RISING, callback=callback, bouncetime=200)

        for channel, pin in enumerate((21, 13, 16, 26)):
            add_button(pin, self._on_color_pressed(channel))

        asyncio.ensure_future(
            self._walk_loop() if mode == common.Mode.WALK else
            self._dance_loop() if mode == common.Mode.DANCE else
            self.show())

    @property
    def hexcolor(self):
        return ''.join('{:02x}'.format(c) for c in self.color)

    async def show(self):
        asyncio.ensure_future(self._redraw_lcd())
        await self._leds.show(self.color)

    async def _redraw_lcd(self):
        now = datetime.datetime.now()
        if (now - self._last_lcd_refresh).total_seconds() < 0.5:
            return

        await self._lcd.clear()

        # render the current color as 4 hex digits.
        await self._lcd.text(self.hexcolor[::2], (10, 10), 1)

        # render empty/filled circles to indicate the mode.
        for i in range(len(common.Mode) - 1):
            active = i == self.mode.value
            x, y = 115, 5 + i * 15
            await self._lcd.ellipse((x, y, x + 10, y + 10), fill=active, outline=1)

        await self._lcd.show()
        self._last_lcd_refresh = now

    async def _walk_loop(self):
        if self.target is None or self.color == self.target:
            self.target = random_rgb()
        color = list(self.color)
        for j, (cchan, tchan) in enumerate(zip(self.color, self.target)):
            if cchan < tchan:
                color[j] += 1
            if cchan > tchan:
                color[j] -= 1
        self.color = tuple(color)
        asyncio.ensure_future(self.show())
        await asyncio.sleep(0.1)
        asyncio.ensure_future(self._walk_loop())

    async def _dance_loop(self):
        self.color = common.random_rgb()
        asyncio.ensure_future(self.show())
        await asyncio.sleep(1)
        asyncio.ensure_future(self._dance_loop())

    def _on_color_pressed(self, idx):
        async def increment_color():
            if self.mode == common.Mode.RGBW:
                color = list(self.color)
                value = color[idx]
                value = (value - (value % 16) + 16) % 256
                color[idx] = value
                self.color = tuple(color)
                asyncio.ensure_future(self.show())
        return increment_color


if __name__ == '__main__':
    if _DEBUG:
        logging.getLogger('asyncio').setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG)

    app = aiohttp.web.Application()

    globe = Globe(app, common.Mode(int(sys.argv[2])))

    async def get_color(req):
        return aiohttp.web.Response(body=globe.hexcolor,
                                    content_type='text/plain')

    async def set_color(req):
        globe.color = hex_to_rgbw(await req.post())
        await globe.show()
        return aiohttp.web.Response(text='ok')

    app.add_routes([aiohttp.web.get('/color', get),
                    aiohttp.web.post('/color', post)])

    aiohttp.web.run_app(app, port=8888)
