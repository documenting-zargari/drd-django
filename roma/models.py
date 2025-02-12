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
    def get_db(cls):
        """ Connect to the Arango database """
        client = ArangoClient()
        return client.db(
            settings.ARANGO_DB_NAME,
            username=settings.ARANGO_USERNAME,
            password=settings.ARANGO_PASSWORD,
        )
    
    @classmethod
    def get_collection(cls):
        """ Get the collection for the model """
        db = cls.get_db()
        return db.collection(cls.collection_name)
    
    def save(self):
        """ Save the model to the database """
        collection = self.get_collection()
        data = self.__dict__.copy()
        data.pop('_id', None)
        data.pop('_rev', None)
        if self._key:
            collection.update(data)
        else:
            result = collection.insert(data)
            self._key = result['_key']
    
    @classmethod
    def get(cls, key):
        """ Get a single document from the collection by key """
        collection = cls.get_collection()
        document = collection.get(key)
        return cls(**document) if document else None
    
    @classmethod
    def all(cls):
        """ Get all documents from the collection """
        collection = cls.get_collection()
        for document in collection.all():
            yield cls(**document)
    
    @classmethod
    def filter(cls, **kwargs):
        """ Filter documents in the collection """
        collection = cls.get_collection()
        query = "FOR doc IN {} FILTER".format(cls.collection_name)
        conditions = [" doc.{} == @{}".format(k, k) for k in kwargs]
        query += " AND".join(conditions) + " RETURN doc"

        db = cls._get_db()
        cursor = db.aql.execute(query, bind_vars=kwargs)
        return [cls(**doc) for doc in cursor]
    
    def delete(self):
        """Delete a document."""
        if not self._key:
            raise ValueError("Cannot delete a document without a _key.")
        collection = self._get_collection()
        collection.delete(self._key)
