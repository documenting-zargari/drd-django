from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser
from django.contrib.auth.models import User

from user.serializers import UserSerializer

# Create your views here.
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('pk')
    serializer_class = UserSerializer

class CategoriesViewSet(viewsets.GenericViewSet):
    queryset = User.objects.all().order_by('pk')