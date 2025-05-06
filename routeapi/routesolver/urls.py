from django.urls import path
from .views import get_vpr_solutions

urlpatterns = [
    path('getallroutesolutions/', get_vpr_solutions)
]
