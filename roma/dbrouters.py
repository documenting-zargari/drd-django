

from data.models import Category, Sample


class DBRouter(object):
    def db_for_read(self, model, **hints):
        if model == Sample or model == Category:
            return 'data'
        return None

    def db_for_write(self, model, **hints):
        if model == Sample or model == Category:
            return 'data'
        return None