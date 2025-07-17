from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response


class ArangoModelViewSet(viewsets.ViewSet):
    """
    A custom viewset for ArangoDB–backed models.
    Subclasses should set the 'model' and 'serializer_class' attributes.
    """

    serializer_class = None  # Must be set in subclass.
    model = None  # Must be set in subclass.

    def get_queryset(self):
        # Return a list of all objects.
        return self.model.all()

    def get_object(self, pk):
        # Smart lookup: try _key first (efficient), fallback to id field (backward compatible)
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        
        # Try _key first (most efficient)
        doc = collection.get(pk)
        if doc:
            return doc
        
        # Fallback to id field search (less efficient but backward compatible)
        if isinstance(pk, str) and pk.isdigit():
            pk = int(pk)
        cursor = collection.find({"id": pk}, limit=1)
        docs = list(cursor)
        if docs:
            return docs[0]
        
        raise NotFound(detail="Object not found")

    def list(self, request):
        queryset = self.get_queryset()
        serializer = self.serializer_class(
            queryset, many=True, context={"request": request, "view": self}
        )
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        instance = self.get_object(pk)
        serializer = self.serializer_class(
            instance, context={"request": request, "view": self}
        )
        return Response(serializer.data)

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(
                self.serializer_class(instance).data, status=status.HTTP_201_CREATED
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
