from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.exceptions import NotFound

class ArangoModelViewSet(viewsets.ViewSet):
    """
    A custom viewset for ArangoDB–backed models.
    Subclasses should set the 'model' and 'serializer_class' attributes.
    """
    serializer_class = None  # Must be set in subclass.
    model = None             # Must be set in subclass.

    def get_queryset(self):
        # Return a list of all objects.
        return self.model.all()

    def get_object(self, pk):
        # Retrieve a single object by its _key.
        instance = self.model.get(pk)
        if not instance:
            raise NotFound(detail="Object not found")
        return instance

    def list(self, request):
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request, 'view': self})
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        instance = self.get_object(pk)
        serializer = self.serializer_class(instance, context={'request': request, 'view': self})
        return Response(serializer.data)

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(
                self.serializer_class(instance).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, pk=None):
        instance = self.get_object(pk)
        serializer = self.serializer_class(instance, data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(self.serializer_class(instance).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None):
        instance = self.get_object(pk)
        serializer = self.serializer_class(instance, data=request.data, partial=True)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(self.serializer_class(instance).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        instance = self.get_object(pk)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
