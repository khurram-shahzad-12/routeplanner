from django.shortcuts import render
from django.http import JsonResponse
from bson import ObjectId
from bson.json_util import dumps,loads
from django.views.decorators.csrf import csrf_exempt
from .vrp_service import VRPSolver
import json
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
def get_vpr_solutions(request):
        if request.method == 'POST': 
            try:
                  data = json.loads(request.body)
                  required_fields = ['invoice_date','miles', 'maxOrders', 'routeLength',]
                  missing_fields = [field for field in required_fields if field not in data]

                  if missing_fields:
                        return JsonResponse({"error":"missing required fields"}, status=400)
                  invoice_date = data.get('invoice_date') 
                  mile_range=data.get('miles')
                  max_orders=data.get('maxOrders')
                  route_length =data.get('routeLength')
                  solver = VRPSolver(invoice_date=invoice_date, mile_range=mile_range,max_orders=max_orders, route_length=route_length)
                  order_reports = solver.generate_routing_solutions()               
                  return JsonResponse({"message": order_reports}, safe=False)          
            except json.JSONDecodeError:
                  return JsonResponse({"error":"Invalid JOSN body"}, status=400)
            except ValueError as ve:
                  return JsonResponse({'error':str(ve)}, status=400)
            except Exception as e:
                  logger.exception("unexpected error occured in vrp_solver")
                  return JsonResponse({"error":"unexpected error"}, status=500)
        else:
              return JsonResponse({"error":"invalid request method"}, status=405)
                                 
            