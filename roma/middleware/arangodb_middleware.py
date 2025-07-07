import logging
import os

from arango import ArangoClient
from arango.exceptions import ArangoError
from arango.http import DefaultHTTPClient
from django.conf import settings

logger = logging.getLogger(__name__)


class ArangoDBMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.connection_error = None

        # Initialize client and attempt connection
        try:
            self.client = ArangoClient(
                http_client=DefaultHTTPClient(
                    retry_attempts=1,
                ),
                hosts=os.getenv("ARANGO_HOST", settings.ARANGO_HOST),
                request_timeout=5,
            )
            self.db = self._connect_to_arangodb()
        except Exception as e:
            logger.error(f"ArangoDB initialization error: {str(e)}")
            self.db = None
            self.connection_error = str(e)

    def _connect_to_arangodb(self):
        """Establish a connection to ArangoDB"""
        try:
            # Check if required settings are defined
            if (
                not hasattr(settings, "ARANGO_DB_NAME")
                or not hasattr(settings, "ARANGO_USERNAME")
                or not hasattr(settings, "ARANGO_PASSWORD")
            ):
                raise ValueError("ArangoDB settings are not properly configured")

            connection = self.client.db(
                settings.ARANGO_DB_NAME,
                username=settings.ARANGO_USERNAME,
                password=settings.ARANGO_PASSWORD,
            )
            return connection
        except ArangoError as e:
            logger.error(f"ArangoDB connection error: {str(e)}")
            self.connection_error = str(e)
            return None
        except Exception as e:
            logger.error(f"Unexpected error connecting to ArangoDB: {str(e)}")
            self.connection_error = str(e)
            return None

    def __call__(self, request):
        """Attach ArangoDB connection to request"""
        # Attach the database connection (even if it's None)
        request.arangodb = self.db
        request.arango_error = self.connection_error

        # Always proceed with the request, even if ArangoDB connection failed
        # Individual views can check request.arangodb and handle failures appropriately
        response = self.get_response(request)
        return response
