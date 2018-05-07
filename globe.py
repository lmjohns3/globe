#!/home/pi/globe/venv/bin/python

'''Driver for the LED lamp.'''

import Adafruit_SSD1306
import arrow
import astral
import atexit
import datetime
import flask
import gpiozero
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import queue
import random
import _rpi_ws281x as ws
import threading
import time

from flask import render_template
from flask import request


def random_rgb():
    '''Generate a random triple of ints in [0, 255], with a 0 for white.'''
    r = lambda: random.randrange(256)
    return [r(), r(), r(), 0]


class LCD(threading.Thread):
    '''A function for driving an OLED LCD via a command queue.'''

    def __init__(self, pin, address, width=128, height=64):
        super().__init__()
        
        self.width = width
        self.height = height

        self._disp = Adafruit_SSD1306.SSD1306_128_64(rst=pin, i2c_address=address)
        self._disp.begin()
        if (width, height) != (self._disp.width, self._disp.height):
            raise RuntimeError('oled screen size mismatch')
        
        self._queue = queue.Queue()
        self._commands = []

    def run(self):
        img = PIL.Image.new('1', (self.width, self.height))
        draw = PIL.ImageDraw.Draw(img)
        font = PIL.ImageFont.truetype('static/inconsolata.ttf', 48)
        while True:
            commands = self._queue.get()
            if commands is None:
                break
            draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)
            for shape, coords, fill, outline in commands:
                if shape == 'rectangle':
                    draw.rectangle(coords, fill=fill, outline=outline)
                elif shape == 'ellipse':
                    draw.ellipse(coords, fill=fill, outline=outline)
                elif shape == 'line':
                    draw.line(coords, fill=fill)
                elif shape == 'polygon':
                    draw.polygon(coords, fill=fill, outline=outline)
                else:
                    draw.text(coords, shape, font=font, fill=fill)
            self._disp.clear()
            self._disp.image(img)
            self._disp.display()

    def show(self):
        self._queue.put(self._commands)
        self._commands = []

    def join(self):
        self._queue.put(None)
        super().join()

    def rectangle(self, coords, fill=0, outline=1):
        self._commands.append(('rectangle', coords, fill, outline))

    def ellipse(self, coords, fill=0, outline=1):
        self._commands.append(('ellipse', coords, fill, outline))

    def line(self, coords, fill=1):
        self._commands.append(('line', coords, fill, 1))

    def polygon(self, coords, fill=0, outline=1):
        self._commands.append(('polygon', coords, fill, outline))

    def text(self, text, coords, fill=1):
        self._commands.append((text, coords, fill, 1))


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

        for c in range(2):
            chan = ws.ws2811_channel_get(self._leds, c)
            ws.ws2811_channel_t_count_set(chan, 0)
            ws.ws2811_channel_t_gpionum_set(chan, 0)
            ws.ws2811_channel_t_invert_set(chan, 0)
            ws.ws2811_channel_t_brightness_set(chan, 0)

        self._channel = ws.ws2811_channel_get(self._leds, 0)
        ws.ws2811_channel_t_gamma_set(self._channel, list(range(256)))
        ws.ws2811_channel_t_count_set(self._channel, size)
        ws.ws2811_channel_t_gpionum_set(self._channel, pin)
        ws.ws2811_channel_t_invert_set(self._channel, 0)
        ws.ws2811_channel_t_brightness_set(self._channel, brightness)
        ws.ws2811_channel_t_strip_type_set(self._channel, ws.SK6812_STRIP_RGBW)

        ws.ws2811_t_freq_set(self._leds, 800000)
        ws.ws2811_t_dmanum_set(self._leds, 5)

        atexit.register(self._cleanup)

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

    def start(self):
        resp = ws.ws2811_init(self._leds)
        if resp != 0:
            str_resp = ws.ws2811_get_return_t_str(resp)
            raise RuntimeError('ws2811_init failed with code '
                               '{} ({})'.format(resp, str_resp))

    def show(self, rgbw):
        self[:] = rgbw
        resp = ws.ws2811_render(self._leds)
        if resp != 0:
            str_resp = ws.ws2811_get_return_t_str(resp)
            raise RuntimeError('ws2811_render failed with code '
                               '{} ({})'.format(resp, str_resp))

    def __getitem__(self, idx):
        return ws.ws2811_led_get(self._channel, idx)

    def __setitem__(self, idx, rgbw):
        r, g, b, w = rgbw
        value = (Gamma.W[w] << 24) | (Gamma.G[g] << 16) | (Gamma.R[r] << 8) | Gamma.B[b]
        if not isinstance(idx, slice):
            idx = slice(idx, idx + 1, 1)
        for idx in range(idx.start or 0, idx.stop or len(self), idx.step or 1):
            ws.ws2811_led_set(self._channel, idx, value)


