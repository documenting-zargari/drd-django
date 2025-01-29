

from data.models import Category, Phrase, Sample, Source


class DBRouter(object):
    
    data_classes = [Sample, Category, Source, Phrase]

    def db_for_read(self, model, **hints):
        if model in self.data_classes:
            return 'data'
        return None

    def db_for_write(self, model, **hints):
        if model in self.data_classes:
            return 'data'
        return None