from django.contrib.auth.models import User
from rest_framework import viewsets

from user.serializers import UserSerializer


# Create your views here.
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("pk")
    serializer_class = UserSerializer
