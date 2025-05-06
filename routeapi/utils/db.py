from django.conf import settings
from pymongo import MongoClient
from pymongo.errors import (
    ConnectionFailure,
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
    AutoReconnect,
)
import logging

logger = logging.getLogger(__name__)
_client = None
_db = None

def get_mongo_connection():
    global _client, _db
    if _db is not None:
        return _db
    try:
        _client=MongoClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS= settings.MONGO_CONNECTION_TIMEOUT_MS
        )
        _client.server_info()
        _db = _client[settings.MONGO_DB_NAME]
        logger.info('connected successfully')
        return _db
    except ServerSelectionTimeoutError as e:
        logger.error(f"mongodb unavailable: {e}")
        _close_connection()
        raise ConnectionError("database timeout") from e
    except (ConnectionFailure, AutoReconnect) as e:
        logger.error(f"connection failed: {e}")
        _close_connection()
        raise ConnectionError('connections failed') from e
    except OperationFailure as e:
        logger.error(f"operation error: {e}")
        _close_connection()
        raise PermissionError("operation failed") from e
    except ConfigurationError as e:
        logger.error(f"invalid configuration: {e}")
        _close_connection()
        raise ValueError("invalid configuration error") from e
    except Exception as e:
        logger.critical(f"database error: {e}", exc_info=True)
        _close_connection()
        raise

def _close_connection():
        global _client, _db
        if _client:
            try:
                _client.close()
            except Exception as e:
                logger.warning(f"error closing connection: {e}")
            _client =None
            _db=None