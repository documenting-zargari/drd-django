import logging
from unittest.mock import MagicMock, patch

from django.http import JsonResponse
from django.test import RequestFactory, TestCase

from roma.middleware.arangodb_middleware import ArangoDBMiddleware

# Suppress logging during tests
logging.disable(logging.CRITICAL)


class ArangoDBMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.get_response_mock = MagicMock(return_value=JsonResponse({"status": "ok"}))

    @patch("roma.middleware.arangodb_middleware.ArangoClient")
    @patch("roma.middleware.arangodb_middleware.settings")
    def test_successful_connection(self, mock_settings, mock_arango_client):
        # Configure mock settings
        mock_settings.ARANGO_DB_NAME = "test_db"
        mock_settings.ARANGO_USERNAME = "test_user"
        mock_settings.ARANGO_PASSWORD = "test_pass"
        mock_settings.ARANGO_HOST = "http://localhost:8529"

        # Mock the ArangoDB client and connection
        mock_db = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.db.return_value = mock_db
        mock_arango_client.return_value = mock_client_instance

        # Initialize the middleware with successful connection
        middleware = ArangoDBMiddleware(self.get_response_mock)

        # Verify connection was made
        self.assertEqual(middleware.db, mock_db)
        self.assertIsNone(middleware.connection_error)

        # Test middleware processing
        request = self.factory.get("/")
        response = middleware(request)

        # Assert that the DB was attached to the request
        self.assertEqual(request.arangodb, mock_db)
        self.assertEqual(request.arango_error, None)

        # Assert that get_response was called
        self.get_response_mock.assert_called_once_with(request)

        # Verify response
        self.assertEqual(response.status_code, 200)

    @patch("roma.middleware.arangodb_middleware.ArangoClient")
    @patch("roma.middleware.arangodb_middleware.settings")
    def test_connection_error(self, mock_settings, mock_arango_client):
        # Configure mock settings
        mock_settings.ARANGO_DB_NAME = "test_db"
        mock_settings.ARANGO_USERNAME = "test_user"
        mock_settings.ARANGO_PASSWORD = "test_pass"
        mock_settings.ARANGO_HOST = "http://localhost:8529"

        # Mock ArangoDB client to raise an exception
        mock_client_instance = MagicMock()
        mock_client_instance.db.side_effect = Exception("Connection failed")
        mock_arango_client.return_value = mock_client_instance

        # Initialize middleware with failed connection
        middleware = ArangoDBMiddleware(self.get_response_mock)

        # Verify connection error was captured
        self.assertIsNone(middleware.db)
        self.assertIsNotNone(middleware.connection_error)

        # Test middleware processing
        request = self.factory.get("/")
        response = middleware(request)

        # The middleware no longer returns an error response directly
        # It now attaches the connection status to the request and continues
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(request.arangodb)
        self.assertIsNotNone(request.arango_error)
