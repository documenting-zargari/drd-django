

from data.models import Category, Sample, Source


class DBRouter(object):
    def db_for_read(self, model, **hints):
        if model in [Sample, Category, Source]:
            return 'data'
        return None

    def db_for_write(self, model, **hints):
        if model in [Sample, Category, Source]:
            return 'data'
        return None