import datetime
import enum
import functools


def profile(f):
    '''Profile a function's elapsed time.'''
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        start = datetime.datetime.now()
        f(*args, **kwargs)
        elapsed = (datetime.datetime.now() - start).total_seconds()
        logging.debug('{} took {:.1f}ms'.format(f.__name__, 1000 * elapsed))
    return wrapper


@enum.unique
class Mode(enum.Enum):
    MANAGED = 0
    RGBW = 1
    LAVA = 2
    FIREWORKS = 3
