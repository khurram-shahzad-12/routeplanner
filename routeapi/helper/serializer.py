import json
from bson import ObjectId
from datetime import datetime

def json_serialize(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: json_serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [json_serialize(item) for item in obj]
    elif isinstance(obj,datetime):
        return obj.strftime('%Y-%m-%d')
    return obj


# json_data = json.dumps(customers, default=json_serialize, indent=4)
# print(json_data)