from rest_framework import serializers, mixins
from django.contrib.auth.models import User

class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'username', ]

    name = serializers.SerializerMethodField('full_name')
    def full_name(self, obj):
        return obj.first_name + ' ' + obj.last_name