class Light(threading.Thread):
    '''A light contains an RGB LED globe, a display, and 6 buttons.'''

    class Mode:
        RGBW = 0
        WALK = 1
        NIGHTLIGHT = 2

        NUM_MODES = 3

    def __init__(self):
        super().__init__()

        self._lock = threading.Lock()

        self._on = True
        self._mode = Light.Mode.RGBW
        self._color = [0, 0, 0, 255]
        self._target = None
        self._time_override = None

        self._displayed_at = 0
        self._display = LCD(pin=14, address=0x3d)
        self._display.start()

        self._globe = Pixels(size=13, pin=18, brightness=255)
        self._globe.start()

        self._buttons = []
        def button(pin, callback):
            b = gpiozero.Button(pin)
            b.when_pressed = callback
            self._buttons.append(b)
        button(20, self._on_power_pressed)
        button(19, self._on_mode_pressed)
        for channel, pin in enumerate((21, 13, 16, 26)):
            button(pin, self._on_color_pressed(channel))

    def __str__(self):
        return 'Light<on={}, mode={}, time={}, color={}, target={}>'.format(
                self.on, self.mode, self.time, self.hexcolor, self.hextarget)

    @property
    def on(self):
        with self._lock:
            return self._on

    @on.setter
    def on(self, on):
        with self._lock:
            self._on = on

    @property
    def mode(self):
        with self._lock:
            return self._mode

    @mode.setter
    def mode(self, mode):
        with self._lock:
            self._mode = mode

    @property
    def color(self):
        with self._lock:
            return self._color

    @color.setter
    def color(self, color):
        with self._lock:
            self._color = color

    @property
    def hexcolor(self):
        return ('{:02x}' * 4).format(*self.color)

    @property
    def target(self):
        with self._lock:
            return self._target

    @target.setter
    def target(self, color):
        with self._lock:
            self._target = color

    @property
    def hextarget(self):
        return ('{:02x}' * 4).format(*self.target) if self.target else ''

    @property
    def time(self):
        with self._lock:
            return self._time_override or arrow.now()

    @time.setter
    def time(self, override):
        with self._lock:
            self._time_override = override

    @property
    def is_night(self):
        #return False
        return not 7 <= self.time.hour <= 18

    def show(self):
        #print(self)

        if not self.on:
            self._globe.show((0, 0, 0, 0))
            self._display.show()
            return

        if self.mode == Light.Mode.NIGHTLIGHT:
            self._globe.show((40, 20, 0, 0))
            self._display.show()
            return

        self._globe.show(self.color)

        now = time.time()
        if self.mode != Light.Mode.WALK or now - self._displayed_at > 1:
            r, g, b, w = self.color
            text = ('{:x}' * 4).format(r // 16, g // 16, b // 16, w // 16)
            self._display.text(text, (10, 10), 1)
            self._display.show()
            self._displayed_at = now

    def run(self):
        self.show()
        while True:
            try:
                if self.is_night and self.mode != Light.Mode.NIGHTLIGHT:
                    self.mode = Light.Mode.NIGHTLIGHT
                    self.show()
                if self.on and self.mode == Light.Mode.WALK:
                    self._walk_step_color()
                time.sleep(1)
            except KeyboardInterrupt:
                self._display.join()
                break

    def _walk_step_color(self):
        for j, (cchan, tchan) in enumerate(zip(self.color, self.target)):
            if cchan < tchan:
                self.color[j] += 1
            if cchan > tchan:
                self.color[j] -= 1
        if self.color == self.target:
            self.target = random_rgb()
        self.show()

    def _on_power_pressed(self):
        self.on = not self.on
        self.show()

    def _on_mode_pressed(self):
        if not self.on or self.is_night:
            return
        self.mode = (self.mode + 1) % Light.Mode.NUM_MODES
        if self.mode == Light.Mode.WALK:
            self.color = random_rgb()
            self.target = random_rgb()
        else:
            self.target = None
        self.show()
        print(self)

    def _on_color_pressed(self, idx):
        def increment_color():
            if self.on and self.mode == Light.Mode.RGBW:
                value = self.color[idx]
                value = (value - (value % 16) + 16) % 256
                self.color[idx] = value
                self.show()
                print(self)
        return increment_color


app = flask.Flask(__name__)
light = Light()

@app.route('/')
def index():
    return render_template('index.html', light=light)

@app.route('/', methods=['POST'])
def set():
    #print(sorted(request.form.items()))
    time = request.form.get('time')
    if time is not None:
        light.time = arrow.get(time) if time else None
    color = request.form.get('color')
    if color is not None:
        light.color = [int(c) for c in color.split(',')]
    target = request.form.get('target')
    if target is not None:
        light.target = [int(c) for c in target.split(',')]
    light.show()
    return render_template('index.html', light=light)


if __name__ == '__main__':
    light.start()
    app.run(host='0.0.0.0', port=80, debug=False, threaded=False, use_reloader=False)
