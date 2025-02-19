from arango import ArangoClient
from django.conf import settings
import os

class ArangoDBMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.client = ArangoClient(hosts=os.getenv('ARANGO_HOST', 'http://localhost:8529'))
        
        # Connect to ArangoDB
        self.db = self._connect_to_arangodb()

    def _connect_to_arangodb(self):
        """Establish a connection to ArangoDB"""
        connection = self.client.db(
            settings.ARANGO_DB_NAME,
            username=settings.ARANGO_USERNAME,
            password=settings.ARANGO_PASSWORD,
        )
        return connection

    def __call__(self, request):
        """Attach ArangoDB connection to request"""
        request.arangodb = self.db
        response = self.get_response(request)
        return response