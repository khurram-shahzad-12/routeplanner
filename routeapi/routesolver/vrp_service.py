import googlemaps
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
from datetime import datetime, timedelta
from .models import routesolver_collection, orders_collection, customer_collection, vehicle_collection
from ..helper.serializer import json_serialize
from decouple import config
from bson import ObjectId
import requests
import json


class VRPSolver:
    def __init__(self, invoice_date, mile_range,max_orders,route_length,service_time ):
        clean_date = invoice_date.split('T')[0]
        self.start_day = datetime.strptime(clean_date, "%Y-%m-%d")
        self.end_day = self.start_day + timedelta(days=1)
        # self.gmaps = googlemaps.Client(key=config('GOOGLE_MAPS_API_KEY'))
        self.invoice_date = invoice_date
        self.mile_range = int(mile_range)
        self.max_orders = int(max_orders)
        self.route_length = int(route_length)
        self.SERVICE_TIME = int(int(service_time)*60)

    def seconds_to_time(self,seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600)//60
        return f"{hours: 02d}:{minutes:02d}"
    
    def format_travel_time(self,seconds):
        return f"{seconds//60}min" if seconds < 3600 else f"{seconds//3600}h {(seconds % 3600)//60}m"
        
    def get_distance_matrix(self, locations):
        distance_matrix = []
        time_matrix = []
        osrm_url = "http://localhost:6000/table/v1/driving/"

        coordinates =';'.join([f"{lon},{lat}" for lat, lon in locations])
        url = f"{osrm_url}{coordinates}?annotations=distance,duration"
        result = requests.get(url)
        if result.status_code != 200:
            raise ValueError(f"failed to get ditance: {result.status_code}")
        data = result.json()
        if "distances" not in data or "durations" not in data:
            raise ValueError("Missing distance/duration from osrm response")
        distance_matrix = [
            [int(cell) if cell is not None else 0 for cell in row]
            for row in data["distances"]
        ]
        time_matrix = [
            [int(cell) if cell is not None else 0 for cell in row]
            for row in data["durations"]
        ]
        return distance_matrix, time_matrix

    def get_orders_for_routing(self):
        orders = list(orders_collection.find({'invoice_date':{'$gte':self.start_day,'$lt':self.end_day}},{'_id': 1, 'ot_date': 1, 'delivery_status': 1, 'items.weight_kg': 1,'customer':1, 'priority_value': 1}))
        if not orders:
            raise ValueError("no order found for the invoice date")
        customer_ids = [order['customer'] for order in orders if 'customer' in order]
        customers = {str(cust['_id']): cust
                     for cust in customer_collection.find({'_id':{'$in':customer_ids}})}                         
        vehicles = list(vehicle_collection.find({'availability':'available'}))        
        if not vehicles:
            raise ValueError("vehicles are not available for orders")
        for veh in vehicles:
            if 'capacity' not in veh or veh['capacity'] is None:
                raise ValueError(f"vehicle {veh.get('name')} is missing capacity information")
            
        vehicle_details = [{
            '_id': str(veh['_id']),
            'name': veh['name'],
            'capacity': int(veh['capacity']),
            'current_location': '55.84869, -4.21531',
            'status': veh['status'],            
        } for veh in vehicles]
       
        depot_location_str = vehicle_details[0]['current_location']
        depot_location = tuple(map(float, depot_location_str.split(',')))
        locations = [depot_location]
        demand = [0]
        customer_id_to_index = {}
        vehicle_capacities = [veh['capacity'] for veh in vehicle_details]
        time_windows = [(0,86400)]
        priority_weight = []
       
        for i,order in enumerate(orders):
            total_weight_kg = sum(
                int(item.get('weight_kg', 1))
                for item in order.get('items',[])
            )
            customer_data = customers.get(str(order['customer']), {})
            if not customer_data:
                raise ValueError(f"customer not found for order {order["_id"]}")
            current_customer = customer_data.get(str('customer_name'))

            latitude = customer_data.get('latitude')
            longitude = customer_data.get('longitude')         
            if latitude is None or longitude is None:
                raise ValueError(f"missing map location for {customer_data['customer_name']}")
            location_str = (float(latitude),float(longitude))
            locations.append(location_str)
            latitude_float = float(latitude)
            longitude_float = float(longitude)
            if not (49.9 <= latitude_float <= 60.9 and -8.6 <= longitude_float <= 1.8):
                raise ValueError(f"{current_customer} location could not find in map")   
            demand.append(total_weight_kg)
            customer_id_to_index[customer_data['_id']]=i+1
            start_time_str = customer_data.get('business_start_hour')
            end_time_str = customer_data.get('business_close_hour')
            if start_time_str:
                start_time = datetime.strptime(start_time_str,"%H:%M")
            else:
                start_time = datetime.strptime("00:00","%H:%M")
            if end_time_str:
                end_time = datetime.strptime(end_time_str,"%H:%M")
            else:
                end_time = datetime.strptime("23:59","%H:%M")
            second_start = (start_time-datetime.strptime("00:00","%H:%M")).seconds
            second_end = (end_time-datetime.strptime("00:00","%H:%M")).seconds
            if second_end < second_start:
                second_end += 86000
            time_windows.append((second_start,second_end))
            priority_weight.append(order.get('priority_value'))
        
        distance_matrix, time_matrix = self.get_distance_matrix(locations)
        return {
            'depot_index': 0,
            'distance_matrix': distance_matrix,
            'time_matrix': time_matrix,
            'vehicle_capacities': vehicle_capacities,
            'demand': demand,
            'locations': locations,
            'num_vehicles': len(vehicles),
            'customer_id_to_index': customer_id_to_index,
            'vehicle_details': vehicle_details,
            'orders':orders,
            'customers':customers,
            'time_windows': time_windows,
            'priority_weight': priority_weight,
        }
    
    def solve_vrp(self, depot_index, distance_matrix, vehicle_capacities, demands, num_vehicles, time_windows, time_matrix, priority_weight):
        num_nodes = len(distance_matrix)
        assert num_nodes > 0, "Distance matrix is empty"
        for row in distance_matrix:
            assert len(row) == num_nodes, "Distance matrix is not square"
        manager = pywrapcp.RoutingIndexManager(len(distance_matrix), num_vehicles, depot_index)
        routing = pywrapcp.RoutingModel(manager)
       
        def distance_callback(from_index, to_index):
            try:
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                return distance_matrix[from_node][to_node]
            except OverflowError as oe:
                print(f"overflow eror in index conversion: {oe}")
                raise ValueError("invalid index type")
            except IndexError as ie:
                print(f"index error: {ie}")
                raise ValueError('reset cusotmer address on map')
    
        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        def demand_callback(from_index):
            from_node = manager.IndexToNode(from_index)
            return demands[from_node]
        
        demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_callback_index,
            0,
            vehicle_capacities,
            True,
            'Capacity'
        )

        def count_callback(from_index):
            from_node = manager.IndexToNode(from_index)
            return 0 if from_node == depot_index else 1
        
        count_callback_index = routing.RegisterUnaryTransitCallback(count_callback)
        routing.AddDimensionWithVehicleCapacity(
            count_callback_index,
            0,
            [self.max_orders] * num_vehicles,
            True,
            'OrderCount',
        )
        routing.AddDimension(
            transit_callback_index,
            0,
            self.mile_range*1600,
            True,
            'Distance',
        )
        SERVICE_TIME = self.SERVICE_TIME
        def time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            service_time = SERVICE_TIME if from_node != depot_index else 0
            return time_matrix[from_node][to_node] + service_time
        
        time_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.AddDimension(
            time_callback_index,
            20*60, 
            24*3600, 
            False,
            'Time',
        )
        time_dimension = routing.GetDimensionOrDie('Time')
        for i,time_window in enumerate(time_windows):
            if i == depot_index:
                continue
            index = manager.NodeToIndex(i)
            time_dimension.CumulVar(index).SetRange(time_window[0], time_window[1])

        max_route_duration = self.route_length * 3600 
        for vehicle_id in range(num_vehicles):
            start_idx = routing.Start(vehicle_id)
            end_idx = routing.End(vehicle_id)
            solver = routing.solver()
            route_duration = time_dimension.CumulVar(end_idx)-time_dimension.CumulVar(start_idx)
            solver.Add(route_duration <= max_route_duration)
            
        
        for node in range(1, len(distance_matrix)):           
            raw_priority = priority_weight[node-1] if(node-1) < len(priority_weight) else 0
            try: 
                priority = int(raw_priority)
            except (TypeError, ValueError):
                priority = 10
            if priority >= 1000:
                penalty = 10000000000000000
            elif priority >= 100:
                penalty = 1000000000000000
            else:
                penalty = 100000000000000          
            routing.AddDisjunction([manager.NodeToIndex(node)], penalty)
        
        search_parameters = pywrapcp.DefaultRoutingSearchParameters() 
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.seconds = 50
        search_parameters.lns_time_limit.seconds = 30    
        solution = routing.SolveWithParameters(search_parameters)
       
        solution_data = {
            'routes': [],
            'total_distance':0
        }
        if solution:
            solution_data['total_distance'] = solution.ObjectiveValue()
            time_dimension = routing.GetDimensionOrDie('Time')
        
            for vehicle_id in range(num_vehicles):
                index = routing.Start(vehicle_id)
                route = []
                route_distance = 0
                route_details = []
                previous_node = None
                start_time = solution.Min(time_dimension.CumulVar(routing.Start(vehicle_id)))
                route_details.append({
                    'node': manager.IndexToNode(index),
                    'type': 'depot',
                    'departure_time': start_time,
                    'arrival_time': 0,
                    'travel_time': 0,
                    'distance': 0
                })
              
                while not routing.IsEnd(index):
                    node = manager.IndexToNode(index)
                    route.append(node)
                    previous_index = index
                    next_index = solution.Value(routing.NextVar(index))
                    next_node =manager.IndexToNode(next_index)
                    route_distance += routing.GetArcCostForVehicle(index, next_index, vehicle_id)
                    arrival_time = solution.Min(time_dimension.CumulVar(next_index))
                    travel_time = time_matrix[node][next_node]
                    distance = distance_matrix[node][next_node]

                    if next_node != depot_index:
                        actual_arrival = arrival_time - SERVICE_TIME
                    else:
                        actual_arrival = arrival_time
                    
                    route_details.append({
                        'node': next_node,
                        'type': 'depot' if next_node == depot_index else 'customer',
                        'arrival_time': actual_arrival,
                        'departure_time': arrival_time,
                        'travel_time': travel_time,
                        'distance': distance,
                    })
                    index = next_index
                
                route.append(manager.IndexToNode(index))
                solution_data['routes'].append({
                    'vehicle_id': vehicle_id,
                    'route': route,
                    'route_detail': route_details,
                    'distance': route_distance
                })
        return solution_data
    
    def generate_routing_solutions(self):
        vrp_data = self.get_orders_for_routing()
        solution = self.solve_vrp(
            vrp_data['depot_index'],
            vrp_data['distance_matrix'],
            vrp_data['vehicle_capacities'],
            vrp_data['demand'],
            vrp_data['num_vehicles'],
            vrp_data['time_windows'],
            vrp_data['time_matrix'],
            vrp_data['priority_weight']
        )   
        mapped_solution = {
            "solution_id" : f"SOL_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'date' : self.start_day,
            'total_distance' : round(solution['total_distance']/1600,2),
            'vehicle_routes': []
        }     
        for route in solution['routes']:
            if len(route['route_detail']) <= 2:
                continue 
            vehicle_id = route['vehicle_id']
            vehicle = vrp_data['vehicle_details'][vehicle_id]
            route_details = {
                'vehicle_id':ObjectId(vehicle['_id']),
                'stops':[],
                'distance_veh_km': round(route['distance']/1600, 2)
            }       
            for i,stop in enumerate(route['route_detail']):
                stop_index = stop['node']
              
                if stop_index == vrp_data['depot_index']:
                    is_final_depot = (i == len(route['route_detail'])-1)

                    if is_final_depot:
                        adjusted_arrival = stop['arrival_time'] - self.SERVICE_TIME
                        route_details['stops'].append({
                        'type':'depot',
                        'location':vrp_data['locations'][stop_index],
                        'address': "Depot Location",
                        'departure_time': self.seconds_to_time(stop['departure_time']),
                        'arrival_time': self.seconds_to_time(adjusted_arrival),
                        'travel_time': self.format_travel_time(stop['travel_time']),
                        'distance': round(stop['distance']/1600,2),
                    })
                    else:                        
                        route_details['stops'].append({
                        'type':'depot',
                        'location':vrp_data['locations'][stop_index],
                        'address': "Depot Location",
                        'departure_time': self.seconds_to_time(stop['departure_time']),
                        'arrival_time': self.seconds_to_time(stop['arrival_time']),
                        'travel_time': self.format_travel_time(stop['travel_time']),
                        'distance': round(stop['distance']/1600,2),
                    })
                else:
                    order_index = stop_index - 1  
                    order = vrp_data['orders'][order_index]
                    customer_id =str(order['customer'])
                    customer_array = vrp_data['customers']
                    customer = customer_array[customer_id]
                    if(customer):
                        latitude= customer.get('latitude')
                        longitude = customer.get('longitude')
                        location_str = f"{latitude},{longitude}"
                        route_details['stops'].append({
                            'type': 'delivery',
                            'order_id': order['_id'],
                            'customer_id': customer['_id'],
                            'customer_name': customer['customer_name'],
                            'address': customer['address'],
                            'location': location_str, 
                            'arrival_time': self.seconds_to_time(stop['arrival_time']),
                            'travel_time': self.format_travel_time(stop['travel_time']),
                            'distance': round(stop['distance']/1600,2),
                            'departure_time': self.seconds_to_time(stop['departure_time'])                                                                         
                        })
            mapped_solution['vehicle_routes'].append(route_details)       
        vehicle_collection.update_many({}, {'$set':{'status': 'unassigned'}})
        for i,veh in enumerate(mapped_solution['vehicle_routes']):                                            
            vehicle_collection.update_one({
                '_id': ObjectId(veh['vehicle_id'])
            }, {'$set': {'status': 'assigned'}})                 
        routesolver_collection.insert_one(mapped_solution)            
        return []
        

