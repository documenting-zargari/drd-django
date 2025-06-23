from arango import ArangoClient
from django.conf import settings

class ArangoModel:
    """ Base class for ArangoDB models """

    collection_name = None

    def __init__(self, **kwargs):
        self._id = kwargs.get('_id')
        self._key = kwargs.get('_key')
        self._rev = kwargs.get('_rev')
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    @classmethod
    def db(cls):
        """ Connect to the Arango database """
        client = ArangoClient(hosts=settings.ARANGO_HOST)
        return client.db(
            settings.ARANGO_DB_NAME,
            username=settings.ARANGO_USERNAME,
            password=settings.ARANGO_PASSWORD,
        )
    
    @classmethod
    def collection(cls):
        """ Get the collection for the model """
        if cls.collection_name is None:
            raise ValueError("collection_name must be defined")
        return cls.db().collection(cls.collection_name)
    
    def save(self):
        data = self.to_dict()
        if self._key:
            # If the document already exists, update it.
            result = self.collection().update(data)
        else:
            # Insert a new document.
            result = self.collection().insert(data)
            self._key = result['_key']
        return result
    
    @classmethod
    def get(cls, key):
        # Returns a single document by _key.
        doc = cls.collection().get(key)
        return cls(**doc) if doc else None
    
    @classmethod
    def get_by_field(cls, field_name, value):
        # Returns a single document by any field.
        query = f"FOR doc IN {cls.collection_name} FILTER doc.{field_name} == @value RETURN doc"
        cursor = cls.db().aql.execute(query, bind_vars={'value': value})
        docs = list(cursor)
        return cls(**docs[0]) if docs else None
    
    @classmethod
    def all(cls):
        # Returns a list of all documents in the collection.
        query = f"FOR doc IN {cls.collection_name} RETURN doc"
        cursor = cls.db().aql.execute(query)
        return [cls(**doc) for doc in cursor]

    def delete(self):
        if not self._key:
            raise Exception("Cannot delete an unsaved instance (missing _key)")
        return self.collection().delete(self._key)

    def to_dict(self):
            # Convert the instance’s data to a dictionary.
            data = {}
            for key, value in self.__dict__.items():
                # Optionally filter out any non–document fields.
                data[key] = value
            return data