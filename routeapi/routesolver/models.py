from django.db import models
from routeapi.utils.db import get_mongo_connection
db = get_mongo_connection()

routesolver_collection = db['routesolver']
orders_collection = db['invoices']
customer_collection = db['customers']
vehicle_collection = db['vehicleNames']